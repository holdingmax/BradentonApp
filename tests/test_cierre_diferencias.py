"""Control Cierre: la diferencia del asiento marcada como revisada o reclasificada (2026-11-24)."""
import copy

import pytest

import control_asientos
import control_cierre


def _entries(year=2026, month=10, difference=70.0):
    """Un asiento mínimo con `difference` = Debe − Haber, como build_month_entries."""
    lines = [
        {"side": "debit", "account": control_cierre.ACCOUNT_CAJA, "amount": 1000.0 + difference},
        {"side": "debit", "account": control_cierre.ACCOUNT_JH, "amount": 500.0},
        {"side": "credit", "account": "Venta Combustible", "amount": 900.0},
        {"side": "credit", "account": "Venta C-Store", "amount": 600.0},
    ]
    debit = sum(l["amount"] for l in lines if l["side"] == "debit")
    credit = sum(l["amount"] for l in lines if l["side"] == "credit")
    return {"year": year, "month": month, "lines": lines, "debit_total": debit, "credit_total": credit,
            "difference": round(debit - credit, 2), "debit_with_lottery": 1070.0, "credit_with_lottery": 1500.0,
            "missing_departments": [], "missing_store_info": [], "empty_fields": [],
            "titles": control_cierre.entry_titles(year, month), "collected": 0.0,
            "sale_tax": {"amount": 0.0, "date": f"{year}-{month:02d}-31", "empty_days": []}}


@pytest.fixture(autouse=True)
def _sin_marcas():
    control_cierre.set_difference_record(2026, 10)
    control_cierre.set_difference_record(2026, 11)
    yield
    control_cierre.set_difference_record(2026, 10)
    control_cierre.set_difference_record(2026, 11)


def test_reclasificar_suma_el_renglon_y_el_asiento_cierra():
    control_cierre.set_difference_record(2026, 10, "reclasificar", 70.0, "2.01.99.00 - PRUEBA", "prepago del 04/10")
    entries = control_cierre.apply_difference_record(_entries())
    assert entries["difference"] == 0.0
    added = [l for l in entries["lines"] if l.get("adjusted")]
    assert len(added) == 1 and added[0]["side"] == "credit" and added[0]["amount"] == 70.0
    assert added[0]["code"] == "2.01.99.00"
    ok, title, _ = control_cierre.status_summary(entries, ())
    assert ok and "reclasificada" in title
    # Asientos de Ratio: el renglón va con su código.
    sales = control_asientos.cierre_entries(entries)[0]["lines"]
    assert any(l["account"] == "2.01.99.00" and l["side"] == "credit" for l in sales)


def test_diferencia_negativa_va_al_debe():
    control_cierre.set_difference_record(2026, 10, "reclasificar", -70.0, "Caja chica")
    entries = control_cierre.apply_difference_record(_entries(difference=-70.0))
    added = next(l for l in entries["lines"] if l.get("adjusted"))
    assert added["side"] == "debit" and entries["difference"] == 0.0
    # Va con los otros renglones del Debe, antes del primero del Haber.
    sides = [l["side"] for l in entries["lines"]]
    assert sides == sorted(sides, key=lambda s: s != "debit")


def test_revisada_deja_el_asiento_igual_pero_en_verde():
    control_cierre.set_difference_record(2026, 10, "revisada", 70.0, note="prepago")
    entries = control_cierre.apply_difference_record(_entries())
    assert entries["difference"] == 70.0 and not any(l.get("adjusted") for l in entries["lines"])
    assert control_cierre.difference_explained(entries)
    ok, title, _ = control_cierre.status_summary(entries, ())
    assert ok and title.startswith("Diferencia revisada")


def test_si_la_diferencia_cambia_la_marca_deja_de_valer():
    control_cierre.set_difference_record(2026, 10, "reclasificar", 70.0, "2.01.99.00 - PRUEBA")
    original = _entries(difference=95.0)
    entries = control_cierre.apply_difference_record(copy.deepcopy(original))
    assert entries["adjustment"]["stale"]
    assert entries["difference"] == 95.0 and len(entries["lines"]) == len(original["lines"])
    ok, title, paragraphs = control_cierre.status_summary(entries, ())
    assert not ok and "no cierra" in title and any("cambió" in p for p in paragraphs)


def test_otro_mes_no_se_toca_y_se_puede_borrar():
    control_cierre.set_difference_record(2026, 11, "revisada", 70.0)
    assert "adjustment" not in control_cierre.apply_difference_record(_entries())
    control_cierre.set_difference_record(2026, 11)
    assert control_cierre.difference_record(2026, 11) is None


@pytest.mark.parametrize("kind, amount, account", [("otra", 70.0, "x"), ("revisada", 0.0, ""), ("reclasificar", 70.0, " ")])
def test_datos_invalidos(kind, amount, account):
    with pytest.raises(ValueError):
        control_cierre.set_difference_record(2026, 10, kind, amount, account)
