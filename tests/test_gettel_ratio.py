"""
Gettel como una sola cuenta, el mayor de Ratio y el control de asientos
(2026-11-13). Los números de junio 2026 son los del papel de trabajo de la
auditoría ("WP GETTEL, J.H WILLIAMS"): pendiente del 17/06 al 30/06,
$11,581.64.
"""

import io
import secrets
import time

import pytest

import auth
import control_asientos
import cuenta_kia_toyota as ckt
import ratio_mayor


def _row(day, la, kia, kia_gal, toyota, toyota_gal):
    return {"date": day, "la": la, "kia": kia, "kia_gal": kia_gal, "toyota": toyota, "toyota_gal": toyota_gal,
            "dif": round(la - kia - toyota, 2)}


def test_total_cupon_a_cobrar_como_el_papel_de_trabajo():
    # La fila VERIFICAR del WP de junio 2026: A 11,317.58, B -56.54, C 320.60.
    totals = ckt.sheet_totals([_row("2026-06-30", 11317.58, 4284.70, 1139.19, 6458.35, 1687.694)])
    assert round(totals["rebate"], 5) == -56.53768
    assert round(totals["charge"], 4) == 320.5954
    assert round(totals["total"], 2) == 11581.64


def test_dias_sugeridos_el_tramo_mas_cercano():
    rows = [_row(f"2026-09-{d:02d}", 1000.0, 600.0, 150.0, 400.0, 100.0) for d in range(1, 11)]
    one_day = ckt.sheet_totals(rows[:1])["total"]
    assert [r["date"] for r in ckt.suggest_pending(rows, one_day * 3 + 100)] == ["2026-09-08", "2026-09-09", "2026-09-10"]
    assert [r["date"] for r in ckt.suggest_pending(rows, one_day * 3 + 600)] == [f"2026-09-{d:02d}" for d in range(7, 11)]
    assert ckt.suggest_pending(rows, one_day * 0.4) == []
    assert ckt.suggest_pending(rows, -50.0) == []


def _ledger():
    charges = {}
    for month, days in ((1, 31), (2, 28)):
        for d in range(1, days + 1):
            charges[f"2031-{month:02d}-{d:02d}"] = {"la": 100.0, "kia": 60.0, "kia_gal": 15.0, "toyota": 40.0, "toyota_gal": 10.0}
    payments = [{"date": "2031-01-20", "transc": "10", "amount": 999.0, "company": ckt.KIA},
                {"date": "2031-01-20", "transc": "11", "amount": 999.0, "company": ckt.KIA},
                {"date": "2031-02-10", "transc": "30", "amount": 500.0, "company": None}]
    return ckt.build_ledger(charges, payments, {})


def test_cuenta_unica_arrastra_los_dias_pendientes():
    day_total = ckt.sheet_totals([_row("2031-01-01", 100.0, 60.0, 15.0, 40.0, 10.0)])["total"]
    account = ckt.single_account(_ledger(), {"2031-01": "2031-01-22"})
    january, february = account["2031-01"], account["2031-02"]
    assert january["opening"] == 0.0
    assert round(january["totals"]["total"], 2) == round(day_total * 31, 2)
    assert january["collected"] == 1998.0 and len(january["payments"]) == 1
    assert january["pending_fixed"] and january["pending_from"] == "2031-01-22"
    assert round(january["pending"]["total"], 2) == round(day_total * 10, 2)
    # Lo que falta cobrar menos los días pendientes: lo que el pago no cubrió justo.
    assert january["gap"] == round(day_total * 31 - 1998.0 - day_total * 10, 2)
    # Febrero arrastra los días pendientes (no el saldo corrido) y, sin día fijado, sugiere.
    assert round(february["opening"], 2) == round(day_total * 10, 2)
    assert february["opening_range"] == ("2031-01-22", "2031-01-31")
    assert not february["pending_fixed"]
    assert round(february["difference"], 2) == round(500.0 - day_total * 38, 2)
    assert february["entries"][0]["debit"] == 2800.0 and february["entries"][2]["debit"] == round(day_total * 28 - 2800.0, 2)
    # Un mes sin nada cargado muestra lo que quedó del anterior.
    march = ckt.account_month(account, 2031, 3)
    assert not march["has_data"] and march["opening"] == february["pending"]["total"]


MAYOR = [
    ("Cuenta: 1.02.01.00 - GETTEL KIA  Emisor:prueba - 01/10/2031 10:00:00",),
    ("Desde: 2031-08-01 Hasta: 30/09/2031",),
    ("Asiento/Cpbte.", "Fecha", "Cliente/Proveedor", "Descripcion", "Detalle", "Debito", "Credito", "Saldo"),
    (None, None, None, "SALDO INICIAL", None, None, None, 1000),
    ("100", "31/08/2031", None, "LIQUIDACION CIERRE RECAUDACION 08-2031", None, 500, 0, 1500),
    ("201", "30/09/2031", None, "LIQUIDACION CIERRE RECAUDACION 09-2031", None, "$ 2.500,00", 0, 4000),
    ("202", "30/09/2031", None, "COBRO GETTEL-KIA SEPTIEMBRE", None, 0, 1800.5, 2199.5),
    ("203", "30/09/2031", None, "REBATE GETTEL SEPTIEMBRE", None, 75.16, 0, 2274.66),
    (None, None, None, None, None, 3075.16, 1800.5, None),
]


def test_mayor_de_ratio():
    (account,) = ratio_mayor.read_rows(MAYOR)
    assert (account["code"], account["name"]) == ("1.02.01.00", "GETTEL KIA")
    assert (account["from"], account["to"], account["opening"], account["closing"]) == ("2031-08-01", "2031-09-30", 1000.0, 2274.66)
    assert [line["asiento"] for line in account["lines"]] == ["100", "201", "202", "203"]
    assert account["lines"][1]["debit"] == 2500.0 and account["lines"][1]["date"] == "2031-09-30"
    assert ratio_mayor.month_end_balances(account) == {"2031-07-31": 1000.0, "2031-08-31": 1500.0, "2031-09-30": 2274.66}
    assert ratio_mayor.to_number("$ 1.234,56") == 1234.56 and ratio_mayor.to_number("-12,5") == -12.5


def test_asientos_contra_el_mayor():
    (account,) = ratio_mayor.read_rows(MAYOR)
    september = [line for line in account["lines"] if line["date"] >= "2031-09-01"]
    expected = [
        control_asientos._entry("LIQUIDACION CIERRE RECAUDACION 09-2031", "Cierre", [
            control_asientos._line("1.01.01.00", "debit", 9000.0), control_asientos._line("1.02.01.00", "debit", 2500.0)],
            ("LIQUIDACION", "RECAUDACION"), ("COMISIONES",)),
        control_asientos._entry("COBRO GETTEL-KIA SEPTIEMBRE", "Cierre", [
            control_asientos._line("1.02.01.00", "credit", 1800.5)], ("COBRO", "GETTEL")),
        control_asientos._entry("REBATE GETTEL SEPTIEMBRE 2031", "Kia y Toyota", [
            control_asientos._line("1.02.01.00", "debit", 63.63)], ("GETTEL",), ("LIQUIDACION",)),
        control_asientos._entry("DEV SALE TAX POR C-STORE 09-2031", "Cierre", [
            control_asientos._line("2.02.01.00", "credit", 40.0)], ("SALE TAX",)),
    ]
    results = control_asientos.compare(expected, {"1.02.01.00": september}, covered={"1.02.01.00", "2.02.01.00"})
    sales, collected, rebate, tax = results
    assert [line["status"] for line in sales["lines"]] == ["sin_mayor", "igual"] and sales["status"] == "parcial"
    assert collected["status"] == "igual" and collected["numbers"] == ["202"]
    assert rebate["status"] == "distinto" and rebate["lines"][0]["diff"] == 11.53
    # El mayor de Sale tax está subido y no tiene el asiento: falta en Ratio.
    assert tax["lines"][0]["status"] == "falta" and tax["status"] == "distinto"
    assert control_asientos.needed_accounts(results) == [("1.01.01.00", "CAJA")]
    assert control_asientos.summary(results) == {"igual": 1, "distinto": 2, "parcial": 1, "sin_mayor": 0}


@pytest.fixture(scope="module")
def cliente():
    import flask_login.utils
    import webapp

    if auth.get_user("prueba") is None:
        auth.create_user("prueba", secrets.token_hex(16), must_change_password=False)
    entorno = {"REMOTE_ADDR": "127.0.0.1", "HTTP_USER_AGENT": "pruebas"}
    with webapp.app.test_request_context(environ_base=entorno):
        identificador = flask_login.utils._create_identifier()
    cliente = webapp.app.test_client()
    cliente.environ_base.update(entorno)
    with cliente.session_transaction() as sesion:
        sesion.update({"_user_id": "prueba", "_fresh": True, "_id": identificador, "_last_seen": time.time()})
    return cliente


def _mayor_xlsx():
    from openpyxl import Workbook

    workbook = Workbook()
    for row in MAYOR:
        workbook.active.append(list(row))
    data = io.BytesIO()
    workbook.save(data)
    data.seek(0)
    return data


def test_paginas_y_carga_del_mayor(cliente):
    for url in ("/controles/kia-toyota", "/controles/asientos", "/carga-datos/ratio", "/carga-datos/gettel",
                "/controles/rapidos/kia"):
        assert cliente.get(url).status_code == 200, url
    origen = {"Origin": "http://localhost"}
    respuesta = cliente.post("/carga-datos/ratio/subir", data={"files": (_mayor_xlsx(), "Mayor Gettel.xlsx")},
                             content_type="multipart/form-data", headers=origen)
    assert respuesta.status_code == 302
    import ratio_mayor_db

    assert ratio_mayor_db.get_balance("1.02.01.00", "2031-09-30")["balance"] == 2274.66
    assert len(ratio_mayor_db.get_lines("1.02.01.00", "2031-09-01", "2031-09-30")) == 3
    assert ratio_mayor_db.covered_accounts("2031-09-01", "2031-09-30") == {"1.02.01.00"}
    pagina = cliente.get("/controles/kia-toyota?year=2031&month=9").get_data(as_text=True)
    assert "$2,274.66" in pagina and "LIQUIDACION CIERRE RECAUDACION 09-2031" in pagina
    # Un saldo a mano, y volver a subir el mayor lo pisa.
    cliente.post("/carga-datos/ratio/saldo", data={"account": "1.02.01.00", "date": "2031-10-31", "balance": "1.500,25"},
                 headers=origen)
    assert ratio_mayor_db.get_balance("1.02.01.00", "2031-10-31") == pytest.approx(
        {"balance": 1500.25, "origin": "manual", "updated_at": ratio_mayor_db.get_balance("1.02.01.00", "2031-10-31")["updated_at"]})
    cliente.post("/carga-datos/ratio/saldo", data={"account": "1.02.01.00", "date": "2031-10-31", "action": "borrar"},
                 headers=origen)
    assert ratio_mayor_db.get_balance("1.02.01.00", "2031-10-31") is None
    # Días pendientes a mano y margen.
    cliente.post("/carga-datos/gettel/pendiente", data={"month": "2031-09", "day": "2031-09-20"}, headers=origen)
    cliente.post("/carga-datos/gettel/pendiente", data={"margin": "250"}, headers=origen)
    import cuenta_kia_toyota_db

    assert cuenta_kia_toyota_db.get_settings() == {"pendiente_desde": {"2031-09": "2031-09-20"}, "margen": 250.0}
    cliente.post("/carga-datos/gettel/pendiente", data={"month": "2031-09", "action": "automatico"}, headers=origen)
    assert cuenta_kia_toyota_db.get_settings()["pendiente_desde"] == {"2031-09": ""}
