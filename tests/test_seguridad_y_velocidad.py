"""
Lo arreglado en la revisión de noviembre (AUDITORIA_2026-11.md): las capturas
del manual piden sesión, las cabeceras de seguridad y de caché, un nombre de
archivo raro no da error 500, el encadenado de meses no recalcula meses sin
datos y el cruce de cupones con los EFT sale de una sola consulta. Corre
contra la base temporal de conftest.py.
"""

import io
import os
import secrets
import time

import pytest

import auth
import caja
import db
import eft_db
import fisico
import month_chain


@pytest.fixture(scope="module")
def app():
    import webapp

    return webapp.app


@pytest.fixture(scope="module")
def cliente(app):
    """Un test client con la sesión de un usuario de prueba ya abierta."""
    import flask_login.utils

    if auth.get_user("prueba") is None:
        auth.create_user("prueba", secrets.token_hex(16), must_change_password=False)
    entorno = {"REMOTE_ADDR": "127.0.0.1", "HTTP_USER_AGENT": "pruebas"}
    with app.test_request_context(environ_base=entorno):
        identificador = flask_login.utils._create_identifier()
    cliente = app.test_client()
    cliente.environ_base.update(entorno)
    with cliente.session_transaction() as sesion:
        sesion.update({"_user_id": "prueba", "_fresh": True, "_id": identificador, "_last_seen": time.time()})
    return cliente


# --- Seguridad ---------------------------------------------------------------

@pytest.mark.parametrize("ruta", [
    "/static/manual/caja.png",
    "/static/./manual/caja.png",
    "/static/MANUAL/caja.png",
    "/static/supplier_logos/../manual/caja.png",
])
def test_capturas_del_manual_piden_sesion(app, ruta):
    respuesta = app.test_client().get(ruta)
    assert respuesta.status_code == 302
    assert respuesta.headers["Location"].endswith("/login")


def test_capturas_del_manual_con_sesion(cliente):
    respuesta = cliente.get("/static/manual/caja.png")
    assert respuesta.status_code == 200
    assert respuesta.headers["Cache-Control"] == "private, max-age=3600"


def test_static_comun_sin_sesion(app):
    respuesta = app.test_client().get("/static/logo.png")
    assert respuesta.status_code == 200
    assert respuesta.headers["Cache-Control"] == "public, max-age=3600"


def test_estilos_compartidos_en_un_archivo(app, cliente):
    """Las páginas enlazan static/base.css con la fecha del archivo en la
    dirección, y así se guarda un año en el navegador."""
    import re

    pagina = cliente.get("/").get_data(as_text=True)
    enlace = re.search(r'<link rel="stylesheet" href="(/static/base\.css\?v=\d+)">', pagina)
    assert enlace, "la página no enlaza static/base.css"
    assert ":root { --accent:" in pagina  # el color del módulo sigue en la página
    respuesta = app.test_client().get(enlace.group(1))
    assert respuesta.status_code == 200 and respuesta.mimetype == "text/css"
    assert respuesta.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert "--accent-soft" in respuesta.get_data(as_text=True)


def test_cabeceras_de_seguridad(app, cliente):
    for respuesta in (app.test_client().get("/login"), cliente.get("/")):
        assert respuesta.status_code == 200
        assert respuesta.headers["Cache-Control"] == "no-store"
        assert respuesta.headers["X-Content-Type-Options"] == "nosniff"
        assert respuesta.headers["X-Frame-Options"] == "SAMEORIGIN"


@pytest.mark.parametrize("nombre", ["..", "a\x00b.pdf", "x" * 300 + ".pdf"])
def test_nombre_de_archivo_raro_no_da_500(cliente, nombre):
    respuesta = cliente.post(
        "/carga-datos/chase/cheques/subir",
        data={"pdf_files": (io.BytesIO(b"%PDF-1.4"), nombre)},
        content_type="multipart/form-data",
    )
    assert respuesta.status_code < 500


def test_texto_de_error_sin_carpetas_del_servidor(app):
    import sqlite3

    import webapp

    ruta = os.path.join(webapp._APP_TEMP_DIR, "bradenton_web_x", "tmpab", "Close Store 05-07.pdf")
    texto = webapp._exc_text(FileNotFoundError(2, "No such file or directory", ruta))
    assert "Close Store 05-07.pdf" in texto
    assert "bradenton_web_x" not in texto and webapp._APP_TEMP_DIR not in texto
    # Los avisos propios de los lectores salen tal cual; un error de la base, no.
    assert webapp._exc_text(ValueError("No se pudo leer el total.")) == "No se pudo leer el total."
    assert "locked" not in webapp._exc_text(sqlite3.OperationalError("database is locked"))


def test_usuarios_a_la_vez_no_se_pisan():
    import threading

    antes = set(auth.load_users())
    hilos = [
        threading.Thread(target=auth.create_user, args=(f"a_la_vez_{i}", secrets.token_hex(16)))
        for i in range(6)
    ]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()
    assert set(auth.load_users()) - antes == {f"a_la_vez_{i}" for i in range(6)}
    for i in range(6):
        auth.delete_user(f"a_la_vez_{i}")
    assert set(auth.load_users()) == antes


# --- Encadenado de meses -----------------------------------------------------

def test_primer_mes_con_datos():
    assert month_chain.earliest([None, None]) is None
    assert month_chain.earliest(["2026-08-05", (2026, 7), None]) == (2026, 7)
    assert month_chain.earliest(["2026-08-05", "5/8/2026"]) == month_chain.UNKNOWN
    # Nada antes de un mes: solo si el primer mes con datos no es anterior.
    assert month_chain.nothing_before(None, 2026, 9)
    assert month_chain.nothing_before((2026, 9), 2026, 9)
    assert not month_chain.nothing_before((2026, 8), 2026, 9)
    assert not month_chain.nothing_before(month_chain.UNKNOWN, 2026, 9)


def test_fechas_de_un_rango_de_meses():
    primero, ultimo = (2026, 11), (2027, 1)
    assert month_chain.months_bounds(primero, ultimo) == ("2026-11-", "2027-02-")
    assert month_chain.in_months("2026-12-31", primero, ultimo)
    assert not month_chain.in_months("2027-02-01", primero, ultimo)
    assert not month_chain.in_months("2026-10-31", primero, ultimo)
    # Como LIKE 'YYYY-MM-%': una fecha con otro formato no es de ningún mes.
    assert not month_chain.in_months("2026-12", primero, ultimo)
    assert not month_chain.in_months("2026-13-01", primero, ultimo)
    assert not month_chain.in_months(None, primero, ultimo)


def _conexiones(monkeypatch):
    cuenta = [0]
    original = db.connect

    def contando(*args, **kwargs):
        cuenta[0] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(db, "connect", contando)
    return cuenta


def test_sin_datos_no_se_recalculan_meses_hacia_atras(monkeypatch):
    """Con la base vacía, el inicial es 0 sin recorrer 60 meses (antes, unas
    245 conexiones en Físico y 490 en Caja por cada página)."""
    cuenta = _conexiones(monkeypatch)
    reporte = fisico.build_month_report(2025, 3)
    assert reporte["initial_source"] == "default" and reporte["initial_gallons"] == 0.0
    assert cuenta[0] <= 6

    cuenta[0] = 0
    reporte = caja.build_month_report_from_db(2025, 3)
    assert reporte["opening_source"] == "default" and reporte["opening_balance"] == 0.0
    assert cuenta[0] <= 16


# --- Cupones contra los EFT --------------------------------------------------

def test_cruce_de_cupones_en_una_consulta(monkeypatch):
    conn = eft_db._connect()
    try:
        with conn:
            for deposito, rcv, fecha in ((901, "RCV-901", "2026-09-07"), (902, "RCV-902", "2026-09-14")):
                conn.execute("INSERT INTO eft_deposits (id, rcv_number, eft_date) VALUES (?, ?, ?)", (deposito, rcv, fecha))
            # DDC-A: dos líneas en el primer EFT y una en el segundo; DDC-B: sin EFT.
            for deposito, cupon, bruto, fee, pagado in (
                (901, "DDC-A", 100.0, 3.0, 97.0), (901, "DDC-A", 50.0, 1.5, 48.5), (902, "DDC-A", 10.0, 0.3, 9.7),
            ):
                conn.execute(
                    "INSERT INTO eft_coupons (deposit_id, coupon, gross_amount, fees_amount, paid_amount) VALUES (?, ?, ?, ?, ?)",
                    (deposito, cupon, bruto, fee, pagado),
                )
            for cupon, neto, grupo, total in (("DDC-A", 155.2, None, None), ("DDC-B", 20.0, None, None),
                                              ("DDC-C", 0.0, "DDC-C DDC-D", 80.0), ("DDC-D", 30.0, "DDC-C DDC-D", 80.0)):
                conn.execute(
                    "INSERT INTO cupones (coupon_id, date, gross, fees, net, reported_group_text, reported_group_total) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (cupon, "2026-09-05", neto, 0.0, neto, grupo, total),
                )
    finally:
        conn.close()

    consultas = []
    original = db.connect

    def contando(*args, **kwargs):
        conn = original(*args, **kwargs)
        conn.set_trace_callback(consultas.append)
        return conn

    monkeypatch.setattr(db, "connect", contando)
    cupones = {c["coupon_id"]: c for c in eft_db.get_cupones_with_status()}
    assert len(consultas) == 2  # los cupones y su cruce, sin importar cuántos haya

    a = cupones["DDC-A"]
    assert a["match"]["lines"] == 3
    assert round(a["match"]["paid_amount"], 2) == 155.2 and round(a["match"]["gross_amount"], 2) == 160.0
    assert (a["match"]["rcv_number"], a["match"]["eft_date"]) == ("RCV-902", "2026-09-14")  # la última línea
    assert a["diff"] == 0.0
    assert cupones["DDC-B"]["match"] is None and cupones["DDC-B"]["diff"] is None
    # El cupón de batch que sigue en 0 muestra lo que le queda al grupo.
    assert cupones["DDC-C"]["group_remaining"] == 50.0 and cupones["DDC-C"]["group_pending_count"] == 1
    assert cupones["DDC-D"]["group_remaining"] is None
