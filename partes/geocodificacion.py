"""Normaliza la dirección que dicta quien llama y averigua el barrio.

Usa servicios gratuitos, sin clave:
- Nominatim (OpenStreetMap): calle, altura y barrio. Política de uso: máximo
  1 consulta por segundo y un User-Agent que identifique al sistema.
- Georef (datos.gob.ar): ubica la altura sobre la calle cuando OpenStreetMap
  no tiene esa casa cargada. Con esa coordenada se le pide el barrio a Nominatim.

Si no hay internet o los servicios fallan, devuelve una lista vacía y el guardia
carga la dirección a mano, como siempre.
"""
import functools
import hashlib
import json
import logging
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.cache import cache

log = logging.getLogger(__name__)

NOMINATIM = "https://nominatim.openstreetmap.org"
GEOREF = "https://apis.datos.gob.ar/georef/api"
TIMEOUT = 5
# OpenStreetMap llama al barrio de distintas formas según quién lo cargó.
CLAVES_BARRIO = ("neighbourhood", "suburb", "quarter", "city_district", "residential")
# Abreviaturas que se usan al tomar la dirección por teléfono.
ABREVIATURAS = [
    (r"bv|bvd|blvd|blv", "Bulevar"), (r"av|avda|avd", "Avenida"), (r"gral", "General"),
    (r"pje|psje", "Pasaje"), (r"dr", "Doctor"), (r"pte|pres", "Presidente"),
    (r"cnel", "Coronel"), (r"tte", "Teniente"), (r"sta", "Santa"), (r"sto", "Santo"),
]
REGEX_ABREVIATURAS = [(re.compile(rf"\b(?:{a})\b\.?", re.IGNORECASE), b) for a, b in ABREVIATURAS]

ARCHIVO_CALLES = Path(__file__).resolve().parent / "datos" / "calles.json"
ARCHIVO_BARRIOS = Path(__file__).resolve().parent / "datos" / "barrios.json"


def _sin_tildes(texto):
    sin = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", sin)).strip()


@functools.cache
def calles_del_listado():
    """Calles de Villa María (las genera el comando actualizar_calles)."""
    try:
        return json.loads(ARCHIVO_CALLES.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


def _calles_agregadas():
    return list(apps.get_model("partes", "Calle").objects.values_list("nombre", flat=True))


def calles():
    """Las del listado más las que agregó Estadística."""
    return sorted({*calles_del_listado(), *_calles_agregadas()})


def barrios_del_mapa():
    """{nombre: [lat, lon]} de los barrios de OSM (los genera actualizar_calles)."""
    try:
        barrios = json.loads(ARCHIVO_BARRIOS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return {normalizar_barrio(nombre): punto for nombre, punto in barrios.items()}


@functools.cache
def _calles_por_clave():
    return {_sin_tildes(c): c for c in calles_del_listado()}


def nombre_oficial(calle):
    """'manuel belgrano' → 'Manuel Belgrano' si está en el listado; si no, la deja como vino."""
    clave = _sin_tildes(expandir_abreviaturas(calle))
    oficial = _calles_por_clave().get(clave)
    if oficial:
        return oficial
    for agregada in _calles_agregadas():
        if _sin_tildes(agregada) == clave:
            return agregada
    return calle


_candado = threading.Lock()
_ultima_consulta = 0.0


def _pedir_json(url, params):
    """GET a un servicio externo. Separado para poder simularlo en los tests."""
    global _ultima_consulta
    if url.startswith(NOMINATIM):
        with _candado:
            espera = 1.0 - (time.monotonic() - _ultima_consulta)
            if espera > 0:
                time.sleep(espera)
            _ultima_consulta = time.monotonic()
    pedido = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": settings.GEOCODIFICACION_USER_AGENT, "Accept-Language": "es"},
    )
    with urllib.request.urlopen(pedido, timeout=TIMEOUT) as respuesta:
        return json.load(respuesta)


# Mismo barrio cargado con dos nombres, o mal escrito en OSM.
BARRIOS_CORREGIDOS = {"Presidente Roque Saenz Peña": "Roque Sáenz Peña", "Botanico": "Botánico",
                      "Las Margaritas residencial": "Las Margaritas", "VALLE ESCONDIDO": "Valle Escondido"}


def normalizar_barrio(nombre):
    nombre = nombre.strip()
    nombre = nombre[len("Barrio "):] if nombre.startswith("Barrio ") else nombre
    return BARRIOS_CORREGIDOS.get(nombre, nombre)


def _barrio(direccion):
    for clave in CLAVES_BARRIO:
        if direccion.get(clave):
            return normalizar_barrio(direccion[clave])
    return ""


def _barrio_por_coordenada(lat, lon):
    resultado = _pedir_json(f"{NOMINATIM}/reverse", {
        "lat": lat, "lon": lon, "format": "jsonv2", "zoom": 17, "addressdetails": 1,
    })
    return _barrio(resultado.get("address", {}))


def _coordenada_georef(calle, numero):
    resultado = _pedir_json(f"{GEOREF}/direcciones", {
        "direccion": f"{calle} {numero}",
        "localidad_censal": settings.GEOCODIFICACION_LOCALIDAD_CENSAL,
        "max": 1, "campos": "ubicacion",
    })
    for direccion in resultado.get("direcciones", []):
        ubicacion = direccion.get("ubicacion") or {}
        if ubicacion.get("lat") is not None:
            return ubicacion["lat"], ubicacion["lon"]
    return None


def expandir_abreviaturas(calle):
    for regex, completa in REGEX_ABREVIATURAS:
        calle = regex.sub(completa, calle)
    return re.sub(r"\s+", " ", calle).strip()


def _buscar(calle, numero):
    resultados = _pedir_json(f"{NOMINATIM}/search", {
        "street": f"{numero} {calle}".strip(),
        "city": settings.GEOCODIFICACION_CIUDAD,
        "state": settings.GEOCODIFICACION_PROVINCIA,
        "country": "Argentina",
        # Solo el ejido de Villa María: sin esto aparecen calles homónimas de otras ciudades.
        "viewbox": settings.GEOCODIFICACION_AREA, "bounded": 1,
        "format": "jsonv2", "addressdetails": 1, "limit": 5,
    })
    candidatos, vistos = [], set()
    for r in resultados:
        direccion = r.get("address", {})
        nombre_calle = direccion.get("road")
        if not nombre_calle:
            continue
        candidato = {
            "calle": nombre_calle,
            "numero": direccion.get("house_number") or numero,
            "barrio": _barrio(direccion),
            "latitud": round(float(r["lat"]), 6),
            "longitud": round(float(r["lon"]), 6),
            "exacta": bool(direccion.get("house_number")),
        }
        clave = (candidato["calle"], candidato["barrio"])
        if clave not in vistos:
            vistos.add(clave)
            candidatos.append(candidato)

    # OpenStreetMap encontró la calle pero no esa casa: el punto es el centro de la
    # calle y el barrio puede ser otro. Se ubica la altura con Georef.
    if candidatos and numero and not candidatos[0]["exacta"]:
        primero = candidatos[0]
        coordenada = _coordenada_georef(primero["calle"], numero)
        if coordenada:
            primero["latitud"], primero["longitud"] = (round(c, 6) for c in coordenada)
            primero["barrio"] = _barrio_por_coordenada(*coordenada) or primero["barrio"]
            primero["exacta"] = True
    return candidatos


def _buscar_esquina(calle, esquina):
    # Georef ubica intersecciones ("Calle A y Calle B"); con la coordenada, el barrio sale de OSM.
    resultado = _pedir_json(f"{GEOREF}/direcciones", {
        "direccion": f"{calle} y {esquina}",
        "localidad_censal": settings.GEOCODIFICACION_LOCALIDAD_CENSAL,
        "max": 1, "campos": "ubicacion",
    })
    for direccion in resultado.get("direcciones", []):
        ubicacion = direccion.get("ubicacion") or {}
        if ubicacion.get("lat") is None:
            continue
        lat, lon = round(ubicacion["lat"], 6), round(ubicacion["lon"], 6)
        return [{"calle": nombre_oficial(calle), "numero": "", "esquina": nombre_oficial(esquina),
                 "barrio": _barrio_por_coordenada(lat, lon), "latitud": lat, "longitud": lon, "exacta": True}]
    return []


def buscar_esquina(calle, esquina):
    """Barrio y coordenada de una esquina. Lista vacía si no se encuentra o no hay red."""
    calle, esquina = expandir_abreviaturas(calle), expandir_abreviaturas(esquina)
    if len(calle) < 3 or len(esquina) < 3:
        return []
    clave = "geo-esq:" + hashlib.md5(f"{calle.lower()}|{esquina.lower()}".encode()).hexdigest()
    candidatos = cache.get(clave)
    if candidatos is None:
        try:
            candidatos = _buscar_esquina(calle, esquina)
        except Exception:  # sin red o servicio caído: el barrio se carga a mano
            log.warning("No se pudo ubicar la esquina %r y %r", calle, esquina, exc_info=True)
            return []
        cache.set(clave, candidatos, 60 * 60 * 24 * 7)
    return candidatos


def buscar_direccion(calle, numero=""):
    """Devuelve hasta 5 candidatos {calle, numero, barrio, latitud, longitud, exacta}."""
    calle, numero = expandir_abreviaturas(calle), numero.strip()
    if len(calle) < 3:
        return []
    clave = "geo:" + hashlib.md5(f"{calle.lower()}|{numero}".encode()).hexdigest()
    candidatos = cache.get(clave)
    if candidatos is None:
        try:
            candidatos = _buscar(calle, numero)
        except Exception:  # sin red, servicio caído, respuesta rara: se carga a mano
            log.warning("No se pudo geocodificar %r %r", calle, numero, exc_info=True)
            return []
        cache.set(clave, candidatos, 60 * 60 * 24 * 7)
    return candidatos
