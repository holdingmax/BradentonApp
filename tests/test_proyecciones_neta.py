"""Utilidad neta: mínimo de días antes de proyectar al ritmo y semáforo de las fuentes (2026-11-24)."""
import datetime as dt

import proyecciones_neta as N


def test_con_pocos_dias_no_se_multiplica():
    # 6 días con $4,407: antes daba $22,036; ahora, el mayor con el mes anterior.
    assert N._at_pace(4407.0, 6, 30, 4126.0) == 4407.0
    assert N._at_pace(1000.0, 6, 30, 4126.0) == 4126.0


def test_con_el_minimo_de_dias_va_al_ritmo():
    assert N._at_pace(1500.0, N.MIN_DAYS, 30, 9999.0) == 1500.0 * 30 / N.MIN_DAYS
    assert N._at_pace(3000.0, 30, 30, 9999.0) == 3000.0


def test_semaforo():
    today = dt.date(2026, 11, 24)
    assert N.freshness(dt.date(2026, 11, 22), 2026, 11, today) == "ok"
    assert N.freshness(dt.date(2026, 11, 19), 2026, 11, today) == "late"
    assert N.freshness(dt.date(2026, 11, 9), 2026, 11, today) == "bad"
    assert N.freshness(None, 2026, 11, today) == "bad"
    assert N.freshness(dt.date(2026, 10, 31), 2026, 10, today) == "ok"
    assert N.freshness(None, 2026, 12, today) is None
