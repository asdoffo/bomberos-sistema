"""Importa la "Lista orden operativa" del cuartel (Excel) como integrantes con su N° de mérito del año.

La planilla tiene tres bloques de columnas (N°, G, APELLIDO Y NOMBRE, P/A) que se leen de arriba abajo
y de izquierda a derecha. Los renglones con texto en la columna N° son títulos de sección
(OFICIALES SUPERIORES, BOMBEROS, ASPIRANTES, BRIGADA AUXILIAR, RETIRO EFECTIVO...).
"""
import re
import unicodedata
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from partes.models import EstadoIntegrante, Integrante, NumeroMerito, Rango

# El rango viene escrito al final del nombre ("RAMIREZ, José Of. Inspector"). Los de suboficial van
# antes que los de oficial porque "Sub. Of. Ppal." también termina como "Of. Ppal.".
OF = r"of(?:icial)?\.?\s*"
SUB_OF = r"sub\.?\s*" + OF
PATRONES_RANGO = [
    (r"cr(?:i|ri)o\.?\s*gral\.?|comisario\s+general", "Comisario General"),
    (r"cr(?:i|ri)o\.?\s*mayor\.?|comisario\s+mayor", "Comisario Mayor"),
    (r"cr(?:i|ri)o\.?\s*insp(?:ector)?\.?|comisario\s+inspector", "Comisario Inspector"),
    (r"sub\s*cr(?:i|ri)o\.?|subcomisario", "Subcomisario"),
    (r"cr(?:i|ri)o\.?|comisario", "Comisario"),
    (SUB_OF + r"mayor\.?|suboficial\s+mayor", "Suboficial Mayor"),
    (SUB_OF + r"(?:ppal|principal)\.?|suboficial\s+principal", "Suboficial Principal"),
    (OF + r"sub\.?\s*insp(?:ector)?\.?", "Oficial Subinspector"),
    (OF + r"(?:ppal|principal)\.?", "Oficial Principal"),
    (OF + r"insp(?:ector)?\.?", "Oficial Inspector"),
    (OF + r"ayud(?:ante)?\.?", "Oficial Ayudante"),
    (r"(?:sargento|sgto\.?)\s*ayud(?:ante)?\.?", "Sargento Ayudante"),
    (r"(?:sargento|sgto\.?)\s*(?:1ero|1°|1º|primero)\.?", "Sargento Primero"),
    (r"sargento|sgto\.?", "Sargento"),
    (r"cabo\s*(?:1ero|1°|1º|primero)\.?", "Cabo Primero"),
    (r"cabo\.?", "Cabo"),
    (r"bombero", "Bombero"),
]
CARGO = re.compile(r"[\s,]+([12])\s*[°º]?\s*jefe\s*$", re.IGNORECASE)

# Sección de la planilla -> (estado, rango si el nombre no trae uno).
SECCIONES = [
    ("ASPIRANTE", "Aspirante", "Aspirante"),
    ("BRIGADA AUXILIAR", "Auxiliar", "Bombero"),
    ("RETIRO", "Retirado", "Bombero"),
    ("BOMBEROS", "Activo", "Bombero"),
    ("OFICIAL", "Activo", "Bombero"),  # también SUB OFICIALES; el rango sale del nombre
]
BLOQUES = (0, 4, 8)  # columnas A, E, I
SUMA = re.compile(r"^=\(?\s*([A-Z]{1,2})(\d+)\s*\+\s*(\d+)\s*\)?$")

def separar_rango(texto):
    """'RAMIREZ, José Of. Inspector' -> ('RAMIREZ, José', 'Oficial Inspector', '')."""
    texto = " ".join(texto.split())
    rango = cargo = ""
    for patron, nombre in PATRONES_RANGO:
        encontrado = re.search(r"[\s,]+(?:" + patron + r")\s*$", texto, re.IGNORECASE)
        if encontrado:
            rango, texto = nombre, texto[:encontrado.start()]
            break
    encontrado = CARGO.search(texto)
    if encontrado:
        cargo, texto = f"{encontrado.group(1)}° Jefe", texto[:encontrado.start()]
    return texto.strip(" ,"), rango, cargo


def separar_nombre(texto):
    """Con coma: 'Apellido, Nombre'. Sin coma: el apellido es lo que está en mayúsculas al principio
    ('DAL MOLIN Fabricio') o, si no hay, la primera palabra ('Quintero Julian')."""
    if "," in texto:
        apellido, nombre = (parte.strip() for parte in texto.split(",", 1))
    else:
        palabras = texto.split()
        mayusculas = 0
        while (mayusculas < len(palabras) - 1 and len(palabras[mayusculas]) > 1
               and palabras[mayusculas].isupper()):
            mayusculas += 1
        corte = mayusculas or 1
        apellido, nombre = " ".join(palabras[:corte]), " ".join(palabras[corte:])
    if apellido.isupper():
        apellido = apellido.title()
    return apellido, nombre


def clave(apellido, nombre):
    """Para reconocer a la misma persona aunque cambien acentos o mayúsculas."""
    texto = unicodedata.normalize("NFKD", f"{apellido} {nombre}".lower())
    return " ".join("".join(c for c in texto if not unicodedata.combining(c)).split())


def leer_planilla(ruta):
    """Devuelve una fila por persona: número, grupo, apellido, nombre, rango, cargo, estado."""
    try:
        import openpyxl
    except ImportError as error:
        raise CommandError("Falta openpyxl: pip install -r requirements.txt") from error
    try:
        valores = openpyxl.load_workbook(ruta, data_only=True).worksheets[0]
        formulas = openpyxl.load_workbook(ruta).worksheets[0]
    except (OSError, ValueError) as error:
        raise CommandError(f"No se pudo abrir {ruta}: {error}") from error

    def valor(celda):
        """Los números de mérito son fórmulas (=(A39+1)). Si el archivo no trae el resultado
        guardado (lo guardó un programa que no calcula), se resuelve la suma."""
        dato, formula = valores[celda].value, formulas[celda].value
        suma = SUMA.match(formula.replace("$", "")) if dato is None and isinstance(formula, str) else None
        if suma:
            base = valor(suma[1] + suma[2])
            return base + int(suma[3]) if isinstance(base, (int, float)) else None
        return dato

    renglones = [[valor(c.coordinate) for c in fila] + [None] * 12 for fila in formulas.iter_rows()]
    personas, estado, rango_seccion = [], None, None
    for inicio in BLOQUES:
        for renglon in renglones:
            numero, grupo, texto = renglon[inicio:inicio + 3]
            if isinstance(numero, str):
                titulo = numero.strip().upper()
                for marca, estado_seccion, rango_por_defecto in SECCIONES:
                    if marca in titulo:
                        estado, rango_seccion = estado_seccion, rango_por_defecto
                        break
                continue  # títulos, encabezados (N°) y "Fecha:" / "Motivo:"
            if not isinstance(numero, (int, float)) or not texto or estado is None:
                continue
            nombre_completo, rango, cargo = separar_rango(str(texto))
            apellido, nombre = separar_nombre(nombre_completo)
            personas.append({
                "numero": int(numero), "grupo": int(grupo) if grupo in (1, 2, 3) else None,
                "apellido": apellido, "nombre": nombre, "cargo": cargo,
                "rango": rango or rango_seccion, "estado": estado,
            })
    return personas


class Command(BaseCommand):
    help = ("Importa la lista de orden operativa (Excel del cuartel) como integrantes con su N° de mérito "
            "del año. Si la persona ya existe (por N° del año o por apellido y nombre) la actualiza. "
            "DNI y legajo quedan provisorios para completar en la ficha.")

    def add_arguments(self, parser):
        parser.add_argument("archivo", help="Ruta del .xlsx (se lee la primera hoja).")
        parser.add_argument("--anio", type=int, default=date.today().year,
                            help="Año de los números de mérito (por defecto, el actual).")
        parser.add_argument("--probar", action="store_true",
                            help="Solo muestra cómo se leyó la planilla, sin guardar nada.")

    def handle(self, *args, **options):
        personas = leer_planilla(options["archivo"])
        if not personas:
            raise CommandError("No se encontró a nadie en la planilla.")
        repetidos = self.numeros_repetidos(personas)
        if options["probar"]:
            for p in personas:
                self.stdout.write(f"{p['numero']:03d}  G{p['grupo'] or '-'}  {p['apellido']}, {p['nombre']}  "
                                  f"| {p['rango']} | {p['estado']}{' | ' + p['cargo'] if p['cargo'] else ''}")
            self.stdout.write(f"{len(personas)} personas.")
            return
        if repetidos:
            raise CommandError("Hay números de mérito repetidos en la planilla; corregilos y volvé a importar:\n"
                               + "\n".join(repetidos))
        self.importar(personas, options["anio"])

    def numeros_repetidos(self, personas):
        vistos, repetidos = {}, []
        for p in personas:
            if p["numero"] in vistos:
                otro = vistos[p["numero"]]
                repetidos.append(f"  N° {p['numero']}: {otro['apellido']}, {otro['nombre']} y "
                                 f"{p['apellido']}, {p['nombre']}")
                self.stderr.write(self.style.WARNING(repetidos[-1]))
            vistos[p["numero"]] = p
        return repetidos

    @transaction.atomic
    def importar(self, personas, anio):
        rangos = {r.nombre_rango: r for r in Rango.objects.all()}
        estados = {e.nombre_estado: e for e in EstadoIntegrante.objects.all()}
        faltan = ({p["rango"] for p in personas} - rangos.keys()) | ({p["estado"] for p in personas} - estados.keys())
        if faltan:
            raise CommandError(f"Faltan en los catálogos: {', '.join(sorted(faltan))}. "
                               "Corré antes: python manage.py cargar_catalogos")
        por_numero = {n.numero_merito: n for n in NumeroMerito.objects.filter(anio=anio).select_related("integrante")}
        por_nombre = {clave(i.apellido, i.nombre): i for i in Integrante.objects.all()}
        creados = actualizados = 0
        for p in personas:
            existente = por_numero.get(p["numero"])
            integrante = por_nombre.get(clave(p["apellido"], p["nombre"]))
            if existente and existente.integrante != integrante:
                raise CommandError(f"El N° {p['numero']}/{anio} ya es de {existente.integrante}, no de "
                                   f"{p['apellido']}, {p['nombre']}. Importá sobre una base sin la demo.")
            if integrante is None:
                integrante = Integrante(legajo=f"PROV-{anio}-{p['numero']:03d}",
                                        dni=str(90000000 + anio % 100 * 1000 + p["numero"]),
                                        localidad="Villa María")
                creados += 1
            else:
                actualizados += 1
            integrante.apellido, integrante.nombre = p["apellido"], p["nombre"]
            integrante.rango, integrante.estado = rangos[p["rango"]], estados[p["estado"]]
            integrante.grupo_guardia = p["grupo"]
            if p["cargo"]:
                integrante.cargo = p["cargo"]
                integrante.es_jefe_activo = p["cargo"] == "1° Jefe"
            integrante.save()
            NumeroMerito.objects.update_or_create(integrante=integrante, anio=anio, defaults={
                "numero_merito": p["numero"], "rango": integrante.rango})
        self.stdout.write(self.style.SUCCESS(
            f"Listado {anio}: {creados} integrantes nuevos y {actualizados} actualizados."))
        if creados:
            self.stdout.write("DNI y legajo de los nuevos son provisorios (legajo PROV-...): completarlos en la ficha.")
