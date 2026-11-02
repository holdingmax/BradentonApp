"""
Proyecciones (Reportes): el panel que miran los dueños día por día (pedido
del usuario, 2026-10-26; reemplaza a la página escondida del 2026-09-18).

Reproduce la hoja "Store Info" del Excel Cierre (filas Ventas / Margen /
Utilidad / Proyecciones) y el "PROYECTADO BRADENTON" del Drive:
  - Proyectado = vendido en los días cargados / días cargados * días del mes.
    El Excel usa siempre 30; acá van los días reales del mes.
  - Combustible: venta neta = Sales Fuel + Desc. Comb. El costo es el PPP de
    la hoja Físico: (inventario inicial + facturas de J.H.) / (galones
    iniciales + comprados), y la utilidad, venta - galones * PPP.
  - C-Store: venta de cada rubro * su % teórico (editables, los del Excel).

Control contra el Balance (hoja "BALANCE HISTORICO" del Bce): para un mes
cerrado se compara lo que proyectaba la app los días 7, 15 y 22 y a fin de mes
con lo real. Medido contra agosto 2026: "4.01 Ventas de combustible" es
Sales Fuel + Desc. Comb y "4.09 Ventas C-Store" son Tabacco + Soda + Beer/Wine
+ Resto (Lotto y Kia/Toyota van por otras cuentas), al centavo.
"""

import calendar
import datetime as dt
import os

import config_store
import fisico
import fisico_db
import lottery_db
import reportes_db
from reporte_diario import DEPARTMENT_GROUPS, group_department_sales

# Los % teóricos del Excel Cierre (fila Margen, abajo de cada rubro). Son el
# default; el que se usa sale de reportes_db.get_department_margins.
DEFAULT_MARGINS = {
    "TABACCO": 0.15,
    "SODA": 0.40,
    "BEER/WINE": 0.25,
    "LOTERY/LOTTO": 0.06,
    "Gettel": 1.00,
    "RESTO": 0.40,
}

CATEGORY_DISPLAY_LABELS = {
    "TABACCO": "Tabacco",
    "SODA": "Soda",
    "BEER/WINE": "Beer / Wine",
    "LOTERY/LOTTO": "Lotto",
    "Gettel": "Kia / Toyota",
    "RESTO": "Resto",
}

# Los rubros que el Balance cuenta como "Ventas C-Store".
BALANCE_CSTORE = ("TABACCO", "SODA", "BEER/WINE", "RESTO")
CHECKPOINT_DAYS = (7, 15, 22)


def _project(actual, days_loaded, days_in_month):
    if not days_loaded or actual is None:
        return None
    return actual * days_in_month / days_loaded


def _ratio(num, den):
    return num / den if (num is not None and den) else None


def _ppp(year, month, upto=None):
    """
    Precio promedio ponderado del combustible: el inventario inicial de la hoja
    Físico más las facturas de J.H. del mes (hasta `upto`, para proyectar como
    se veía ese día). None si todavía no hay galones.
    """
    gallons, amount, _source = fisico._resolve_initial(year, month)
    invoices = fisico_db.get_month_invoices(year, month)
    if upto is not None:
        invoices = [inv for inv in invoices if (inv.get("invoice_date") or "") <= upto.isoformat()]
    gallons += sum(inv.get("gallons") or 0.0 for inv in invoices)
    amount += sum(inv.get("amount") or 0.0 for inv in invoices)
    return (amount / gallons if gallons else None), len(invoices)


def lottery_commissions(year, month, pos_proj=None, margin=None):
    """
    La ganancia de Lottery del mes: las comisiones (On-Line + Skoff) de las
    semanas que se pagan a Chase en el mes, igual que el TOTAL DEL MES del
    Excel de Lottery y la cuenta 4.10 del Bce (pedido del usuario,
    2026-11-02). La última semana de un mes que se paga en el siguiente cuenta
    en el siguiente. Proyectado: al ritmo de los días con comisión cargada,
    hasta completar las semanas del mes. Sin ningún día cargado, la venta de
    LOTTO del POS proyectada por el % teórico (`pos_proj` * `margin`).

    Medido del 27/07 al 11/10/2026: la comisión fue el 5,97 % de lo vendido
    (por semana, de 3 % a 7 %, porque la de Skoff se cobra sobre los libros
    liquidados, no sobre la venta).
    """
    days = [d for block in lottery_db.blocks_paid_in_month(year, month) for d in block["days"]]
    loaded = [d for d in days if d.get("total_comm") is not None or d.get("sales_comm") is not None]
    comm = -sum((d.get("total_comm") or 0.0) + (d.get("sales_comm") or 0.0) for d in loaded)
    sales = sum((d.get("sales") or 0.0) + (d.get("skoff_net_sales") or 0.0) for d in loaded)
    if loaded:
        comm_proj = comm * len(days) / len(loaded)
    else:
        comm_proj = pos_proj * margin if (pos_proj is not None and margin is not None) else None
    return {
        "first": dt.date.fromisoformat(days[0]["date"]) if days else None,
        "last": dt.date.fromisoformat(days[-1]["date"]) if days else None,
        "days_total": len(days),
        "days_loaded": len(loaded),
        "comm": comm if loaded else None,
        "comm_proj": comm_proj,
        "sales": sales if loaded else None,
        "rate": _ratio(comm, sales) if loaded else None,
        "estimated": not loaded,
    }


def _month_days(year, month):
    """Un renglón por día cargado: combustible y C-Store por rubro."""
    store = {r["date"]: r for r in reportes_db.get_month_store_info(year, month) if r.get("volume") is not None}
    departments = reportes_db.get_month_departments_by_date(year, month)
    days = []
    for date_key in sorted(store):
        row = store[date_key]
        groups, _ = group_department_sales(departments.get(date_key, []))
        by_group = {g["label"]: g.get("amount") or 0.0 for g in groups}
        fuel_sales = (row.get("sales_fuel") or 0.0) + (row.get("desc_comb") or 0.0)
        cstore = sum(by_group.get(label, 0.0) for label in BALANCE_CSTORE)
        days.append({
            "date": dt.date.fromisoformat(date_key),
            "gallons": row.get("volume") or 0.0,
            "fuel_sales": fuel_sales,
            "price": _ratio(fuel_sales, row.get("volume")),
            "groups": by_group,
            "cstore": cstore,
            "lotto": by_group.get("LOTERY/LOTTO", 0.0),
            "kia": by_group.get("Gettel", 0.0),
            "total": fuel_sales + cstore,
        })
    return days


def _summary(year, month, days, margins, upto=None):
    """Vendido y proyectado con los días hasta `upto` (todos si es None)."""
    days_in_month = calendar.monthrange(year, month)[1]
    if upto is not None:
        days = [d for d in days if d["date"] <= upto]
    loaded = len(days)
    gallons = sum(d["gallons"] for d in days)
    fuel_sales = sum(d["fuel_sales"] for d in days)
    ppp, invoices = _ppp(year, month, upto)

    def fuel_block(gal, sales):
        if gal is None or sales is None:
            return {"gallons": None, "sales": None, "price": None, "cost": None, "margin_gal": None, "utilidad": None}
        cost = gal * ppp if ppp is not None else None
        price = _ratio(sales, gal)
        return {
            "gallons": gal,
            "sales": sales,
            "price": price,
            "cost": cost,
            "margin_gal": (price - ppp) if (price is not None and ppp is not None) else None,
            "utilidad": (sales - cost) if cost is not None else None,
        }

    rubros = []
    for label, _members in DEPARTMENT_GROUPS:
        amount = sum(d["groups"].get(label, 0.0) for d in days)
        margin = margins.get(label, DEFAULT_MARGINS.get(label, 0.0))
        proj = _project(amount, loaded, days_in_month)
        rubros.append({
            "label": label,
            "display": CATEGORY_DISPLAY_LABELS.get(label, label),
            "in_balance": label in BALANCE_CSTORE,
            "margin": margin,
            "amount": amount,
            "utilidad": amount * margin,
            "proj": proj,
            "utilidad_proj": proj * margin if proj is not None else None,
        })

    def totals(items, key):
        values = [r[key] for r in items]
        return None if any(v is None for v in values) else sum(values)

    balance_rubros = [r for r in rubros if r["in_balance"]]
    cstore = {
        "rubros": rubros,
        "total": {k: totals(rubros, k) for k in ("amount", "utilidad", "proj", "utilidad_proj")},
        "balance": {k: totals(balance_rubros, k) for k in ("amount", "utilidad", "proj", "utilidad_proj")},
    }
    for block in (cstore["total"], cstore["balance"]):
        block["margin"] = _ratio(block["utilidad"], block["amount"])
        block["margin_proj"] = _ratio(block["utilidad_proj"], block["proj"])

    return {
        "days_in_month": days_in_month,
        "days_loaded": loaded,
        "last_day": days[-1]["date"] if days else None,
        "ppp": ppp,
        "fuel_invoices": invoices,
        "fuel": fuel_block(gallons, fuel_sales) if loaded else fuel_block(None, None),
        "fuel_proj": fuel_block(_project(gallons, loaded, days_in_month), _project(fuel_sales, loaded, days_in_month)),
        "cstore": cstore,
    }


def build_projection(year, month):
    """Todo lo que muestra la página de Proyecciones para un mes."""
    margins = reportes_db.get_department_margins(DEFAULT_MARGINS)
    days = _month_days(year, month)
    result = _summary(year, month, days, margins)
    result.update({"year": year, "month": month, "days": days, "balance": None})
    # Cada día con el PPP del mes, como la hoja Físico: la suma de los días da
    # la utilidad del mes.
    ppp = result["ppp"]
    for d in days:
        d["margin_gal"] = (d["price"] - ppp) if (d["price"] is not None and ppp is not None) else None
        d["fuel_utilidad"] = (d["fuel_sales"] - d["gallons"] * ppp) if ppp is not None else None
        d["cstore_utilidad"] = sum(d["groups"].get(label, 0.0) * margins.get(label, DEFAULT_MARGINS[label])
                                   for label in BALANCE_CSTORE)
    totals = {key: sum(d[key] for d in days)
              for key in ("gallons", "fuel_sales", "cstore", "cstore_utilidad", "lotto", "kia", "total")}
    totals["groups"] = {label: sum(d["groups"].get(label, 0.0) for d in days) for label, _members in DEPARTMENT_GROUPS}
    totals["price"] = _ratio(totals["fuel_sales"], totals["gallons"])
    totals["margin_gal"] = (totals["price"] - ppp) if (totals["price"] is not None and ppp is not None) else None
    totals["fuel_utilidad"] = (totals["fuel_sales"] - totals["gallons"] * ppp) if ppp is not None else None
    result["days_totals"] = totals
    gallons, amount, source = fisico._resolve_initial(year, month)
    result["fuel_initial"] = {"gallons": gallons, "amount": amount, "source": source}

    _add_lottery(year, month, result, margins)
    result["split"] = gross_split(result)

    real = balance_for_month(year, month)
    if real:
        result["balance"] = _balance_control(year, month, days, margins, real)
    result["charts"] = _chart_data(year, month, result)
    return result


def _add_lottery(year, month, result, margins):
    """
    Lottery deja de ir por % teórico: su utilidad es la comisión real de las
    semanas que se pagan en el mes (lottery_commissions), y el C-Store suma
    esa comisión, como la Nota 3 del Bce. Kia / Toyota no es ganancia (es
    combustible a cuenta): queda afuera.
    """
    cstore = result["cstore"]
    lotto = next(r for r in cstore["rubros"] if r["label"] == "LOTERY/LOTTO")
    lot = lottery_commissions(year, month, lotto["proj"], lotto["margin"])
    lotto.update({"lottery": True, "utilidad": lot["comm"], "utilidad_proj": lot["comm_proj"], "margin": lot["rate"]})
    result["lottery"] = lot

    def plus(a, b):
        return (a or 0.0) + (b or 0.0) if (a is not None or b is not None) else None

    base = cstore["balance"]
    with_lottery = {
        "amount": plus(base["amount"], lotto["amount"]),
        "utilidad": plus(base["utilidad"], lot["comm"]),
        "proj": plus(base["proj"], lotto["proj"]),
        "utilidad_proj": plus(base["utilidad_proj"], lot["comm_proj"]),
    }
    with_lottery["margin"] = _ratio(with_lottery["utilidad"], with_lottery["amount"])
    with_lottery["margin_proj"] = _ratio(with_lottery["utilidad_proj"], with_lottery["proj"])
    cstore["with_lottery"] = with_lottery


def gross_split(result):
    """
    Qué parte de la utilidad bruta proyectada pone cada cosa (pedido del
    usuario, 2026-11-02): el combustible solo, y el C-Store abierto en sus
    rubros más la comisión de Lottery. None sin PPP.
    """
    fuel = result["fuel_proj"]["utilidad"]
    if fuel is None:
        return None
    rubros = result["cstore"]["rubros"]
    parts = [{"key": r["label"], "label": r["display"] if not r.get("lottery") else "Lottery",
              "value": r["utilidad_proj"] or 0.0}
             for r in [r for r in rubros if r["in_balance"]] + [r for r in rubros if r.get("lottery")]]
    cstore = sum(p["value"] for p in parts)
    total = fuel + cstore
    for p in parts:
        p["pct_total"] = _ratio(p["value"], total)
        p["pct_cstore"] = _ratio(p["value"], cstore)
    return {
        "total": total,
        "fuel": {"value": fuel, "pct_total": _ratio(fuel, total)},
        "cstore": {"value": cstore, "pct_total": _ratio(cstore, total), "parts": parts},
    }


def _chart_data(year, month, result):
    """
    Lo que usan los gráficos de la página: la utilidad bruta proyectada por
    unidad. El gráfico del mes día por día y los de mes a mes (por galón,
    galones y ventas) se sacaron el 2026-11-02, a pedido del usuario.
    """
    return {"split": result["split"]}


# --- Control contra el Balance ----------------------------------------------

_BALANCE_ROWS = {
    "galones vendidos": "galones",
    "4.01.00.00 - ventas de combustible": "ventas_combustible",
    "4.09.00.00 - ventas c-store": "ventas_cstore",
    "5.02.00.00 - costo de ventas de combustible": "costo_combustible",
    "5.15.00.00 - costo de venta c-store": "costo_cstore",
    # Para el desglose de la utilidad neta (pedido del usuario, 2026-11-02).
    "4.10.00.00 - comisiones lottery": "comisiones_lottery",
    "4.05.00.00 - ventas ice machine": "ventas_ice",
    "4.06.00.00 - alquiler food truck": "alquiler_food_truck",
    "4.04.00.00 - ventas carwash/vaccum": "ventas_vaccum",
    "4.03.00.00 - otros ingresos y descuentos": "otros_ingresos",
    "4.07.00.00 - otros ingresos combustible": "otros_ingresos_combustible",
    "5.04.00.00 - gastos de tarjeta de credito": "gastos_tarjeta",
}
# El "ESTADO RESULTADO" de arriba de la hoja: los gastos vienen en negativo.
_RESULTADO_ROWS = {
    "ganancia bruta": "ganancia_bruta",
    "sueldos y jornales": "sueldos",
    "comisiones y gastos bancarios": "gastos_bancarios",
    "energia electrica": "energia",
    "water and waste services": "agua",
    "intereses y recargos": "intereses",
    "seguro": "seguro",
    "reparacion y mantenimientos": "reparaciones",
    "administration fees": "adm_fees",
    "otros gastos varios": "otros_gastos_varios",
    "gastos directos": "gastos_directos",
    "otros": "otros_gastos",
    "resultado neto": "resultado_neto",
}
BALANCE_FIELDS = tuple(_BALANCE_ROWS.values()) + tuple(_RESULTADO_ROWS.values())


def read_balance_workbook(path):
    """
    {"AAAA-MM": {campo: valor}} de la hoja "BALANCE HISTORICO" de un Bce (los
    campos de BALANCE_FIELDS): una columna por mes, con las fechas en el
    renglón "Nota 1 : Ventas", y el "ESTADO RESULTADO" con sus propias fechas.
    Solo los meses con al menos la venta de combustible; un dato vacío queda
    en None.
    """
    import openpyxl

    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        if "BALANCE HISTORICO" not in workbook.sheetnames:
            raise ValueError("El archivo no tiene la hoja \"BALANCE HISTORICO\": ¿es el Bce del mes?")
        rows = list(workbook["BALANCE HISTORICO"].iter_rows(values_only=True))
    finally:
        workbook.close()

    header = next((i for i, row in enumerate(rows)
                   if row and isinstance(row[0], str) and row[0].strip().lower().startswith("nota 1")), None)
    if header is None:
        raise ValueError("No encontré el renglón \"Nota 1 : Ventas\" con los meses en BALANCE HISTORICO.")
    def label_of(row):
        return row[0].strip().lower() if row and isinstance(row[0], str) else ""

    def month_columns(row):
        return {col: f"{value.year:04d}-{value.month:02d}"
                for col, value in enumerate(row) if isinstance(value, (dt.date, dt.datetime))}

    def first_rows(section, names):
        # La primera aparición de cada renglón (más abajo se repiten).
        found = {}
        for row in section:
            field = names.get(label_of(row))
            if field and field not in found:
                found[field] = row
        return found

    months = month_columns(rows[header])
    found = first_rows(rows[header + 1:], _BALANCE_ROWS)
    if "ventas_combustible" not in found:
        raise ValueError("No encontré \"4.01.00.00 - VENTAS DE COMBUSTIBLE\" en BALANCE HISTORICO.")
    # El Estado de Resultado va arriba de la Nota 1. Si falta, esos campos quedan en None.
    results_header = next((i for i, row in enumerate(rows[:header]) if label_of(row) == "estado resultado"), None)
    sections = [(months, found)]
    if results_header is not None:
        sections.append((month_columns(rows[results_header]),
                         first_rows(rows[results_header + 1:header], _RESULTADO_ROWS)))

    result = {}
    for key in sorted(set(months.values())):
        values = dict.fromkeys(BALANCE_FIELDS)
        for columns, rows_by_field in sections:
            col = next((c for c, k in columns.items() if k == key), None)
            if col is None:
                continue
            for field, row in rows_by_field.items():
                value = row[col] if col < len(row) else None
                values[field] = round(float(value), 2) if isinstance(value, (int, float)) else None
        if values["ventas_combustible"]:
            result[key] = values
    if not result:
        raise ValueError("El Balance no tiene ningún mes con ventas de combustible.")
    return result


def save_balance(months, filename):
    """
    Guarda los meses leídos. Un mes ya guardado solo se reemplaza si el archivo
    nuevo lo trae igual o más completo (el Bce de septiembre trae agosto
    completo y septiembre a medio cargar). Devuelve (guardados, salteados).
    """
    stored = config_store.read(config_store.PROYECCIONES_BALANCE) or {}
    saved, skipped = [], []
    for key, values in sorted(months.items()):
        filled = sum(1 for f in BALANCE_FIELDS if values.get(f) is not None)
        previous = stored.get(key) or {}
        if sum(1 for f in BALANCE_FIELDS if previous.get(f) is not None) > filled:
            skipped.append(key)
            continue
        stored[key] = dict(values, archivo=os.path.basename(filename),
                           cargado=dt.datetime.now().isoformat(timespec="seconds"))
        saved.append(key)
    config_store.write(config_store.PROYECCIONES_BALANCE, stored)
    return saved, skipped


def balance_for_month(year, month):
    stored = config_store.read(config_store.PROYECCIONES_BALANCE) or {}
    return stored.get(f"{year:04d}-{month:02d}")


def _balance_control(year, month, days, margins, real):
    """Lo real del Balance contra la proyección de los días 7, 15, 22 y fin de mes."""
    def utilidad(sales, cost):
        return (sales - cost) if (sales is not None and cost is not None) else None

    real_rows = {
        "galones": real.get("galones"),
        "ventas_combustible": real.get("ventas_combustible"),
        "utilidad_combustible": utilidad(real.get("ventas_combustible"), real.get("costo_combustible")),
        "ventas_cstore": real.get("ventas_cstore"),
        "utilidad_cstore": utilidad(real.get("ventas_cstore"), real.get("costo_cstore")),
    }
    days_in_month = calendar.monthrange(year, month)[1]
    cuts = [(f"Día {d}", dt.date(year, month, d)) for d in CHECKPOINT_DAYS if d < days_in_month]
    cuts.append(("Fin de mes", dt.date(year, month, days_in_month)))
    checkpoints = []
    for label, cut in cuts:
        summary = _summary(year, month, days, margins, upto=cut)
        if not summary["days_loaded"]:
            continue
        proj = {
            "galones": summary["fuel_proj"]["gallons"],
            "ventas_combustible": summary["fuel_proj"]["sales"],
            "utilidad_combustible": summary["fuel_proj"]["utilidad"],
            "ventas_cstore": summary["cstore"]["balance"]["proj"],
            "utilidad_cstore": summary["cstore"]["balance"]["utilidad_proj"],
        }
        checkpoints.append({
            "label": label,
            "days_loaded": summary["days_loaded"],
            "proj": proj,
            # Redondeado y + 0.0: que una coincidencia exacta no salga "-0.0 %".
            "deviation": {k: round(_ratio(proj[k] - real_rows[k], real_rows[k]), 4) + 0.0
                          if (proj[k] is not None and real_rows[k]) else None
                          for k in proj},
        })
    rows = [
        ("galones", "Galones vendidos", "gal"),
        ("ventas_combustible", "Venta de combustible", "$"),
        ("utilidad_combustible", "Utilidad de combustible", "$"),
        ("ventas_cstore", "Venta C-Store (sin Lotto ni Kia/Toyota)", "$"),
        ("utilidad_cstore", "Utilidad C-Store", "$"),
    ]
    return {
        "real": real_rows,
        "source": real.get("archivo"),
        "loaded_at": real.get("cargado"),
        "rows": rows,
        "checkpoints": checkpoints,
        "cstore_margin_real": _ratio(real_rows["utilidad_cstore"], real_rows["ventas_cstore"]),
        "fuel_margin_gal_real": _ratio(real_rows["utilidad_combustible"], real_rows["galones"]),
    }
