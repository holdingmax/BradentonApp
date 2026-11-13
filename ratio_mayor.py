"""
Lectura del Libro Mayor que exporta Ratio (3Ratio) a Excel (pedido del
usuario, 2026-11-13): para comparar los saldos y los asientos de la
contabilidad contra lo que la app calcula (Gettel, Cierre, Lottery).

Formato del Excel (visto en "Mayor Caja 07-2025" y en el papel de trabajo de
la auditoría de junio 2026, hoja "Analisis saldo GETTEL."):

    Cuenta: 1.02.01.00 - GETTEL KIA  Emisor:garmendiaa - 13/08/2026 16:29:43
    Desde: 2025-07-01 Hasta: 30/06/2026
    Asiento/Cpbte. | Fecha | [Cliente/Proveedor] | Descripcion | Detalle | Debito | Credito | Saldo
                                                   SALDO INICIAL                              379
    7864 | 31/07/2025 | LIQUIDACION CIERRE RECAUDACION 07-2025 | 8287.49 | 0 | 8666.49
    ...

Un Excel puede traer varias cuentas (una por hoja, o una debajo de la otra):
se lee cada bloque que arranca con "Cuenta:". Solo lectura, sin base.
"""

import calendar
import re
from datetime import date, datetime, timedelta

_ACCOUNT = re.compile(r"Cuenta:\s*(\d\.\d{2}\.\d{2}\.\d{2})\s*-\s*(.*?)\s*(?:Emisor\b.*)?$", re.IGNORECASE)
_RANGE = re.compile(r"Desde:\s*(\S+)\s+Hasta:\s*(\S+)", re.IGNORECASE)
_HEADERS = {"ASIENTO/CPBTE.": "asiento", "ASIENTO": "asiento", "FECHA": "date", "DESCRIPCION": "description",
            "DESCRIPCIÓN": "description", "DETALLE": "detail", "DEBITO": "debit", "DÉBITO": "debit",
            "CREDITO": "credit", "CRÉDITO": "credit", "SALDO": "balance"}


def _to_date(value):
    """Fecha de Ratio: celda de fecha, "31/07/2025" o "2025-07-01"."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if match:
        return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text[:10])
    if match:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return None


def to_number(value):
    """Importe de Ratio: número, o texto en formato argentino ("$ 1.234,56") o con punto decimal."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("$", "").replace(" ", "").replace("\xa0", "").strip()
    if not text:
        return None
    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    text = text.strip("-()")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def _norm(value):
    return str(value).strip().upper() if value is not None else ""


def read_rows(rows):
    """Las cuentas de una hoja ya leída como lista de filas (tuplas de valores)."""
    accounts, current, columns = [], None, None
    for row in rows:
        first = next((v for v in row if v is not None and str(v).strip()), None)
        text = str(first).strip() if first is not None else ""
        match = _ACCOUNT.search(text)
        if match:
            current = {"code": match.group(1), "name": match.group(2).strip(" -"), "from": None, "to": None,
                       "opening": None, "lines": []}
            accounts.append(current)
            columns = None
            continue
        if current is None:
            continue
        match = _RANGE.search(text)
        if match and current["from"] is None:
            first_day, last_day = _to_date(match.group(1)), _to_date(match.group(2))
            current["from"] = first_day.isoformat() if first_day else None
            current["to"] = last_day.isoformat() if last_day else None
            continue
        labels = [_norm(v) for v in row]
        if columns is None:
            if any(label.startswith("ASIENTO") for label in labels) and "SALDO" in labels:
                columns = {}
                for index, label in enumerate(labels):
                    key = _HEADERS.get(label)
                    if key and key not in columns:
                        columns[key] = index
            continue

        def cell(key):
            index = columns.get(key)
            return row[index] if index is not None and index < len(row) else None

        description = str(cell("description") or "").strip()
        if description.upper() == "SALDO INICIAL":
            current["opening"] = to_number(cell("balance")) or 0.0
            continue
        day = _to_date(cell("date"))
        number = "" if isinstance(cell("asiento"), (date, datetime)) else str(cell("asiento") or "").strip()
        if day is None or not number or " " in number:
            continue  # totales, renglones en blanco
        number = number[:-2] if number.endswith(".0") else number
        current["lines"].append({
            "asiento": number, "date": day.isoformat(), "description": description,
            "detail": str(cell("detail") or "").strip(),
            "debit": round(to_number(cell("debit")) or 0.0, 2), "credit": round(to_number(cell("credit")) or 0.0, 2),
            "balance": to_number(cell("balance")),
        })
    for account in accounts:
        _complete(account)
    return [a for a in accounts if a["from"] and a["to"]]


def _complete(account):
    """Completa el período (si el Excel no lo trae) y el saldo de cada renglón y del final."""
    lines = account["lines"]
    if lines and not account["from"]:
        account["from"] = min(line["date"] for line in lines)
    if lines and not account["to"]:
        account["to"] = max(line["date"] for line in lines)
    running = account["opening"] or 0.0
    for line in lines:
        running = round(running + line["debit"] - line["credit"], 2)
        if line["balance"] is None:
            line["balance"] = running
        else:
            running = round(line["balance"], 2)
            line["balance"] = running
    account["closing"] = running


def read_mayor_workbook(path):
    """
    Las cuentas de un Excel de mayor de Ratio: [{code, name, from, to,
    opening, closing, lines: [{asiento, date, description, detail, debit,
    credit, balance}]}]. ValueError si el archivo no trae ningún mayor.
    """
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    accounts = []
    try:
        for sheet in workbook.worksheets:
            accounts.extend(read_rows(list(sheet.iter_rows(max_col=16, values_only=True))))
    finally:
        workbook.close()
    if not accounts:
        raise ValueError("El Excel no es un mayor de Ratio (falta el renglón \"Cuenta: ...\").")
    return accounts


def month_end_balances(account):
    """
    {fecha: saldo} al cierre de cada mes que el mayor cubre entero, más el
    último día del mayor: el saldo del último renglón hasta esa fecha, o el
    inicial si no hubo movimientos.
    """
    first, last = date.fromisoformat(account["from"]), date.fromisoformat(account["to"])
    opening = account["opening"] or 0.0
    cuts = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        end = date(year, month, calendar.monthrange(year, month)[1])
        if end <= last:
            cuts.append(end)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    if last not in cuts:
        cuts.append(last)
    balances = {}
    for cut in cuts:
        before = [line for line in account["lines"] if line["date"] <= cut.isoformat()]
        balances[cut.isoformat()] = round(before[-1]["balance"], 2) if before else round(opening, 2)
    # El saldo inicial es el saldo al día anterior al primero del mayor.
    balances.setdefault((first - timedelta(days=1)).isoformat(), round(opening, 2))
    return balances
