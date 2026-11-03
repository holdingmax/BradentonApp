"""
Control de combustible (Controles, pedido del usuario, 2026-10-26).

Con lo ya cargado:
  - Galones: stock teórico día por día (inventario inicial de Físico +
    facturas de J.H. recibidas - galones vendidos de Store Info), y la medición
    del mes contra el teórico del día en que se midió (Físico la compara con el
    de fin de mes). Se toma la medición como al cierre de ese día.
  - Precio y margen por galón: el precio de venta de cada día contra el PPP del
    mes (el de Físico y Proyecciones) y contra el costo de la última factura
    recibida hasta ese día (reposición).
  - El cuadro de la hoja Fisico del Cierre (`sheet`, fisico.build_month_report).
Las facturas contra los EFT salieron el 2026-11-03 (pedido del usuario).
"""

import calendar
import datetime as dt

import fisico
import fisico_db
import reportes_db


def _ratio(num, den):
    return num / den if (num is not None and den) else None


def _cost_per_gallon(invoice):
    return _ratio(invoice.get("amount"), invoice.get("gallons"))


def _last_purchase_before(day, all_invoices):
    """La última factura recibida hasta `day` inclusive (puede ser del mes anterior)."""
    best = None
    for inv in all_invoices:
        if inv["invoice_date"] <= day.isoformat() and inv.get("gallons"):
            if best is None or (inv["invoice_date"], inv["id"]) > (best["invoice_date"], best["id"]):
                best = inv
    return best


def build_control(year, month):
    report = fisico.build_month_report(year, month)
    ppp = _ratio(report["total_amount"], report["total_gallons"])
    invoices = fisico_db.get_month_invoices(year, month)
    all_invoices = fisico_db.list_all_invoices()
    store = {r["date"]: r for r in reportes_db.get_month_store_info(year, month)}

    received = {}
    for inv in invoices:
        received.setdefault(inv["invoice_date"], []).append(inv)

    days_in_month = calendar.monthrange(year, month)[1]
    stock = report["initial_gallons"] or 0.0
    days, last_loaded = [], None
    for day_number in range(1, days_in_month + 1):
        day = dt.date(year, month, day_number)
        key = day.isoformat()
        row = store.get(key) or {}
        sold = row.get("volume")
        in_gallons = sum(inv["gallons"] or 0.0 for inv in received.get(key, []))
        if sold is None and not in_gallons:
            continue
        if sold is not None:
            last_loaded = day
        stock = stock + in_gallons - (sold or 0.0)
        fuel_sales = (row.get("sales_fuel") or 0.0) + (row.get("desc_comb") or 0.0) if sold is not None else None
        price = _ratio(fuel_sales, sold)
        last = _last_purchase_before(day, all_invoices)
        last_cost = _cost_per_gallon(last) if last else None
        days.append({
            "date": day,
            "received": in_gallons,
            "invoices": [inv["invoice_number"] for inv in received.get(key, [])],
            "sold": sold,
            "stock": round(stock, 2),
            "fuel_sales": fuel_sales,
            "price": price,
            "margin_ppp": (price - ppp) if (price is not None and ppp is not None) else None,
            "last_cost": last_cost,
            "last_number": last["invoice_number"] if last else None,
            "margin_last": (price - last_cost) if (price is not None and last_cost is not None) else None,
        })

    sold_total = sum(d["sold"] or 0.0 for d in days)
    sales_total = sum(d["fuel_sales"] or 0.0 for d in days)
    price_month = _ratio(sales_total, sold_total)
    # El margen del mes contra la última compra pesa cada día por sus galones.
    weighted = [(d["margin_last"], d["sold"]) for d in days if d["margin_last"] is not None and d["sold"]]
    margin_last_month = _ratio(sum(m * g for m, g in weighted), sum(g for _, g in weighted))
    # Para explicar el margen con última compra (pedido del usuario,
    # 2026-11-03): qué factura se usó de costo y en cuántos días.
    last_costs = []
    for d in days:
        if d["last_cost"] is None or not d["sold"]:
            continue
        if last_costs and last_costs[-1]["number"] == d["last_number"]:
            entry = last_costs[-1]
        else:
            entry = {"number": d["last_number"], "cost": d["last_cost"], "from": d["date"], "sold": 0.0, "sales": 0.0}
            last_costs.append(entry)
        entry["to"] = d["date"]
        entry["sold"] += d["sold"]
        entry["sales"] += d["fuel_sales"] or 0.0
    for entry in last_costs:
        entry["price"] = _ratio(entry["sales"], entry["sold"])
        entry["margin"] = entry["price"] - entry["cost"] if entry["price"] is not None else None

    final_stock = days[-1]["stock"] if days else round(report["initial_gallons"] or 0.0, 2)

    reading = None
    if report["real_gallons"] is not None:
        reading_date = dt.date.fromisoformat(report["real_reading_date"]) if report["real_reading_date"] else None
        month_end = dt.date(year, month, days_in_month)
        # Una medición sin fecha o de después del mes (ej. el 1° del siguiente) va contra el fin de mes.
        cut = reading_date if (reading_date and reading_date <= month_end) else month_end
        at_cut = [d for d in days if cut and d["date"] <= cut]
        theoretical = at_cut[-1]["stock"] if at_cut else round(report["initial_gallons"] or 0.0, 2)
        # Teórico menos real, como la hoja Fisico (positivo = falta combustible).
        diff = round(theoretical - report["real_gallons"], 2)
        reading = {
            "date": reading_date,
            "cut": cut,
            "gallons": report["real_gallons"],
            "theoretical": theoretical,
            "diff": diff,
            "diff_pct": _ratio(diff, theoretical),
            "diff_amount": diff * ppp if ppp is not None else None,
            # La medición es de un día sin el Close Store cargado: el teórico queda corto.
            "incomplete": last_loaded is None or cut > last_loaded,
        }

    return {
        "year": year,
        "month": month,
        "initial_gallons": report["initial_gallons"],
        "initial_amount": report["initial_amount"],
        "initial_source": report["initial_source"],
        "received_gallons": report["invoices_gallons"],
        "received_amount": report["invoices_amount"],
        "received_count": len(invoices),
        "total_gallons": report["total_gallons"],
        "total_amount": report["total_amount"],
        "sold_gallons": round(sold_total, 2),
        "fuel_sales": round(sales_total, 2),
        "last_costs": last_costs,
        "final_stock": final_stock,
        # El valor del stock teórico al PPP, como el Inventario Final Teórico de Físico.
        "final_value": round(final_stock * ppp, 2) if ppp is not None else None,
        "last_loaded": last_loaded,
        "ppp": ppp,
        "price": price_month,
        "margin_ppp": (price_month - ppp) if (price_month is not None and ppp is not None) else None,
        "margin_last": margin_last_month,
        "days": days,
        "reading": reading,
        # El cuadro como la hoja Fisico del Cierre (pedido del usuario, 2026-11-03).
        "sheet": report,
    }
