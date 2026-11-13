"""
Persistencia de los mayores de Ratio (ratio_mayor.py; pedido del usuario,
2026-11-13): los renglones de cada cuenta y su saldo a una fecha.

- Subir un mayor reemplaza los renglones de esa cuenta en el período del
  Excel (Desde/Hasta) y guarda el saldo al cierre de cada mes que cubre.
- Un saldo también se puede cargar a mano (origen "manual"); el de un mayor
  subido después lo pisa.

reportes_data/ratio.db (gitignored), o el esquema ratio de PostgreSQL
(migraciones/versions/0028_ratio.py).
"""

import os
from datetime import datetime

import app_paths
import db

_BASE_DIR = app_paths.DATA_DIR
_DB_PATH = os.path.join(_BASE_DIR, "ratio.db")

ORIGIN_MAYOR = "mayor"
ORIGIN_MANUAL = "manual"


def _init_sqlite(conn):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS mayor_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account TEXT NOT NULL, position INTEGER NOT NULL,
            asiento TEXT, date TEXT NOT NULL, description TEXT, detail TEXT,
            debit REAL, credit REAL, balance REAL,
            source TEXT, loaded_at TEXT
        );
        CREATE INDEX IF NOT EXISTS mayor_lines_account_date ON mayor_lines (account, date);
        CREATE TABLE IF NOT EXISTS saldos (
            account TEXT NOT NULL, date TEXT NOT NULL,
            balance REAL NOT NULL, origin TEXT NOT NULL, name TEXT, updated_at TEXT,
            PRIMARY KEY (account, date)
        );
        CREATE TABLE IF NOT EXISTS mayor_periods (
            account TEXT NOT NULL, date_from TEXT NOT NULL, date_to TEXT NOT NULL,
            name TEXT, source TEXT, loaded_at TEXT,
            PRIMARY KEY (account, date_from, date_to)
        );
        """
    )


def _connect():
    return db.connect("ratio", _DB_PATH, _init_sqlite)


def _now():
    return datetime.now().isoformat(timespec="seconds")


def save_mayor(account, balances, source):
    """
    Guarda una cuenta leída por ratio_mayor.read_mayor_workbook y sus saldos
    de fin de mes (ratio_mayor.month_end_balances). Devuelve la cantidad de
    renglones guardados.
    """
    conn = _connect()
    now = _now()
    try:
        with conn:
            conn.execute("DELETE FROM mayor_lines WHERE account = ? AND date >= ? AND date <= ?",
                         (account["code"], account["from"], account["to"]))
            conn.executemany(
                "INSERT INTO mayor_lines (account, position, asiento, date, description, detail, debit, credit, "
                "balance, source, loaded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(account["code"], position, line["asiento"], line["date"], line["description"], line["detail"],
                  line["debit"], line["credit"], line["balance"], source, now)
                 for position, line in enumerate(account["lines"])],
            )
            for day, balance in balances.items():
                conn.execute(
                    "INSERT INTO saldos (account, date, balance, origin, name, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (account, date) DO UPDATE SET balance = excluded.balance, origin = excluded.origin, "
                    "name = excluded.name, updated_at = excluded.updated_at",
                    (account["code"], day, balance, ORIGIN_MAYOR, account["name"], now),
                )
            conn.execute(
                "INSERT INTO mayor_periods (account, date_from, date_to, name, source, loaded_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (account, date_from, date_to) DO UPDATE SET name = excluded.name, source = excluded.source, "
                "loaded_at = excluded.loaded_at",
                (account["code"], account["from"], account["to"], account["name"], source, now),
            )
    finally:
        conn.close()
    return len(account["lines"])


def set_manual_balance(account, day, balance, name=None):
    """Saldo de una cuenta a una fecha, cargado a mano (None lo borra si era manual)."""
    conn = _connect()
    try:
        with conn:
            if balance is None:
                conn.execute("DELETE FROM saldos WHERE account = ? AND date = ? AND origin = ?",
                             (account, day, ORIGIN_MANUAL))
            else:
                conn.execute(
                    "INSERT INTO saldos (account, date, balance, origin, name, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (account, date) DO UPDATE SET balance = excluded.balance, origin = excluded.origin, "
                    "updated_at = excluded.updated_at",
                    (account, day, balance, ORIGIN_MANUAL, name, _now()),
                )
    finally:
        conn.close()


def get_balance(account, day):
    """{balance, origin, updated_at} de la cuenta a esa fecha exacta, o None."""
    conn = _connect()
    try:
        row = conn.execute("SELECT balance, origin, updated_at FROM saldos WHERE account = ? AND date = ?",
                           (account, day)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_lines(account, date_from, date_to):
    conn = _connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT asiento, date, description, detail, debit, credit, balance FROM mayor_lines "
            "WHERE account = ? AND date >= ? AND date <= ? ORDER BY date, position, id",
            (account, date_from, date_to))]
    finally:
        conn.close()


def get_lines_by_account(date_from, date_to):
    """{cuenta: [renglones]} de todas las cuentas con movimientos en el período."""
    conn = _connect()
    try:
        result = {}
        for r in conn.execute(
                "SELECT account, asiento, date, description, detail, debit, credit, balance FROM mayor_lines "
                "WHERE date >= ? AND date <= ? ORDER BY account, date, position, id", (date_from, date_to)):
            result.setdefault(r["account"], []).append(dict(r))
        return result
    finally:
        conn.close()


def covered_accounts(date_from, date_to):
    """Las cuentas con un mayor subido que cubre todo el período (aunque no tengan movimientos)."""
    conn = _connect()
    try:
        return {r["account"] for r in conn.execute(
            "SELECT account FROM mayor_periods WHERE date_from <= ? AND date_to >= ? ORDER BY account",
            (date_from, date_to))}
    finally:
        conn.close()


def list_periods():
    """Lo subido, lo más nuevo primero: [{account, name, date_from, date_to, loaded_at}]."""
    conn = _connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT account, name, date_from, date_to, source, loaded_at FROM mayor_periods "
            "ORDER BY account, date_from, date_to")]
    finally:
        conn.close()


def list_balances(origin=None):
    conn = _connect()
    try:
        sql = "SELECT account, date, balance, origin, name, updated_at FROM saldos"
        params = ()
        if origin:
            sql += " WHERE origin = ?"
            params = (origin,)
        return [dict(r) for r in conn.execute(sql + " ORDER BY account, date", params)]
    finally:
        conn.close()
