"""
Utilidad neta proyectada (pedido del usuario, 2026-11-02): la utilidad bruta
de Proyecciones menos los gastos del mes, con los mismos renglones que el
"ESTADO RESULTADO" del BALANCE HISTORICO del Bce.

Todo sale de lo que se carga día a día:
  - Utilidad bruta: combustible y C-Store por rubro como en Proyecciones, la
    comisión real de Lottery (proyecciones.lottery_commissions), los
    ingresos de Chase (rebates, hielo, Food Truck, Vaccumms) y, restando, los
    fees de tarjeta de los Cupones (por fecha de venta).
  - Gastos: Chase por categoría y los gastos de efectivo de Caja. Lo de Chase
    sin categoría va aparte, a la vista, para que no se pierda.

A fin de mes se proyecta así:
  - Lo que se paga una vez por mes (MONTHLY): lo real si ya se pagó; si
    todavía no, lo del mes anterior.
  - Lo demás: lo real hasta el último día cargado, al mismo ritmo hasta fin
    de mes. Sin ningún día cargado, lo del mes anterior.

Chase es lo que se paga y el Bce lo que corresponde al mes, así que algunos
renglones no coinciden (en agosto 2026: sueldos $12.288 contra $16.738). Por
eso, si el mes tiene Bce, cada grupo muestra al lado lo real del Bce.
"""

import calendar
import datetime as dt

import caja_db
import chase_db
import eft_db
import proyecciones

# Categorías de Chase de cada renglón.
REBATE_FUEL = ("REBATE COMBUSTIBLE",)
REBATE_CSTORE = ("REBATE",)
ICE = ("VENTA ICE", "DEPOSITO VENTA ICE")
FOOD_TRUCK = ("FOOD TRUCK",)
VACCUMMS = ("DEPOSITO VACCUMMS",)
# (campo del Bce, renglón, categorías de Chase). "Otros Gastos Varios" suma
# además los gastos de Caja.
EXPENSES = (
    ("sueldos", "Sueldos y Jornales", ("SUELDOS", "SUELDO")),
    ("gastos_bancarios", "Comisiones y Gastos Bancarios", ("GASTOS BANCARIOS", "COMISIONES Y GASTOS BANCARIOS")),
    ("energia", "Energía Eléctrica", ("ENERGIA ELECTRICA",)),
    ("agua", "Water and Waste Services", ("AGUA",)),
    ("intereses", "Intereses y Recargos", ()),
    ("seguro", "Seguro", ("SEGURO",)),
    ("reparaciones", "Reparación y Mantenimiento", ("REPARACION Y MANTENIMIENTO",)),
    ("adm_fees", "Administration Fees", ("ADMINISTRATION FEE",)),
    ("otros_gastos_varios", "Otros Gastos Varios", ("INTERNET", "ALARMA", "TELEFONO", "ELISTAR")),
    ("otros_gastos", "Otros (Movilidad y Viáticos)", ("MOVILIDAD Y VIATICOS",)),
)
# Las facturas de una vez por mes.
MONTHLY = {"ENERGIA ELECTRICA", "AGUA", "SEGURO", "INTERNET", "ALARMA", "TELEFONO", "ELISTAR"}
# El alquiler del Food Truck es fijo pero entra en varios depósitos: hasta
# que llega todo, lo del mes anterior.
FIXED = {"FOOD TRUCK"}


def _prev_month(year, month):
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _elapsed(last, year, month):
    """Días del mes cubiertos hasta `last` (una fecha o None)."""
    if last is None:
        return 0
    first = dt.date(year, month, 1)
    if last < first:
        return 0
    return min((last - first).days + 1, calendar.monthrange(year, month)[1])


def _at_pace(actual, elapsed, days_in_month, previous):
    if elapsed >= days_in_month:
        return actual
    if elapsed:
        return actual * days_in_month / elapsed
    return previous


def _chase_by_category(year, month):
    """{categoría: neto} del mes; "" son los débitos sin categoría."""
    totals = {}
    for tx in chase_db.get_month_transactions(year, month):
        key = tx.get("detalle") or ""
        amount = tx.get("amount") or 0.0
        if not key and amount >= 0:
            continue
        totals[key] = totals.get(key, 0.0) + amount
    return totals


def _card_fees(year, month):
    """Fees de los Cupones con fecha de venta en el mes, y la última fecha."""
    total, last = 0.0, None
    for row in eft_db.list_cupones_for_forecast():
        day = row["date"].date() if isinstance(row["date"], dt.datetime) else row["date"]
        if (day.year, day.month) != (year, month):
            continue
        total += row.get("fees") or 0.0
        last = day if last is None or day > last else last
    return total, last


def _caja_expenses(year, month):
    items = caja_db.get_month_expense_items(year, month)
    return sum(item["amount"] or 0.0 for day_items in items.values() for item in day_items)


def _line(label, source, actual, proj, bce=None):
    return {"label": label, "source": source, "actual": actual, "proj": proj, "bce": bce}


def _group(title, lines, bce=None, key=None):
    def total(field):
        values = [ln[field] for ln in lines]
        return None if any(v is None for v in values) else sum(values)
    return {"key": key, "title": title, "lines": lines, "actual": total("actual"), "proj": total("proj"), "bce": bce}


def build_net(year, month, projection=None):
    """El desglose de la utilidad neta del mes: lo de hasta hoy, lo proyectado y lo real del Bce."""
    p = projection or proyecciones.build_projection(year, month)
    days_in_month = calendar.monthrange(year, month)[1]
    prev_year, prev_month = _prev_month(year, month)

    last_chase = chase_db.get_last_posting_date()
    last_chase = dt.date.fromisoformat(str(last_chase)[:10]) if last_chase else None
    chase_elapsed = _elapsed(last_chase, year, month)
    chase = _chase_by_category(year, month)
    chase_prev = _chase_by_category(prev_year, prev_month)
    chase_until = last_chase if chase_elapsed and chase_elapsed < days_in_month else None

    def chase_amount(categories, sign, totals):
        # + 0.0: que un gasto en cero no salga "−0.00".
        return sign * sum(totals.get(c, 0.0) for c in categories) + 0.0

    def chase_line(label, categories, sign=1):
        """Un renglón de Chase: ingresos con sign=1, gastos con sign=-1 (quedan en positivo)."""
        actual = chase_amount(categories, sign, chase)
        proj = 0.0
        for category in categories:
            now = chase_amount((category,), sign, chase)
            previous = chase_amount((category,), sign, chase_prev)
            if chase_elapsed >= days_in_month:
                proj += now
            elif category in MONTHLY:
                proj += now if now else previous
            elif category in FIXED:
                proj += max(now, previous)
            else:
                proj += _at_pace(now, chase_elapsed, days_in_month, previous)
        names = ", ".join(categories) if categories else "sin categoría"
        if chase_elapsed >= days_in_month:
            how = ""
        elif any(c in MONTHLY for c in categories):
            how = "si todavía no se pagó, la del mes anterior"
        elif any(c in FIXED for c in categories):
            how = "hasta que entre todo, lo del mes anterior"
        elif chase_until:
            how = f"al ritmo hasta el {chase_until.strftime('%d/%m')}"
        else:
            how = ""
        return _line(label, f"Chase: {names}" + (f" · {how}" if how else ""), actual, proj)

    real = proyecciones.balance_for_month(year, month)

    def bce(*fields, sign=1):
        if not real:
            return None
        values = [real.get(f) for f in fields]
        if any(v is None for v in values):
            return None
        return sign * sum(values)

    # ---------------- Utilidad bruta ----------------
    fuel = _group("Combustible", [
        _line("Venta − costo (PPP)", "Close Store y Físico", p["fuel"]["utilidad"], p["fuel_proj"]["utilidad"]),
        chase_line("Rebate de combustible", REBATE_FUEL),
    ], bce=(bce("ventas_combustible", "otros_ingresos_combustible") - real["costo_combustible"])
        if bce("ventas_combustible", "otros_ingresos_combustible", "costo_combustible") is not None else None)

    rubros = [r for r in p["cstore"]["rubros"] if r["in_balance"]]
    cstore = _group("C-Store", [
        _line(r["display"], f"Close Store × {r['margin'] * 100:.0f} %", r["utilidad"], r["utilidad_proj"]) for r in rubros
    ] + [chase_line("Rebates", REBATE_CSTORE)],
        bce=(real["ventas_cstore"] - real["costo_cstore"]) if bce("ventas_cstore", "costo_cstore") is not None else None,
        key="cstore")

    lot = p["lottery"]
    if lot["estimated"]:
        lot_source = "Sin comisiones cargadas: la venta de LOTTO × el % teórico"
    else:
        lot_source = (f"Lottery: semanas del {lot['first'].strftime('%d/%m')} al {lot['last'].strftime('%d/%m')}, "
                      f"{lot['days_loaded']} de {lot['days_total']} días con comisión")
    lottery = _group("Lottery", [_line("Comisiones On-Line y Skoff", lot_source, lot["comm"] or 0.0, lot["comm_proj"])],
                     bce=bce("comisiones_lottery"))

    others = _group("Otros ingresos", [
        chase_line("Máquina de hielo", ICE),
        chase_line("Alquiler Food Truck", FOOD_TRUCK),
        chase_line("Vaccumms", VACCUMMS),
    ], bce=bce("ventas_ice", "alquiler_food_truck", "ventas_vaccum", "otros_ingresos"))

    fees, last_coupon = _card_fees(year, month)
    fees_prev, _ = _card_fees(prev_year, prev_month)
    coupon_elapsed = _elapsed(last_coupon, year, month)
    fees_source = "Cupones: fees por fecha de venta"
    if coupon_elapsed and coupon_elapsed < days_in_month:
        fees_source += f" · al ritmo hasta el {last_coupon.strftime('%d/%m')}"
    cards = _group("Gastos de tarjeta", [
        _line("Fees de tarjeta", fees_source, -fees, -_at_pace(fees, coupon_elapsed, days_in_month, fees_prev)),
    ], bce=bce("gastos_tarjeta", sign=-1))

    gross_groups = [fuel, cstore, lottery, others, cards]

    # ---------------- Gastos ----------------
    store_elapsed = _elapsed(p["last_day"], year, month)
    caja = _caja_expenses(year, month)
    caja_proj = _at_pace(caja, store_elapsed, days_in_month, _caja_expenses(prev_year, prev_month))
    expense_lines = []
    for field, label, categories in EXPENSES:
        line = chase_line(label, categories, sign=-1)
        if field == "otros_gastos_varios":
            line["actual"] += caja
            line["proj"] += caja_proj
            line["source"] += " · Caja: gastos de efectivo"
        if not categories:
            # Intereses y Recargos no tiene categoría en Chase: solo aparece si el Bce lo trae.
            if not bce(field):
                continue
            line = _line(label, "No tiene categoría en Chase", 0.0, 0.0)
        line["bce"] = bce(field, sign=-1)
        expense_lines.append(line)
    uncategorized = chase_line("Sin categoría en Chase", ("",), sign=-1)
    uncategorized["source"] = "Chase: débitos sin categoría (categorizalos en Chase para que vayan a su renglón)"
    if chase_elapsed and chase_elapsed < days_in_month:
        uncategorized["source"] += f" · al ritmo hasta el {chase_until.strftime('%d/%m')}"
    expense_lines.append(uncategorized)
    expenses = _group("Gastos", expense_lines, bce=bce("gastos_directos", "otros_gastos", sign=-1))

    def add(groups, field):
        values = [g[field] for g in groups]
        return None if any(v is None for v in values) else sum(values)

    gross = {"actual": add(gross_groups, "actual"), "proj": add(gross_groups, "proj"), "bce": bce("ganancia_bruta")}
    net = {
        field: (gross[field] - expenses[field]) if (gross[field] is not None and expenses[field] is not None) else None
        for field in ("actual", "proj")
    }
    net["bce"] = bce("resultado_neto")
    return {
        "year": year,
        "month": month,
        "days_in_month": days_in_month,
        "last_store_day": p["last_day"],
        "last_chase_day": last_chase,
        "gross_groups": gross_groups,
        "gross": gross,
        "expenses": expenses,
        "net": net,
        "has_bce": bool(real and real.get("resultado_neto") is not None),
        "bce_source": real.get("archivo") if real else None,
        "ppp_missing": p["ppp"] is None,
    }
