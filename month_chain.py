"""
Ayudas para los cálculos que se encadenan mes a mes (el Saldo Inicial de Caja
y el Inventario Inicial de Físico salen del final del mes anterior).

Dos cosas, las dos sin cambiar ningún resultado (V1 de AUDITORIA_2026-11.md):

- Saber cuál es el primer mes con algo cargado, para no seguir calculando
  meses hacia atrás cuando ya no queda nada: antes cada página recalculaba
  siempre 60 meses, aunque los datos empezaran hace cuatro.
- Recordar un resultado mientras dura un pedido de página (solo los GET, que
  no guardan nada): Proyecciones pide el mismo mes varias veces.
"""

import copy
import re

# No se pudo saber cuál es el primer mes (una fecha guardada con otro
# formato): se encadena como siempre, sin atajo.
UNKNOWN = "desconocido"

_ISO_MONTH = re.compile(r"^(\d{4})-(\d{2})(?:-|$)")


def month_of(value):
    """(año, mes) de una fecha "YYYY-MM-DD" o de un par (año, mes); None si no
    hay valor; UNKNOWN si no se entiende."""
    if value is None:
        return None
    if isinstance(value, (tuple, list)):
        try:
            year, month = int(value[0]), int(value[1])
        except (TypeError, ValueError, IndexError):
            return UNKNOWN
    else:
        match = _ISO_MONTH.match(str(value))
        if not match:
            return UNKNOWN
        year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return UNKNOWN
    return (year, month)


def earliest(values):
    """El mes más viejo entre fechas "YYYY-MM-DD" y pares (año, mes): None si
    no hay nada cargado, UNKNOWN si alguna no se entiende."""
    months = []
    for value in values:
        month = month_of(value)
        if month == UNKNOWN:
            return UNKNOWN
        if month is not None:
            months.append(month)
    return min(months) if months else None


def nothing_before(first_month, year, month):
    """True si es seguro que no hay nada cargado en ningún mes anterior a
    (year, month). `first_month` es lo que devuelve earliest()."""
    if first_month == UNKNOWN:
        return False
    return first_month is None or (year, month) <= first_month


def months_bounds(first, last):
    """Para leer de una vez todos los meses de `first` a `last` (pares (año,
    mes), los dos incluidos): el texto desde el que empiezan sus fechas
    "YYYY-MM-DD" y el texto en el que ya son del mes siguiente al último."""
    after = (last[0] + 1, 1) if last[1] == 12 else (last[0], last[1] + 1)
    return f"{first[0]:04d}-{first[1]:02d}-", f"{after[0]:04d}-{after[1]:02d}-"


def in_months(date_text, first, last):
    """True si la fecha es de un mes entre `first` y `last`: lo mismo que
    pedir cada mes por separado con LIKE 'YYYY-MM-%' (una fecha guardada con
    otro formato no entra en ninguno)."""
    match = _ISO_MONTH.match(str(date_text or ""))
    if not match or len(str(date_text)) < 8:
        return False
    month = (int(match.group(1)), int(match.group(2)))
    return 1 <= month[1] <= 12 and first <= month <= last


def cached(key, compute):
    """
    compute() recordado mientras dura el pedido de página en curso. Solo en un
    GET: un POST guarda y después vuelve a leer, y tiene que ver lo recién
    guardado. Fuera de un pedido (una carga en segundo plano, una prueba, las
    herramientas de consola) se calcula siempre. Cada uno recibe su copia.
    """
    try:
        from flask import g, has_request_context, request
    except ImportError:
        return compute()
    if not has_request_context() or request.method != "GET":
        return compute()
    store = g.setdefault("_month_chain_cache", {})
    if key in store:
        return copy.deepcopy(store[key])
    result = compute()
    store[key] = copy.deepcopy(result)
    return result
