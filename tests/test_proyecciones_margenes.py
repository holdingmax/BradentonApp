"""Proyecciones: los % teóricos valen desde el mes en que se cambian en adelante (2026-11-24)."""
import pytest

import config_store
import proyecciones


@pytest.fixture(autouse=True)
def _sin_margenes():
    config_store.write(config_store.PROYECCIONES_MARGENES, {})
    yield
    config_store.write(config_store.PROYECCIONES_MARGENES, {})


def test_sin_cambios_valen_los_del_excel():
    margins = proyecciones.margins_for_month(2026, 9)
    assert margins == proyecciones.DEFAULT_MARGINS


def test_un_cambio_vale_para_ese_mes_y_los_siguientes_no_los_anteriores():
    proyecciones.save_margins(2026, 10, {"TABACCO": 0.20, "LOTERY/LOTTO": 0.07})
    assert proyecciones.margins_for_month(2026, 9)["TABACCO"] == 0.15
    assert proyecciones.margins_for_month(2026, 9)["LOTERY/LOTTO"] == 0.06
    for year, month in ((2026, 10), (2026, 12), (2027, 3)):
        assert proyecciones.margins_for_month(year, month)["TABACCO"] == 0.20
        assert proyecciones.margins_for_month(year, month)["LOTERY/LOTTO"] == 0.07


def test_cambiar_un_mes_anterior_pisa_los_siguientes():
    proyecciones.save_margins(2026, 11, {"SODA": 0.50})
    proyecciones.save_margins(2026, 10, {"TABACCO": 0.20})
    nov = proyecciones.margins_for_month(2026, 11)
    assert nov["TABACCO"] == 0.20 and nov["SODA"] == 0.40
    assert proyecciones.margins_for_month(2026, 9)["TABACCO"] == 0.15


def test_kia_toyota_no_se_edita():
    proyecciones.save_margins(2026, 10, {"Gettel": 0.5})
    assert proyecciones.margins_for_month(2026, 10)["Gettel"] == 1.0
