"""
Cuenta corriente de Kia (Gettel) y Toyota (pedido del usuario, 2026-10-07).

Cómo funciona (confirmado con el manager y con los Excel de Cierre desde
mayo 2024):
- Los autos de las concesionarias cargan nafta con un vale y no pagan en el
  momento: el POS lo registra como Local Account (Store Info). Toyota empezó
  el 24/10/2025; antes era solo Kia.
- Lo que se les cobra por cada período es (vales − $0.02 por galón de
  rebate) × 1.03 (el 3% del recargo de la tarjeta): REBATE_PER_GALLON y
  CARD_CHARGE, las mismas fórmulas de las hojas "Pendiente" del Excel.
- Cada una o dos semanas pagan lo acumulado con una American Express, en
  cobros de hasta $999; el POS lo registra como el departamento LOCAL ACCT
  (la columna VS del Excel) y lo deposita J.H.

Cálculo puro (sin UI ni base) más la lectura de las hojas de Gettel-Toyota
de los Excel de Cierre (o de los Excel de control de Gettel), que es de
donde sale el historial: cargas por día y por empresa (con galones) y los
pagos (con la empresa entre paréntesis desde febrero 2026).
"""

import calendar
import os
import re
from datetime import date, datetime, timedelta

KIA = "Kia"
TOYOTA = "Toyota"
COMPANIES = (KIA, TOYOTA)
TOYOTA_START = "2025-10-24"  # primer vale de Toyota: antes, todo pago es de Kia
# Cada una paga con su propia tarjeta (el manager, 2026-10-07) y el recibo
# trae los últimos 4 dígitos: así se asignaron los pagos sin nombre de
# octubre 2025 a febrero 2026 (Toyota ya pagaba con la 0953 desde noviembre
# 2025). La Amex 1008 (16/02/2026) no se sabe de quién es.
KNOWN_CARDS = {"3924": KIA, "5145": KIA, "5449": KIA, "0953": TOYOTA, "1363": TOYOTA, "9451": TOYOTA}
# Un pago anulado (el recibo dice VOID y el POS no lo cobró) no cuenta.
VOIDED = "Anulado"
REBATE_PER_GALLON = 0.02
CARD_CHARGE = 0.03
TOLERANCE = 0.005
# Un pago que se atrasa más que esto se avisa (pagan cada una o dos semanas).
LATE_PAYMENT_DAYS = 21


def to_charge(amount, gallons):
    """Lo que se cobra por unas cargas: (vales − rebate por galón) × 1.03."""
    return round(((amount or 0.0) - REBATE_PER_GALLON * (gallons or 0.0)) * (1 + CARD_CHARGE), 2)


# ---------------------------------------------------------------------------
# Lectura de los Excel (hojas de Gettel-Toyota)
# ---------------------------------------------------------------------------

def _norm(value):
    return str(value).strip().upper() if value is not None else ""


def _num(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _fix_date(day, file_month):
    """Una fecha tipeada al revés (12/01 en vez de 01/12) se da vuelta si así cae cerca del mes del archivo."""
    if file_month is None or day.day > 12:
        return day
    lo = date(file_month[0], file_month[1], 1) - timedelta(days=40)
    hi = date(file_month[0], file_month[1], 1) + timedelta(days=70)
    if lo <= day <= hi:
        return day
    try:
        swapped = date(day.year, day.day, day.month)
    except ValueError:
        return day
    for year in (file_month[0], file_month[0] - 1, file_month[0] + 1):
        try:
            candidate = swapped.replace(year=year)
        except ValueError:
            continue
        if lo <= candidate <= hi:
            return candidate
    return day


def _file_month(path):
    """(año, mes) del nombre del archivo: "Cierre 09-25.xlsx", "Gettel-Toyota Enero.xlsx" con la carpeta, etc."""
    name = os.path.basename(path)
    match = re.search(r"(\d{2})-(\d{2})\b", name)
    if match and 1 <= int(match.group(1)) <= 12:
        return 2000 + int(match.group(2)), int(match.group(1))
    match = re.search(r"[\\/](\d{4})[\\/](\d{2}) ", path)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def read_control_workbook(path):
    """
    Lee las hojas de Gettel-Toyota de un Excel de Cierre (o de control de
    Gettel). Devuelve {"days": [{date, la, kia, kia_gal, toyota, toyota_gal,
    main}], "payments": [{date, transc, amount, company}], "pos": [{date, la,
    vs}], "pending": {mes: primer día pendiente}}. "main" es la hoja del mes (no la de días pendientes del mes
    anterior). La empresa de un pago sale de la aclaración "(Kia)"/"(Toyota)"
    del primer renglón de cada pago; antes de TOYOTA_START, todo es Kia.
    """
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    file_month = _file_month(path)
    result = {"days": [], "payments": [], "pos": [], "pending": {}}
    try:
        for sheet in workbook.worksheets:
            title = sheet.title.upper()
            if title.startswith("STORE INFO"):
                result["pos"].extend(_read_store_info(sheet, file_month))
                continue
            if not any(key in title for key in ("GETTEL", "PENDIENTE", "PAGO", "PEND ")):
                continue
            rows = list(sheet.iter_rows(max_col=20, values_only=True))
            header = None
            for i, row in enumerate(rows[:6]):
                if "LOCAL ACCOUNT" in [_norm(v) for v in row]:
                    header = ("days", i)
                    break
            if header is None:
                for i, row in enumerate(rows[:6]):
                    if any("TRANSC" in _norm(v) for v in row):
                        header = ("payments", i)
                        break
            if header is None:
                continue
            kind, i = header
            labels = [_norm(v) for v in rows[i]]
            if kind == "days":
                days = _read_days(rows[i + 1:], labels, "PEND" not in title, file_month)
                result["days"].extend(days)
                # Hoja "Pendiente": los días sin cobrar al cierre de ese mes,
                # del primero al último. De ahí sale desde qué día quedó
                # pendiente (single_account), sea la hoja del mes o la del anterior.
                if "PEND" in title and days:
                    last = max(d["date"] for d in days)
                    result["pending"][last[:7]] = min(d["date"] for d in days)
            else:
                # Desde septiembre 2026 la hoja PAGO trae un cuadro por empresa,
                # Toyota en A-H y Kia en I-P (pedido del usuario, 2026-10-29),
                # con el nombre arriba: se lee cada cuadro por su columna "Transc".
                above = [_norm(v) for v in rows[i - 1]] if i else []
                for transc in (j for j, x in enumerate(labels) if "TRANSC" in x):
                    heading = above[transc - 2] if 0 <= transc - 2 < len(above) else ""
                    block = KIA if "KIA" in heading else TOYOTA if "TOYOTA" in heading else None
                    result["payments"].extend(_read_payments(rows[i + 1:], labels, file_month, transc, block))
    finally:
        workbook.close()
    return result


def _read_days(rows, labels, main, file_month):
    la = labels.index("LOCAL ACCOUNT")
    kia = next((j for j, x in enumerate(labels) if x in ("1 X 1", "GETTEL")), None)
    toyota = next((j for j, x in enumerate(labels) if x == "TOYOTA"), None)
    date_col = labels.index("FECHA") if "FECHA" in labels else 0
    days = []
    for row in rows:
        value = row[date_col] if date_col < len(row) else None
        if not isinstance(value, datetime):
            continue

        def cell(j):
            return _num(row[j]) if j is not None and j < len(row) else None

        days.append({
            "date": _fix_date(value.date(), file_month).isoformat(), "la": cell(la),
            "kia": cell(kia), "kia_gal": cell(kia + 1) if kia is not None else None,
            "toyota": cell(toyota), "toyota_gal": cell(toyota + 1) if toyota is not None else None,
            "main": main,
        })
    return days


def _read_payments(rows, labels, file_month, transc, block=None):
    """
    Los pagos de un cuadro de la hoja PAGO: la fecha dos columnas antes de
    "Transc", el importe en "Total Cupon" y la empresa del cuadro (block) o,
    si no, de la aclaración "(Kia)"/"(Toyota)" de la columna de al lado.
    """
    date_col = max(transc - 2, 0)
    amount = next((j for j, x in enumerate(labels) if j > transc and x.startswith("TOTAL CUPON")), transc + 1)
    payments, last, company = [], None, None
    for row in rows:
        if date_col < len(row) and isinstance(row[date_col], datetime):
            day = _fix_date(row[date_col].date(), file_month).isoformat()
            if day != last:
                company = None
            last = day
        number = row[transc] if transc < len(row) else None
        value = _num(row[amount]) if amount < len(row) else None
        if value is None or last is None or not re.fullmatch(r"\d+(\.0)?", str(number or "").strip()):
            continue
        # Solo la columna de al lado: con dos cuadros, más a la derecha está el de la otra empresa.
        note = _norm(row[amount + 1]) if amount + 1 < len(row) and isinstance(row[amount + 1], str) else ""
        if "KIA" in note:
            company = KIA
        elif "TOYOTA" in note:
            company = TOYOTA
        who = block or company or (KIA if last < TOYOTA_START else None)
        payments.append({"date": last, "transc": str(number).replace(".0", ""), "amount": round(value, 2),
                         "company": who})
    return payments


def _read_store_info(sheet, file_month):
    rows = list(sheet.iter_rows(max_col=30, values_only=True))
    for i, row in enumerate(rows[:6]):
        labels = [_norm(v) for v in row]
        if "LOCAL ACCOUNT" in labels and "VS" in labels:
            la, vs = labels.index("LOCAL ACCOUNT"), labels.index("VS")
            return [{"date": r[0].date().isoformat(), "la": _num(r[la]), "vs": _num(r[vs])}
                    for r in rows[i + 1:] if isinstance(r[0], datetime)]
    return []


def best_days(days):
    """Un renglón por día: el que tiene las empresas separadas y, entre esos, el de la hoja del mes."""
    best = {}
    for d in days:
        score = (bool((d["kia"] or 0) or (d["toyota"] or 0)), d.get("main", True))
        current = best.get(d["date"])
        if current is None or score >= current[0]:
            best[d["date"]] = (score, d)
    return {k: v[1] for k, v in best.items()}


# ---------------------------------------------------------------------------
# Cuenta corriente
# ---------------------------------------------------------------------------

def payment_groups(payments):
    """
    Junta los cobros de un mismo pago (mismo día, N° de transacción
    seguidos): un pago de $4,153.67 son cinco cobros. Cada grupo tiene su
    empresa si todos sus cobros la tienen.
    """
    groups = []
    for p in sorted(payments, key=lambda p: (p["date"], int(p["transc"]) if p["transc"].isdigit() else 0)):
        number = int(p["transc"]) if p["transc"].isdigit() else None
        last = groups[-1] if groups else None
        if (last and last["date"] == p["date"] and number is not None and last["last_transc"] is not None
                and 0 < number - last["last_transc"] <= 3 and last["company"] == p["company"]):
            last["items"].append(p)
            last["last_transc"] = number
        else:
            groups.append({"date": p["date"], "items": [p], "last_transc": number, "company": p["company"]})
    for g in groups:
        g["amount"] = round(sum(p["amount"] for p in g["items"]), 2)
        g["ids"] = [p.get("id") for p in g["items"] if p.get("id") is not None]
        g["transcs"] = [p["transc"] for p in g["items"]]
        g["pos_only"] = any(p.get("pos_only") for p in g["items"])
    return groups


# Días en que el POS cobró LOCAL ACCT (VS) y no hay recibo anotado: el pago
# existe (lo cobró la caja y lo depositó J.H.) aunque falte el papel. Se
# suman como "cobrado sin recibo", asignables a una empresa (pedido del
# usuario, 2026-10-07: abril, mayo y septiembre 2026 tenían $14,184.73 así,
# que eran de Kia). Un recibo anotado con otra fecha que el cobro del POS
# (hasta POS_MATCH_DAYS de distancia) se compensa y no cuenta dos veces.
POS_ONLY = "POS"
POS_MATCH_DAYS = 7


def pos_only_payments(payments, pos, assignments=None):
    """Lo cobrado en el POS sin recibo, un pago por grupo de días cercanos; y lo anotado de más."""
    assignments = assignments or {}
    paid = {}
    for p in payments:
        if p["company"] != VOIDED:
            paid[p["date"]] = round(paid.get(p["date"], 0.0) + p["amount"], 2)
    diffs = []
    for day in sorted(set(paid) | {d for d, v in pos.items() if v.get("vs")}):
        if day not in pos or pos[day].get("vs") is None:
            continue
        diff = round((pos[day].get("vs") or 0.0) - paid.get(day, 0.0), 2)
        if abs(diff) > TOLERANCE:
            diffs.append((day, diff))
    clusters = []
    for day, diff in diffs:
        if clusters and (date.fromisoformat(day) - date.fromisoformat(clusters[-1][-1][0])).days <= POS_MATCH_DAYS:
            clusters[-1].append((day, diff))
        else:
            clusters.append([(day, diff)])
    extra, over = [], []
    for cluster in clusters:
        net = round(sum(d for _, d in cluster), 2)
        day = max(cluster, key=lambda x: x[1])[0]
        if net > TOLERANCE:
            company = assignments.get(day) or (KIA if day < TOYOTA_START else None)
            extra.append({"date": day, "transc": POS_ONLY, "amount": net, "company": company,
                          "pos_only": True})
        elif net < -TOLERANCE:
            over.append({"date": min(cluster, key=lambda x: x[1])[0], "amount": -net})
    return extra, over


def build_ledger(charges, payments, pos, today=None):
    """
    charges: {fecha: {kia, kia_gal, toyota, toyota_gal, la}}; payments:
    [{date, transc, amount, company}] (company None = sin asignar); pos:
    {fecha: {la, vs}} (Local Account y LOCAL ACCT cobrado del POS). Devuelve
    los saldos por empresa, el resumen por mes y lo que hay que revisar.
    """
    today = today or date.today()
    events = {}  # mes -> empresa -> {...}
    by_day = {}

    def bucket(month, company):
        return events.setdefault(month, {}).setdefault(company, {
            "vouchers": 0.0, "gallons": 0.0, "charged": 0.0, "paid": 0.0})

    for day, c in charges.items():
        month = day[:7]
        row = by_day.setdefault(day, {"date": day})
        for company, amount_key, gal_key in ((KIA, "kia", "kia_gal"), (TOYOTA, "toyota", "toyota_gal")):
            amount, gallons = c.get(amount_key) or 0.0, c.get(gal_key) or 0.0
            charged = to_charge(amount, gallons) if amount else 0.0
            b = bucket(month, company)
            b["vouchers"] += amount
            b["gallons"] += gallons
            b["charged"] += charged
            row[company] = {"vouchers": amount, "gallons": gallons, "charged": charged}
        row["la_excel"] = c.get("la")
    unassigned = []
    payments = [p for p in payments if p["company"] != VOIDED]
    for p in payments:
        row = by_day.setdefault(p["date"], {"date": p["date"]})
        row.setdefault("paid", {}).setdefault(p["company"] or "?", 0.0)
        row["paid"][p["company"] or "?"] = round(row["paid"][p["company"] or "?"] + p["amount"], 2)
        if p["company"]:
            bucket(p["date"][:7], p["company"])["paid"] += p["amount"]
        else:
            unassigned.append(p)
    for day, values in pos.items():
        row = by_day.setdefault(day, {"date": day})
        row["la_pos"], row["vs"] = values.get("la"), values.get("vs")

    months = sorted(events)
    balances = {company: 0.0 for company in COMPANIES}
    monthly = []
    for month in months:
        entry = {"month": month, "companies": {}}
        for company in COMPANIES:
            b = events[month].get(company) or {"vouchers": 0.0, "gallons": 0.0, "charged": 0.0, "paid": 0.0}
            opening = balances[company]
            balances[company] = round(opening + b["charged"] - b["paid"], 2)
            entry["companies"][company] = {
                "opening": round(opening, 2), "vouchers": round(b["vouchers"], 2), "gallons": round(b["gallons"], 2),
                "charged": round(b["charged"], 2), "paid": round(b["paid"], 2), "closing": balances[company]}
        entry["unassigned"] = round(sum(p["amount"] for p in unassigned if p["date"][:7] == month), 2)
        monthly.append(entry)

    # Días: Local Account contra los vales, y lo cobrado en el POS contra los pagos anotados.
    days = []
    running = {company: 0.0 for company in COMPANIES}
    for day in sorted(by_day):
        row = by_day[day]
        # Saldo de cada una al cierre del día (pedido del usuario: "con qué
        # saldo quedan día a día"). Los pagos sin empresa no lo mueven.
        for company in COMPANIES:
            running[company] = round(running[company] + (row.get(company) or {}).get("charged", 0.0)
                                     - (row.get("paid") or {}).get(company, 0.0), 2)
        row["balance"] = dict(running)
        vouchers = sum((row.get(c) or {}).get("vouchers", 0.0) for c in COMPANIES)
        la = row.get("la_pos") if row.get("la_pos") is not None else row.get("la_excel")
        row["vouchers_total"] = round(vouchers, 2)
        row["la"] = la
        row["la_diff"] = None if la is None or day not in charges else round(la - vouchers, 2)
        paid = round(sum((row.get("paid") or {}).values()), 2)
        row["paid_total"] = paid
        row["vs_diff"] = None if row.get("vs") is None else round(row["vs"] - paid, 2)
        days.append(row)

    last_payment = {}
    for p in payments:
        if p["company"] and p["date"] > last_payment.get(p["company"], ""):
            last_payment[p["company"]] = p["date"]
    issues = []
    status = {}
    for company in COMPANIES:
        last = last_payment.get(company)
        days_since = (today - date.fromisoformat(last)).days if last else None
        status[company] = {"balance": balances[company], "last_payment": last, "days_since": days_since,
                           "last_amount": round(sum(p["amount"] for p in payments
                                                    if p["company"] == company and p["date"] == last), 2) if last else None,
                           "late": days_since is not None and days_since > LATE_PAYMENT_DAYS}
        if status[company]["late"]:
            issues.append(f"{company} no paga hace {days_since} días (último pago: {last[8:10]}/{last[5:7]}/{last[:4]}).")
    if unassigned:
        issues.append(f"{len(payment_groups(unassigned))} pago(s) sin empresa (${sum(p['amount'] for p in unassigned):,.2f}): "
                      f"asignalos en Carga de Datos → Gettel / Toyota para que el saldo de cada una sea el real.")
    return {
        "monthly": monthly,
        "days": days,
        "payments": sorted(payments, key=lambda p: (p["date"], str(p.get("transc") or ""))),
        "status": status,
        "total_balance": round(sum(balances.values()) - sum(p["amount"] for p in unassigned), 2),
        "unassigned_groups": payment_groups(unassigned),
        "issues": issues,
    }


def month_days(ledger, year, month):
    """Los días del mes (todos, aunque no tengan movimientos)."""
    by_date = {d["date"]: d for d in ledger["days"]}
    first = f"{year:04d}-{month:02d}-01"
    previous = [d for d in ledger["days"] if d["date"] < first]
    balance = dict(previous[-1]["balance"]) if previous else {company: 0.0 for company in COMPANIES}
    rows = []
    for day in range(1, calendar.monthrange(year, month)[1] + 1):
        key = f"{year:04d}-{month:02d}-{day:02d}"
        row = by_date.get(key) or {"date": key}
        balance = row.get("balance") or balance
        rows.append(dict(row, balance=balance))
    return rows


# ---------------------------------------------------------------------------
# Cierre del mes como el Excel Cierre y Ratio (pedido del usuario, 2026-10-29)
# ---------------------------------------------------------------------------

# Las cuentas de Ratio (Libro Diario, asientos 11745 y 11747 de septiembre 2026).
ACCOUNT_GETTEL = "1.02.01.00 - GETTEL KIA"
ACCOUNT_OTHER_INCOME = "4.07.00.00 - OTROS INGRESOS COMBUSTIBLE"
ACCOUNT_JH = "1.03.02.00 - J.H. WILLIAMS - RECAUDACION A LIQUIDAR"


def _to_collect(vouchers, gallons):
    """
    Lo que se cobra por los vales de un mes, como la hoja Gettel-Toyota del
    Cierre: rebate = −$0.02 × galones (E35) y recargo = 3% de (vales +
    rebate) (E37). Total a cobrar = vales + rebate + recargo (E39).
    """
    rebate = round(-REBATE_PER_GALLON * gallons, 2)
    charge = round((vouchers + rebate) * CARD_CHARGE, 2)
    return {"vouchers": round(vouchers, 2), "gallons": round(gallons, 2), "rebate": rebate, "charge": charge,
            "total": round(vouchers + rebate + charge, 2)}


def month_closing(ledger, year, month):
    """
    El mes de Kia y Toyota como el Cierre y Ratio: por empresa, los vales,
    el rebate y el recargo, lo que pagó (cada pago) y el saldo; y los dos
    asientos de Ratio del mes, REBATE GETTEL (Gettel KIA a Otros ingresos
    combustible: recargo + rebate de las dos) y COBRO GETTEL-KIA (J.H. a
    Gettel KIA: el LOCAL ACCT que cobró el POS). Medido contra septiembre
    2026: rebate $636.32 y cobro $28,494.94, como Ratio.
    """
    from control_cierre import MONTH_NAMES_UPPER

    prefix = f"{year:04d}-{month:02d}"
    days = [d for d in ledger["days"] if d["date"].startswith(prefix)]
    summary = next((m for m in ledger["monthly"] if m["month"] == prefix), None)
    companies = {}
    for company in COMPANIES:
        vouchers = sum((d.get(company) or {}).get("vouchers", 0.0) for d in days)
        gallons = sum((d.get(company) or {}).get("gallons", 0.0) for d in days)
        block = _to_collect(vouchers, gallons)
        payments = [p for p in ledger.get("payments", []) if p["company"] == company and p["date"].startswith(prefix)]
        opening = summary["companies"][company]["opening"] if summary else 0.0
        paid = round(sum(p["amount"] for p in payments), 2)
        block.update({"opening": opening, "payments": payments, "paid": paid,
                      "closing": round(opening + block["total"] - paid, 2)})
        companies[company] = block
    total = _to_collect(sum(c["vouchers"] for c in companies.values()), sum(c["gallons"] for c in companies.values()))
    total.update({key: round(sum(c[key] for c in companies.values()), 2) for key in ("opening", "paid", "closing")})
    unassigned = [p for p in ledger.get("payments", []) if not p["company"] and p["date"].startswith(prefix)]
    la = round(sum(d.get("la") or 0.0 for d in days), 2)
    vs = round(sum(d.get("vs") or 0.0 for d in days), 2)
    # Sin redondear en el medio, como el Excel (E35 + E37): agosto 2026 da
    # $604.48, igual que Ratio (redondeando cada parte daba $604.49).
    all_gallons = sum(c["gallons"] for c in companies.values())
    raw_rebate = -REBATE_PER_GALLON * all_gallons
    rebate_entry = round(raw_rebate + (sum(c["vouchers"] for c in companies.values()) + raw_rebate) * CARD_CHARGE, 2)
    month_name = MONTH_NAMES_UPPER[month - 1]
    return {
        "has_data": bool(total["vouchers"] or total["paid"] or vs),
        "companies": companies,
        "total": total,
        "unassigned": unassigned,
        "unassigned_total": round(sum(p["amount"] for p in unassigned), 2),
        "la": la,
        "la_diff": round(la - total["vouchers"], 2),
        "vs": vs,
        "vs_diff": round(vs - total["paid"] - sum(p["amount"] for p in unassigned), 2),
        "entries": [
            {"title": f"REBATE GETTEL {month_name} {year}", "lines": [
                (ACCOUNT_GETTEL, rebate_entry, None), (ACCOUNT_OTHER_INCOME, None, rebate_entry)]},
            {"title": f"COBRO GETTEL-KIA {month_name}", "lines": [
                (ACCOUNT_JH, vs, None), (ACCOUNT_GETTEL, None, vs)]},
        ],
    }


# ---------------------------------------------------------------------------
# Gettel como una sola cuenta (pedido del usuario, 2026-11-13)
# ---------------------------------------------------------------------------
# Kia y Toyota se llevan juntas, como en el Excel de Cierre (hojas
# "Gettel-Toyota", "PAGO Cupones" y "Pendiente") y en el papel de trabajo de
# la auditoría ("WP GETTEL, J.H WILLIAMS", hoja "Analisis saldo GETTEL."): es
# la cuenta 1.02.01.00 GETTEL KIA de Ratio.
# - A cobrar del mes (TOTAL CUPON A COBRAR) = Local Account + rebate
#   (−$0.02 por galón) + CHARGE 3% de (vales + rebate).
# - Lo pendiente al cierre son días enteros: desde el primer día sin cobrar
#   hasta fin de mes, y ese total es lo que arrastra el mes siguiente. El día
#   lo decide quien arma el Cierre (hoja "Pendiente"); la app lo toma del
#   Excel, o de lo que se cargue a mano, y si no hay nada sugiere el tramo
#   que más se acerca a lo que falta cobrar.
# - Ese pendiente se compara contra el saldo de la cuenta en Ratio, con un
#   margen: los pagos con tarjeta no caen justo en el corte de un día.

GETTEL_ACCOUNT_CODE = "1.02.01.00"
GETTEL_ACCOUNT_NAME = "GETTEL KIA"
DEFAULT_MARGIN = 500.0
# Desde qué día quedó pendiente cada mes en la hoja "Pendiente" de los Excel
# de Cierre del Drive (leídos el 13/11/2026). Junio 2026 es el papel de
# trabajo de la auditoría: 17/06 al 30/06, $11,581.64 = saldo de Ratio.
PENDING_FROM = {
    "2024-12": "2024-11-29", "2025-02": "2025-02-01", "2025-03": "2025-03-16", "2025-04": "2025-04-30",
    "2025-05": "2025-05-28", "2025-06": "2025-06-30", "2025-07": "2025-07-31", "2025-08": "2025-08-19",
    "2025-09": "2025-09-23", "2025-10": "2025-10-27", "2025-11": "2025-11-25", "2025-12": "2025-12-31",
    "2026-01": "2026-01-29", "2026-02": "2026-02-25", "2026-03": "2026-03-28", "2026-04": "2026-04-26",
    "2026-05": "2026-05-24", "2026-06": "2026-06-17", "2026-07": "2026-07-19", "2026-08": "2026-08-19",
    "2026-09": "2026-09-21", "2026-10": "2026-10-20",
}
# Hasta cuántos días para atrás se busca el tramo pendiente.
_PENDING_LOOKBACK = 92


def sheet_row(day):
    """Un día como un renglón de la hoja Gettel-Toyota: Local Account, Gettel, galones, Toyota, galones y DIF."""
    kia, toyota = day.get(KIA) or {}, day.get(TOYOTA) or {}
    vouchers = (kia.get("vouchers") or 0.0) + (toyota.get("vouchers") or 0.0)
    la = day.get("la") if day.get("la") is not None else vouchers
    return {"date": day["date"], "la": la,
            "kia": kia.get("vouchers") or 0.0, "kia_gal": kia.get("gallons") or 0.0,
            "toyota": toyota.get("vouchers") or 0.0, "toyota_gal": toyota.get("gallons") or 0.0,
            "dif": round(la - vouchers, 2)}


def sheet_totals(rows):
    """
    La fila VERIFICAR y lo de abajo, con las fórmulas del Excel (sin redondear
    en el medio): rebate = −0.02 × galones, charge = 3% de (vales + rebate) y
    total = Local Account + rebate + charge.
    """
    totals = {key: sum(r[key] for r in rows) for key in ("la", "kia", "kia_gal", "toyota", "toyota_gal", "dif")}
    totals["rebate"] = -REBATE_PER_GALLON * (totals["kia_gal"] + totals["toyota_gal"])
    totals["charge"] = (totals["kia"] + totals["toyota"] + totals["rebate"]) * CARD_CHARGE
    totals["total"] = totals["la"] + totals["rebate"] + totals["charge"]
    return totals


def suggest_pending(rows, target):
    """
    Los días pendientes cuando nadie los fijó: el tramo final de días cuyo
    TOTAL CUPON A COBRAR más se acerca a lo que falta cobrar. Vacío si no
    falta nada, o si lo que falta es menos de la mitad del último día.
    """
    best, best_gap = [], abs(target)
    for start in range(len(rows) - 1, -1, -1):
        total = sheet_totals(rows[start:])["total"]
        gap = abs(total - target)
        if gap < best_gap:
            best, best_gap = rows[start:], gap
        elif total > target + best_gap:
            break
    return best


def _blank_row(day):
    return {"date": day, "la": 0.0, "kia": 0.0, "kia_gal": 0.0, "toyota": 0.0, "toyota_gal": 0.0, "dif": 0.0}


def single_account(ledger, pending_from=None):
    """
    La cuenta de Gettel mes a mes, como el Excel de Cierre. `ledger` es el de
    build_ledger; `pending_from` es {mes: día} con el primer día pendiente de
    cada mes (lo que dice la hoja "Pendiente" o lo cargado a mano), que pisa
    a PENDING_FROM. Devuelve {mes: {...}} con la hoja del mes, los pagos, lo
    cobrado, los días pendientes y los tres movimientos de la cuenta en Ratio.
    """
    from control_cierre import MONTH_NAMES_UPPER

    fixed = dict(PENDING_FROM)
    fixed.update(pending_from or {})
    by_day = {d["date"]: d for d in ledger["days"]}
    data_days = sorted(d for d, row in by_day.items()
                       if row.get("la") is not None or row.get("vouchers_total"))
    rows = [sheet_row(by_day[d]) for d in data_days]
    payments = ledger.get("payments", [])
    months = sorted({d[:7] for d in data_days} | {p["date"][:7] for p in payments})
    account, opening, opening_range = {}, 0.0, None
    for key in months:
        year, month = int(key[:4]), int(key[5:7])
        month_end = f"{key}-{calendar.monthrange(year, month)[1]:02d}"
        loaded = {r["date"]: r for r in rows if r["date"][:7] == key}
        last = max(loaded) if loaded else month_end
        # Todos los días hasta el último cargado, como la hoja del Excel.
        month_rows = [loaded.get(f"{key}-{d:02d}") or _blank_row(f"{key}-{d:02d}")
                      for d in range(1, int(last[8:10]) + 1)]
        totals = sheet_totals(month_rows)
        month_payments = [p for p in payments if p["date"][:7] == key]
        groups = payment_groups(month_payments)
        for number, group in enumerate(groups, 1):
            group["number"] = number
        collected = sum(p["amount"] for p in month_payments)
        to_collect = opening + totals["total"]
        missing = to_collect - collected
        upto = [r for r in rows if r["date"] <= month_end][-_PENDING_LOOKBACK:]
        suggested = suggest_pending(upto, missing)
        start = fixed.get(key)
        if start and not any(r["date"] >= start for r in upto):
            start = None  # el mes todavía no llegó a ese día: vale la sugerencia
        pending_rows =[r for r in upto if r["date"] >= start] if start else suggested
        pending = sheet_totals(pending_rows)
        vs = sum(row.get("vs") or 0.0 for day, row in by_day.items() if day[:7] == key)
        rebate_entry = totals["rebate"] + totals["charge"]
        month_name = MONTH_NAMES_UPPER[month - 1]
        account[key] = {
            "month": key, "last": last, "complete": last == month_end, "has_data": bool(loaded or month_payments),
            "rows": month_rows, "totals": totals,
            "opening": opening, "opening_range": opening_range,
            "to_collect": to_collect, "payments": groups, "collected": collected,
            "difference": collected - to_collect,
            "pending_rows": pending_rows, "pending": pending,
            "pending_from": pending_rows[0]["date"] if pending_rows else None,
            "pending_fixed": bool(start),
            "suggested_from": suggested[0]["date"] if suggested else None,
            # Lo que falta cobrar menos los días pendientes: el pago que no cae justo en el corte de un día.
            "gap": round(missing - pending["total"], 2),
            "vs": round(vs, 2),
            "vs_diff": round(vs - collected, 2),
            "entries": [
                {"title": f"LIQUIDACION CIERRE RECAUDACION {month:02d}-{year}", "debit": round(totals["la"], 2),
                 "credit": None, "note": "Local Account del mes (Store Info)"},
                {"title": f"COBRO GETTEL-KIA {month_name}", "debit": None,
                 "credit": round(vs if vs else collected, 2), "note": "LOCAL ACCT cobrado en el POS"},
                {"title": f"REBATE GETTEL {month_name} {year}", "debit": round(rebate_entry, 2), "credit": None,
                 "note": "Rebate x Gallon + CHARGE 3%"},
            ],
        }
        opening = pending["total"]
        opening_range = (pending_rows[0]["date"], pending_rows[-1]["date"]) if pending_rows else None
    return account


def account_month(account, year, month):
    """El mes pedido de single_account, o un mes vacío que arrastra el pendiente del último mes anterior."""
    key = f"{year:04d}-{month:02d}"
    if key in account:
        return account[key]
    previous = [m for m in sorted(account) if m < key]
    last = account[previous[-1]] if previous else None
    opening = last["pending"]["total"] if last else 0.0
    empty = sheet_totals([])
    return {"month": key, "last": None, "complete": False, "has_data": False, "rows": [], "totals": empty,
            "opening": opening,
            "opening_range": ((last["pending_rows"][0]["date"], last["pending_rows"][-1]["date"])
                              if last and last["pending_rows"] else None),
            "to_collect": opening, "payments": [], "collected": 0.0, "difference": -opening,
            "pending_rows": last["pending_rows"] if last else [], "pending": last["pending"] if last else empty,
            "pending_from": last["pending_from"] if last else None, "pending_fixed": False, "suggested_from": None,
            "gap": 0.0, "vs": 0.0, "vs_diff": 0.0, "entries": []}
