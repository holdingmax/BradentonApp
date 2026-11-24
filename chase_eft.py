"""
Los EFT de J.H. en Chase con su número de RCV (pedido del usuario,
2026-11-24: "que se categoricen solo los EFT una vez lleguen, así no quedan
categorizados como EFT- y nada más").

La regla de Chase deja cada pago de J.H. como "EFT RCV-". Cuando el EFT de
ese pago está cargado en Carga de Datos → EFT y Cupones, el movimiento pasa
a "EFT RCV-23174": mismo criterio que el cruce de jh_mensual (el importe de
Chase es lo que J.H. debita o acredita, facturas pagadas menos cupones, y la
fecha está a 3 días o menos de la del EFT). Queda con detalle_source="eft",
que ni una recarga del extracto ni un cambio de reglas pisan; si el EFT se
borra, el movimiento vuelve a "EFT RCV-".
"""

from datetime import date, datetime

import chase_db
import eft_db

EFT_PREFIX = "EFT RCV-"
WINDOW_DAYS = 3


def _loaded_efts():
    """[{rcv, date, draft}] de todos los EFT cargados (draft = facturas − cupones)."""
    months = set()
    for dep in eft_db.list_all_deposits():
        try:
            day = datetime.strptime(dep["eft_date"], "%m/%d/%Y").date()
        except (TypeError, ValueError):
            continue
        months.add((day.year, day.month))
    result = []
    for year, month in sorted(months):
        for item in eft_db.get_month_deposits(year, month):
            dep = item["deposit"]
            try:
                day = datetime.strptime(dep["eft_date"], "%m/%d/%Y").date()
            except (TypeError, ValueError):
                continue
            invoices = sum(p["paid_amount"] or 0.0 for p in item["paid_invoices"])
            coupons = sum(c["paid_amount"] or 0.0 for c in item["coupons"])
            result.append({"rcv": (dep["rcv_number"] or "").strip(), "date": day, "draft": round(invoices - coupons, 2)})
    return result


def _detalle_for(rcv):
    rcv = rcv.upper()
    if rcv.startswith("RCV-"):
        rcv = rcv[4:]
    return EFT_PREFIX + rcv


def link_eft_rcv():
    """Pone el N° de RCV a los "EFT RCV-" de Chase con su EFT cargado. Devuelve cuántos cambió."""
    rows = chase_db.eft_rows(EFT_PREFIX)
    if not rows:
        return 0
    efts = _loaded_efts()
    used = set()
    wanted = {}
    # Primero los que ya tienen su RCV y siguen valiendo, así no se los roba otro.
    for eft in efts:
        target = _detalle_for(eft["rcv"])
        for row in rows:
            if row["_rowid"] in used or row["detalle"] != target:
                continue
            day = date.fromisoformat(str(row["posting_date"])[:10])
            if abs((day - eft["date"]).days) <= WINDOW_DAYS and abs(-row["amount"] - eft["draft"]) < 0.005:
                used.add(row["_rowid"])
                wanted[row["_rowid"]] = target
                eft["done"] = True
                break
    for eft in efts:
        if eft.get("done"):
            continue
        best = None
        for row in rows:
            if row["_rowid"] in used or abs(-row["amount"] - eft["draft"]) >= 0.005:
                continue
            gap = abs((date.fromisoformat(str(row["posting_date"])[:10]) - eft["date"]).days)
            if gap <= WINDOW_DAYS and (best is None or gap < best[0]):
                best = (gap, row)
        if best:
            used.add(best[1]["_rowid"])
            wanted[best[1]["_rowid"]] = _detalle_for(eft["rcv"])
    changed = 0
    for row in rows:
        target = wanted.get(row["_rowid"])
        if target:
            if row["detalle"] != target or row["detalle_source"] != "eft":
                chase_db.set_eft_detalle(row["_rowid"], target, "eft")
                changed += 1
        elif row["detalle_source"] == "eft":
            # Su EFT ya no está cargado: vuelve a lo de la regla.
            chase_db.set_eft_detalle(row["_rowid"], EFT_PREFIX, "rule")
            changed += 1
    return changed
