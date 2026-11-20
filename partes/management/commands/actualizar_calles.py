"""Arma los listados de calles y barrios de Villa María para sugerirlos al cargar un parte.

Uso: python manage.py actualizar_calles

Baja de OpenStreetMap las calles y los barrios con nombre del área y se queda con los
que caen dentro del ejido de la ciudad (así no se mezclan los de Villa Nueva), y le suma las
calles de Georef (el servicio del gobierno), que tiene varias que faltan en OpenStreetMap.
Con --solo-georef suma las de Georef a lo que ya hay, sin consultar OpenStreetMap. El
resultado se guarda en partes/datos/calles.json y barrios.json, que se versionan en
git: la pantalla los usa sin internet. Los barrios además se suman al catálogo Barrio
(editable en el admin) la próxima vez que se corra cargar_catalogos.
"""
import json
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from partes.geocodificacion import (
    ARCHIVO_BARRIOS, ARCHIVO_CALLES, GEOREF, NOMINATIM, _sin_tildes, calles_del_listado, normalizar_barrio,
)

OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
# Nombres de lugares que OSM marca como barrio pero no lo son.
BARRIOS_EXCLUIDOS = {"equip. urbano", "futuro barrio en construccion", "futuro barrio en construcción",
                     "villa nueva", "amma"}
# Senderos, accesos internos y similares: no son direcciones donde se despacha una unidad.
TIPOS_EXCLUIDOS = {"footway", "path", "cycleway", "steps", "track", "bridleway", "corridor",
                   "service", "construction", "proposed"}


def _pedir(url, datos=None):
    pedido = urllib.request.Request(url, data=datos,
                                    headers={"User-Agent": settings.GEOCODIFICACION_USER_AGENT})
    with urllib.request.urlopen(pedido, timeout=180) as respuesta:
        return json.load(respuesta)


def dentro(punto, anillos):
    """Punto en polígono (ray casting). anillos: el exterior y los huecos, en [lon, lat]."""
    x, y = punto
    adentro = False
    for anillo in anillos:
        for (x1, y1), (x2, y2) in zip(anillo, anillo[1:] + anillo[:1]):
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                adentro = not adentro
    return adentro


# Palabras que no distinguen una calle de otra: "Gral. Paz" y "General José María Paz" son la misma.
PALABRAS_VACIAS = {"de", "del", "la", "las", "los", "el", "y", "calle", "avenida", "av", "bulevar", "bv", "pasaje", "pje",
                   "general", "gral", "doctor", "dr", "dra", "presidente", "pte", "coronel", "cnel", "teniente", "tte",
                   "ingeniero", "ing", "profesor", "prof", "san", "santa", "santo", "gobernador", "monsenor",
                   "concejal", "intendente", "maestro", "cabo", "sargento", "nacional", "provincial", "republica"}
# Abreviaturas con las que Georef trae los nombres.
ABREVIATURAS_GEOREF = {"av": "Avenida", "aut": "Autopista", "cbo": "Cabo", "cjal": "Concejal", "comb": "Combatientes",
                       "gdor": "Gobernador", "granads": "Granaderos", "grl": "General", "int": "Intendente",
                       "mons": "Monseñor", "mstro": "Maestro", "mstros": "Maestros", "nac": "Nacional",
                       "pcial": "Provincial", "pcias": "Provincias", "pje": "Pasaje", "rca": "República",
                       "sgt": "Sargento", "sold": "Soldados"}
PALABRAS_MINUSCULA = {"de", "del", "la", "las", "los", "el", "y", "e"}
# Sin nombre real en Georef.
NOMBRES_DESCARTADOS = {"calle s n", "s n", "calle sin nombre", "sin nombre", "calle publica", "calle"}


def nombre_prolijo(nombre):
    """Georef viene en mayúsculas: "17 DE OCTUBRE ARA" → "17 de Octubre Ara"."""
    palabras = [ABREVIATURAS_GEOREF.get(p, p) for p in nombre.strip().lower().split()]
    return " ".join(p if i and p in PALABRAS_MINUSCULA else p[0].upper() + p[1:] for i, p in enumerate(palabras))


def palabras_clave(nombre):
    return {"setiembre" if p == "septiembre" else p
            for p in _sin_tildes(nombre).split() if p not in PALABRAS_VACIAS}


def es_la_misma(nueva, existente):
    """Todas las palabras de una están en la otra, contando las iniciales ("A Lisignoli" = "Aldo Lisignoli")."""
    def incluida(a, b):
        return all(p in b or (len(p) == 1 and any(q.startswith(p) for q in b)) for p in a)
    return bool(nueva and existente) and (incluida(nueva, existente) or incluida(existente, nueva))


def sumar_georef(calles, localidad):
    """Suma a `calles` las de Georef que no estén ya. Devuelve cuántas agregó."""
    datos = _pedir(f"{GEOREF}/calles?" + urllib.parse.urlencode({
        "localidad_censal": localidad, "max": 5000, "campos": "nombre"}))
    claves = [palabras_clave(c) for c in calles]
    agregadas = 0
    for calle in datos.get("calles", []):
        nombre = nombre_prolijo(calle["nombre"])
        clave = palabras_clave(nombre)
        if not clave or _sin_tildes(nombre) in NOMBRES_DESCARTADOS or any(es_la_misma(clave, c) for c in claves):
            continue
        calles.add(nombre)
        claves.append(clave)
        agregadas += 1
    return agregadas


class Command(BaseCommand):
    help = "Actualiza el listado de calles de Villa María desde OpenStreetMap y Georef."

    def add_arguments(self, parser):
        parser.add_argument("--solo-georef", action="store_true",
                            help="Suma las calles de Georef al listado actual, sin consultar OpenStreetMap.")

    def handle(self, *args, **options):
        if options["solo_georef"]:
            calles = set(calles_del_listado())
            agregadas = sumar_georef(calles, settings.GEOCODIFICACION_LOCALIDAD_CENSAL)
            ARCHIVO_CALLES.write_text(json.dumps(sorted(calles), ensure_ascii=False, indent=0), encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"{agregadas} calles nuevas de Georef; {len(calles)} en total."))
            return
        ciudad = _pedir(f"{NOMINATIM}/search?" + urllib.parse.urlencode({
            "city": settings.GEOCODIFICACION_CIUDAD, "state": settings.GEOCODIFICACION_PROVINCIA,
            "country": "Argentina", "format": "jsonv2", "polygon_geojson": 1, "limit": 1,
        }))
        geometria = ciudad[0]["geojson"] if ciudad else None
        if not geometria or geometria["type"] not in ("Polygon", "MultiPolygon"):
            raise CommandError("Nominatim no devolvió el contorno de la ciudad.")
        poligonos = [geometria["coordinates"]] if geometria["type"] == "Polygon" else geometria["coordinates"]

        oeste, norte, este, sur = settings.GEOCODIFICACION_AREA.split(",")
        area = f"({sur},{oeste},{norte},{este})"
        consulta = (f'[out:json][timeout:120];(way["highway"]["name"]{area};'
                    f'nwr["place"~"neighbourhood|suburb|quarter"]["name"]{area};'
                    f'way["landuse"="residential"]["name"]{area};);out tags center;')
        elementos = None
        for servidor in OVERPASS:
            try:
                elementos = _pedir(servidor, urllib.parse.urlencode({"data": consulta}).encode())["elements"]
                break
            except Exception as error:  # los servidores públicos se saturan seguido
                self.stderr.write(f"{servidor}: {error}")
        if elementos is None:
            raise CommandError("Overpass no respondió. Probá de nuevo en unos minutos.")

        calles, barrios = set(), {}
        for e in elementos:
            centro = e.get("center") or ({"lat": e["lat"], "lon": e["lon"]} if "lat" in e else None)
            if not centro or not any(dentro((centro["lon"], centro["lat"]), anillos) for anillos in poligonos):
                continue
            nombre = e["tags"]["name"].strip()
            if "highway" in e["tags"]:
                if e["tags"]["highway"] not in TIPOS_EXCLUIDOS:
                    calles.add(nombre)
            elif nombre.lower() not in BARRIOS_EXCLUIDOS:
                nombre = normalizar_barrio(nombre)
                barrios.setdefault(nombre, [round(centro["lat"], 6), round(centro["lon"], 6)])

        try:
            self.stdout.write(f"{sumar_georef(calles, settings.GEOCODIFICACION_LOCALIDAD_CENSAL)} calles sumadas de Georef.")
        except Exception as error:  # con OpenStreetMap solo también sirve
            self.stderr.write(f"Georef: {error}")

        ARCHIVO_CALLES.parent.mkdir(exist_ok=True)
        ARCHIVO_CALLES.write_text(json.dumps(sorted(calles), ensure_ascii=False, indent=0),
                                  encoding="utf-8")
        ARCHIVO_BARRIOS.write_text(json.dumps(dict(sorted(barrios.items())), ensure_ascii=False, indent=0),
                                   encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(
            f"{len(calles)} calles y {len(barrios)} barrios guardados en {ARCHIVO_CALLES.parent}."))
