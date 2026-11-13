"""
Control de asientos (pedido del usuario, 2026-11-13): los asientos de cierre
del mes como los calcula la app (Cierre, Gettel, Lottery, loyalty de
Chevron), renglón por renglón contra lo que quedó cargado en Ratio.

Ratio se lee de los mayores subidos en Carga de Datos → Ratio
(ratio_mayor.py): cada renglón esperado (cuenta, Debe o Haber, importe) se
busca en el mayor de su cuenta, en el mes. Cálculo puro, sin base ni UI.
"""

ACCOUNTS = {
    "1.01.01.00": "CAJA",
    "1.01.02.00": "CHASE BANK",
    "1.02.01.00": "GETTEL KIA",
    "1.03.02.00": "J.H. WILLIAMS - RECAUDACION A LIQUIDAR",
    "2.01.16.00": "LOTTERY A PAGAR",
    "2.02.01.00": "SALE TAX A PAGAR",
    "4.01.00.00": "VENTAS DE COMBUSTIBLE",
    "4.04.00.00": "VENTAS CAR WASH",
    "4.05.00.00": "VENTAS ICE MACHINE",
    "4.07.00.00": "OTROS INGRESOS COMBUSTIBLE",
    "4.09.00.00": "VENTAS C-STORE",
    "4.10.00.00": "COMISIONES LOTTERY",
    "4.11.00.00": "OTROS INGRESOS - LOYALTY CHEVRON",
    "5.04.00.00": "GASTOS DE TARJETA DE CREDITO",
    "5.13.00.00": "TAX BGS",
}
# Los nombres de la hoja Store Info del Cierre (control_cierre.entry_lines) y su cuenta de Ratio.
_CIERRE_ACCOUNTS = {
    "Caja": "1.01.01.00",
    "J.H. WILIAMS-RECAUD A LIQ": "1.03.02.00",
    "Cuenta Corriente a Cobrar-Gettel KIA": "1.02.01.00",
    "Venta Combustible": "4.01.00.00",
    "Venta Car Wash": "4.04.00.00",
    "Venta ICE": "4.05.00.00",
    "Venta C-Store": "4.09.00.00",
}
TOLERANCE = 0.005

SAME = "igual"
DIFFERENT = "distinto"
MISSING = "falta"
NO_LEDGER = "sin_mayor"


def _line(account, side, amount, label=None):
    """Un renglón esperado. Un importe negativo va del otro lado, como lo cargaría Ratio."""
    amount = round(amount or 0.0, 2)
    if amount < 0:
        side, amount = ("credit" if side == "debit" else "debit"), -amount
    return {"account": account, "name": ACCOUNTS.get(account, ""), "side": side, "amount": amount, "label": label}


def _entry(title, source, lines, must, must_not=()):
    return {"title": title, "source": source, "lines": [l for l in lines if l["amount"] > TOLERANCE],
            "must": tuple(must), "must_not": tuple(must_not)}


def cierre_entries(entries):
    """Los tres asientos de Controles → Cierre (control_cierre.build_month_entries)."""
    if not entries:
        return []
    titles = entries["titles"]
    sales = [_line(_CIERRE_ACCOUNTS[l["account"]], l["side"], l["amount"], l["account"]) for l in entries["lines"]]
    collected, tax = entries["collected"], entries["sale_tax"]["amount"]
    return [
        _entry(titles["sales"], "Cierre", sales, ("LIQUIDACION", "RECAUDACION"), ("COMISIONES", "LOTTERY")),
        _entry(titles["collected"], "Cierre",
               [_line("1.03.02.00", "debit", collected), _line("1.02.01.00", "credit", collected)], ("COBRO", "GETTEL")),
        _entry(titles["sale_tax"], "Cierre",
               [_line("5.13.00.00", "debit", tax), _line("2.02.01.00", "credit", tax)], ("SALE TAX",)),
    ]


def gettel_entries(account_month):
    """REBATE GETTEL del mes (cuenta_kia_toyota.single_account): rebate + CHARGE 3% a Otros ingresos combustible."""
    rebate = next((e for e in account_month.get("entries", []) if e["title"].startswith("REBATE")), None)
    if not rebate or not rebate["debit"]:
        return []
    return [_entry(rebate["title"], "Kia y Toyota",
                   [_line("1.02.01.00", "debit", rebate["debit"]), _line("4.07.00.00", "credit", rebate["debit"])],
                   ("GETTEL",), ("LIQUIDACION",))]


def lottery_entries(closing, year, month):
    """Los dos asientos de Lottery (lottery_db.compute_month_closing), un renglón por línea del Excel."""
    if not closing or not closing.get("has_data"):
        return []
    period = f"{month:02d}-{year}"
    return [
        _entry(f"LIQUIDACION CIERRE LOTTERY {period}", "Lottery", [
            _line("1.01.01.00", "debit", closing["caja_online"], "Caja-On Line"),
            _line("1.01.01.00", "debit", closing["caja_skoff"], "Caja-Skoff"),
            _line("5.04.00.00", "debit", closing["gastos_admin"], "Gastos Adminits-Loteria"),
            _line("2.01.16.00", "credit", closing["lottery_a_pagar_1"], "a Lottery a Pagar"),
            _line("2.01.16.00", "credit", closing["lottery_a_pagar_2"], "a Lottery a Pagar"),
        ], ("LIQUIDACION", "LOTTERY"), ("COMISIONES",)),
        _entry(f"LIQUIDACION CIERRE RECAUDACION COMISIONES {period}", "Lottery", [
            _line("2.01.16.00", "debit", closing["com_lottery_a_pagar"], "Lottery a Pagar"),
            _line("2.01.16.00", "debit", closing["com_lottery_a_pagar_online"], "Lottery a Pagar-Online"),
            _line("2.01.16.00", "debit", closing["com_lottery_a_pagar_skoff"], "Lottery a Pagar-skoff"),
            _line("1.01.02.00", "credit", closing["com_chase_bank"], "a Chase Bank"),
            _line("4.10.00.00", "credit", closing["com_comision_online"], "a Comision On-Line"),
            _line("4.10.00.00", "credit", closing["com_comision_skoff"], "a Comision Skoff"),
            _line("1.01.01.00", "credit", closing["com_caja"], "a Caja"),
        ], ("COMISIONES",)),
    ]


def loyalty_entries(loyalty, month_name, year):
    """INGRESOS LOYALTY CHEVRON del mes (control_tarjetas.loyalty_month)."""
    gross = (loyalty or {}).get("total", {}).get("gross") or 0.0
    if not gross:
        return []
    return [_entry(f"INGRESOS LOYALTY CHEVRON {month_name} {year}", "Tarjetas y Cupones",
                   [_line("1.03.02.00", "debit", gross), _line("4.11.00.00", "credit", gross)], ("LOYALTY",))]


def _matches(description, must, must_not):
    text = (description or "").upper()
    return all(word in text for word in must) and not any(word in text for word in must_not)


def compare(expected, lines_by_account, covered=()):
    """
    Cada asiento esperado contra Ratio. `lines_by_account` es {cuenta:
    [renglones del mayor en el mes]} y `covered` las cuentas con un mayor
    subido que cubre el mes. A cada renglón le agrega "status" (igual,
    distinto, falta o sin_mayor) y, si lo encontró, "ratio" (el renglón del
    mayor) y "diff" (Ratio − app). Un renglón de Ratio se usa una sola vez.
    """
    used = set()
    loaded = set(covered) | set(lines_by_account)
    results = []
    for entry in expected:
        lines = []
        for line in entry["lines"]:
            row = dict(line, status=NO_LEDGER, ratio=None, diff=None)
            lines.append(row)
            if line["account"] not in loaded:
                continue
            candidates = [(index, l) for index, l in enumerate(lines_by_account.get(line["account"], []))
                          if (line["account"], index) not in used and (l[line["side"]] or 0.0) > TOLERANCE]
            exact = [c for c in candidates if abs(c[1][line["side"]] - line["amount"]) < TOLERANCE]
            named = [c for c in candidates if _matches(c[1]["description"], entry["must"], entry["must_not"])]
            pick = next((c for c in exact if c in named), None) or (exact[0] if exact else None)
            if pick:
                row["status"] = SAME
            elif named:
                pick = min(named, key=lambda c: abs(c[1][line["side"]] - line["amount"]))
                row["status"] = DIFFERENT
            else:
                row["status"] = MISSING
                continue
            used.add((line["account"], pick[0]))
            row["ratio"] = pick[1]
            row["diff"] = round(pick[1][line["side"]] - line["amount"], 2)
        numbers = {l["ratio"]["asiento"] for l in lines if l["ratio"] and l["ratio"].get("asiento")
                   and _matches(l["ratio"]["description"], entry["must"], entry["must_not"])}
        results.append(dict(entry, lines=lines, numbers=sorted(numbers), extras=[]))
    # Lo que Ratio tiene en esos mismos asientos y la app no espera.
    for result in results:
        for account, account_lines in sorted(lines_by_account.items()):
            for index, l in enumerate(account_lines):
                if (account, index) not in used and l.get("asiento") in result["numbers"]:
                    used.add((account, index))
                    result["extras"].append(dict(l, account=account, name=ACCOUNTS.get(account, "")))
        statuses = {l["status"] for l in result["lines"]}
        if not result["lines"]:
            result["status"] = SAME
        elif statuses == {NO_LEDGER}:
            result["status"] = NO_LEDGER
        elif statuses <= {SAME, NO_LEDGER} and not result["extras"]:
            result["status"] = SAME if statuses == {SAME} else "parcial"
        else:
            result["status"] = DIFFERENT
    return results


def summary(results):
    """Cuántos asientos quedaron iguales, distintos y sin poder controlar."""
    counts = {SAME: 0, DIFFERENT: 0, "parcial": 0, NO_LEDGER: 0}
    for result in results:
        counts[result["status"]] += 1
    return counts


def needed_accounts(results):
    """Las cuentas cuyo mayor falta subir para controlar todo: [(código, nombre)]."""
    missing = {}
    for result in results:
        for line in result["lines"]:
            if line["status"] == NO_LEDGER:
                missing[line["account"]] = line["name"]
    return sorted(missing.items())
