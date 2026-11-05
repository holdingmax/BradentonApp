"""
Fotos del inventario final de Físico (pedido del usuario, 2026-11-05): el
ticket "Inventory Report" de los tanques que imprime la consola de Shell,
como la última página del Resumen de Ventas del mes (escaneado con
CamScanner, de costado y con la tira en el medio de una hoja en blanco).

Cada foto se endereza (la OSD de Tesseract y, si no alcanza, probando los
cuatro giros), se recorta al ticket y se intenta leer el volumen de cada
tanque. Capa de lectura pura: el guardado está en webapp/fisico_db.

La lectura nunca adivina: el ticket trae VOLUME y ULLAGE (lo que falta para
llenar) de cada tanque, y los dos siempre suman la capacidad del tanque
(`TANK_CAPACITY`, igual en todos los tickets de ago-sep 2026). Solo se toma
un volumen si su VOLUME + ULLAGE da justo esa capacidad, y si al menos dos
lecturas lo confirman. El OCR confunde el 8 con el 6 o el 4 en esta letra:
se prueba el 8 en su lugar, pero solo vale si después la suma cierra.
"""

import difflib
import re
from datetime import datetime

from PIL import Image, ImageFilter, ImageOps

import ocr_utils

TANK_CAPACITY = {1: 14165, 2: 6057}

_KEYWORDS = ("VOLUME", "GALS", "INVENTORY", "ULLAGE", "REGULAR", "PREMIUM")
_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
_DATE_RE = re.compile(r"\b([A-Z]{3})\s+(\d{1,2})\s*[.,]\s*(\d{4})\s+(\d{1,2})\s*[:.]\s*(\d{2})\s*([AP])", re.I)
_VALUE_RE = re.compile(r"^(VOLUME|ULLAGE)\s*[=~-]+\s*(\d+)\s*GAL")
_WATERMARK_RE = re.compile(r"scann|camscan|^with$", re.I)


def _ocr(image):
    ocr_utils.ensure_pytesseract()
    return ocr_utils.pytesseract.image_to_string(image, config="--psm 6")


def _keyword_score(text):
    upper = text.upper()
    return sum(upper.count(k) for k in _KEYWORDS)


def straighten(image):
    """(imagen derecha, si parece un ticket de inventario)."""
    ocr_utils.ensure_pytesseract()
    small = image.copy()
    small.thumbnail((1400, 1400))
    osd = ocr_utils.detect_osd_rotation(small)
    order = ([osd] if osd is not None else []) + [r for r in (0, 90, 180, 270) if r != osd]
    best_score, best_rotate = -1, 0
    for rotate in order:
        data = ocr_utils.pytesseract.image_to_data(ocr_utils.apply_osd_rotation(small, rotate), config="--psm 6", output_type=ocr_utils.pytesseract.Output.DICT)
        words = [w for w, c in zip(data["text"], data["conf"]) if len(w.strip()) >= 3 and float(c) > 80]
        score = _keyword_score(" ".join(data["text"]))
        if score > best_score:
            best_score, best_rotate = score, rotate
        # Con el ticket, o con mucho texto bien leído (una hoja derecha que no es el ticket), no hace falta probar más giros.
        if score >= 6 or len(words) >= 40:
            break
    return ocr_utils.apply_osd_rotation(image, best_rotate), best_score >= 4


def crop_to_ticket(image):
    """Recorta al texto del ticket, sin la marca de CamScanner ni los bordes de la hoja."""
    ocr_utils.ensure_pytesseract()
    data = ocr_utils.pytesseract.image_to_data(image, config="--psm 6", output_type=ocr_utils.pytesseract.Output.DICT)
    boxes = []
    for i, word in enumerate(data["text"]):
        word = word.strip()
        if len(word) >= 3 and float(data["conf"][i]) > 60 and re.search(r"[A-Z0-9]{3}", word.upper()) and not _WATERMARK_RE.search(word):
            boxes.append((data["left"][i], data["top"][i], data["left"][i] + data["width"][i], data["top"][i] + data["height"][i]))
    if not boxes:
        return image
    # Lo que queda lejos de la columna del ticket es ruido (la marca de agua, manchas).
    centers = sorted((b[0] + b[2]) / 2 for b in boxes)
    middle = centers[len(centers) // 2]
    widths = sorted(b[2] - b[0] for b in boxes)
    reach = max(widths[len(widths) // 2] * 8, image.width * 0.15)
    boxes = [b for b in boxes if abs((b[0] + b[2]) / 2 - middle) <= reach]
    left, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
    right, bottom = max(b[2] for b in boxes), max(b[3] for b in boxes)
    margin = int(0.04 * max(right - left, bottom - top))
    return image.crop((max(0, left - margin), max(0, top - margin), min(image.width, right + margin), min(image.height, bottom + margin)))


def _parse_date(match):
    month_text = match.group(1).upper()
    month = _MONTHS.index(month_text) + 1 if month_text in _MONTHS else None
    if month is None:
        close = difflib.get_close_matches(month_text, _MONTHS, n=1, cutoff=0.6)
        month = _MONTHS.index(close[0]) + 1 if close else None
    if month is None:
        return None
    hour = int(match.group(4)) % 12 + (12 if match.group(6).upper() == "P" else 0)
    try:
        return datetime(int(match.group(3)), month, int(match.group(2)), hour, int(match.group(5)))
    except ValueError:
        return None


def parse_reports(text):
    """
    Los reportes de un texto de OCR: [{"date": datetime o None, "pairs": [(VOLUME, ULLAGE), ...]}].
    Cada VOLUME va con el ULLAGE que le sigue; de qué tanque es lo dice su suma
    (ver read_inventory), porque el renglón "T 2:PREMIUM" a veces no se lee.
    """
    reports, volume = [], None
    for raw in text.upper().splitlines():
        line = re.sub(r"^[^A-Z0-9]+", "", raw.strip())
        match = _DATE_RE.search(line)
        if match:
            reports.append({"date": _parse_date(match), "pairs": []})
            volume = None
            continue
        if "INVENTORY" in line and not reports:
            reports.append({"date": None, "pairs": []})
            continue
        match = _VALUE_RE.match(line)
        if not match or not reports:
            continue
        if match.group(1) == "VOLUME":
            volume = match.group(2)
        elif volume is not None:
            reports[-1]["pairs"].append((volume, match.group(2)))
            volume = None
    return reports


# Lo que el OCR suele leer en lugar de un 8 en la letra del ticket, y el "="
# leído como un dígito de más adelante ("= 3976" -> "93976").
_EIGHT_LOOKALIKES = "64390"


def _candidates(digits):
    found = {digits}
    if len(digits) >= 5:
        found.add(digits[1:])
    for i, ch in enumerate(digits):
        if ch in _EIGHT_LOOKALIKES:
            found.add(digits[:i] + "8" + digits[i + 1:])
    return {int(d) for d in found if d}


def _tank_volumes(volume, ullage):
    """(tanque, volumen) de un par leído cuya suma da justo la capacidad de un tanque, probando las confusiones del 8."""
    matches = set()
    for v in _candidates(volume):
        for u in _candidates(ullage):
            for tank, capacity in TANK_CAPACITY.items():
                if v + u == capacity:
                    matches.add((tank, v))
    return matches


def _variants(image):
    gray = ImageOps.grayscale(image)
    for width in (800, 1100, 700, 1000):
        sized = gray.resize((width, max(1, round(gray.height * width / gray.width))), Image.LANCZOS)
        yield sized
        yield sized.filter(ImageFilter.MedianFilter(3))
        yield sized.filter(ImageFilter.MinFilter(3))
        yield sized.filter(ImageFilter.GaussianBlur(1.2)).point(lambda p: 255 if p > 170 else 0)


def read_inventory(image):
    """
    El último reporte del ticket con todos los tanques leídos con su
    verificación: {"date": datetime o None, "tanks": {1: gal, 2: gal}, "gallons": total},
    o None si no se pudo leer con confianza. `image` ya derecha y recortada.

    Se lee con varios tratamientos de la imagen y cada lectura vota; un
    volumen vale si lo dan al menos dos lecturas y ninguno otro le empata.
    """
    votes = {}  # fecha -> tanque -> volumen -> lecturas
    for variant in _variants(image):
        for report in parse_reports(_ocr(variant)):
            seen = set()
            for volume, ullage in report["pairs"]:
                matches = _tank_volumes(volume, ullage)
                if len({t for t, _ in matches}) == 1 and len(matches) == 1:
                    seen |= matches
            for tank, volume in seen:
                tanks = votes.setdefault(report["date"], {}).setdefault(tank, {})
                tanks[volume] = tanks.get(volume, 0) + 1
    readings = {}
    for when, tanks in votes.items():
        chosen = {}
        for tank in TANK_CAPACITY:
            ranked = sorted((tanks.get(tank) or {}).items(), key=lambda kv: -kv[1])
            if ranked and ranked[0][1] >= 2 and (len(ranked) == 1 or ranked[1][1] < ranked[0][1]):
                chosen[tank] = ranked[0][0]
        if len(chosen) == len(TANK_CAPACITY):
            readings[when] = chosen
    dated = [d for d in readings if d is not None]
    if dated:
        when = max(dated)
    elif None in readings:
        when = None
    else:
        return None
    tanks = readings[when]
    return {"date": when, "tanks": tanks, "gallons": sum(tanks.values())}


def images_from_file(path):
    """Las imágenes de un archivo subido: cada página de un PDF o la foto. ValueError si no es ninguna de las dos."""
    with open(path, "rb") as handle:
        head = handle.read(5)
    if head == b"%PDF-":
        ocr_utils.ensure_pdfplumber()
        with ocr_utils.pdfplumber.open(path) as pdf:
            return [ocr_utils.render_page(page, 250) for page in pdf.pages]
    try:
        image = Image.open(path)
        image.load()
    except Exception:
        raise ValueError("no es una foto ni un PDF")
    return [ImageOps.exif_transpose(image).convert("RGB")]


def process_file(path):
    """
    Las fotos de inventario de un archivo, derechas y recortadas, con su
    lectura: [{"image": PIL, "reading": dict o None}]. De un PDF de varias
    páginas (el Resumen de Ventas entero) quedan solo las del ticket; una foto
    suelta queda siempre, aunque no se reconozca.
    """
    images = images_from_file(path)
    photos = []
    for image in images:
        straight, is_ticket = straighten(image)
        if not is_ticket and len(images) > 1:
            continue
        if is_ticket:
            straight = crop_to_ticket(straight)
        photos.append({"image": straight, "reading": read_inventory(straight) if is_ticket else None})
    return photos
