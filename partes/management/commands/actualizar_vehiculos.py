"""Actualiza combustibles, tipos de vehículo y vehículos desde los CSV exportados de la base (10/11).

    python manage.py actualizar_vehiculos --dir partes/datos/vehiculos [--dry-run]

Lee partes_combustible*.csv, partes_tipovehiculo*.csv y partes_vehiculo*.csv (UTF-8, con encabezado),
en ese orden por las claves foráneas. Cada registro se actualiza o se crea por su id. No se borra
nada: los vehículos que no están en el CSV se listan (pueden estar en partes, capacitaciones o
mantenimientos). El kilometraje no se baja: lo actualizan los cierres de partes.
"""
import csv
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from partes.models import Combustible, TipoVehiculo, Vehiculo

BOOLEANOS = {"es_propiedad_cuerpo", "en_servicio", "dado_de_baja"}
FECHAS = {"fecha_adquisicion", "fecha_vto_verificacion"}
ENTEROS = {"anio", "kilometraje_actual", "combustible_id", "tipo_vehiculo_id"}


def leer(carpeta, prefijo):
    archivos = sorted(Path(carpeta).glob(f"{prefijo}*.csv"))
    if not archivos:
        raise CommandError(f"No encontré {prefijo}*.csv en {carpeta}.")
    if len(archivos) > 1:
        raise CommandError(f"Hay más de un {prefijo}*.csv en {carpeta}: dejá solo el que corresponde.")
    with open(archivos[0], encoding="utf-8-sig", newline="") as archivo:
        return archivos[0].name, list(csv.DictReader(archivo))


def convertir(campo, texto):
    texto = (texto or "").strip()
    if campo in BOOLEANOS:
        if texto not in ("0", "1"):
            raise CommandError(f"{campo}: se esperaba 0 o 1 y vino «{texto}».")
        return texto == "1"
    if campo in FECHAS:
        return date.fromisoformat(texto) if texto else None
    if campo in ENTEROS:
        return int(texto) if texto else None
    return texto


class Command(BaseCommand):
    help = "Actualiza combustibles, tipos de vehículo y vehículos desde los CSV (por id, sin borrar nada)."

    def add_arguments(self, parser):
        parser.add_argument("--dir", default="partes/datos/vehiculos", help="Carpeta con los tres CSV.")
        parser.add_argument("--dry-run", action="store_true", help="Muestra lo que haría, sin guardar nada.")

    def handle(self, *args, **options):
        carpeta = options["dir"]
        with transaction.atomic():
            self.cargar(Combustible, *leer(carpeta, "partes_combustible"))
            self.cargar(TipoVehiculo, *leer(carpeta, "partes_tipovehiculo"))
            self.cargar(Vehiculo, *leer(carpeta, "partes_vehiculo"))
            if options["dry_run"]:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("--dry-run: no se guardó nada."))
            else:
                self.stdout.write(self.style.SUCCESS("Listo."))

    def cargar(self, modelo, nombre_archivo, filas):
        nombre = modelo._meta.verbose_name_plural
        campos = {f.attname for f in modelo._meta.concrete_fields} - {"id"}
        creados, modificados, iguales, avisos = [], [], 0, []
        leidas = {}
        for numero, fila in enumerate(filas, start=2):
            desconocidos = set(fila) - campos - {"id"}
            if desconocidos:
                raise CommandError(f"{nombre_archivo}: columnas que no existen en {nombre}: {sorted(desconocidos)}.")
            try:
                pk = int(fila["id"])
                leidas[pk] = {c: convertir(c, v) for c, v in fila.items() if c != "id"}
            except (KeyError, ValueError) as error:
                raise CommandError(f"{nombre_archivo}, renglón {numero}: {error}") from error
        self.controlar_unicos(modelo, nombre_archivo, leidas)
        ids = set(leidas)
        for pk, datos in leidas.items():
            actual = modelo.objects.filter(pk=pk).first()
            if actual is None:
                modelo.objects.create(pk=pk, **datos)
                creados.append(f"{pk}: {datos.get('descripcion') or datos.get('legajo')}")
                continue
            if "kilometraje_actual" in datos and (actual.kilometraje_actual or 0) > (datos["kilometraje_actual"] or 0):
                avisos.append(f"{actual.legajo}: queda con {actual.kilometraje_actual} km "
                              f"(el CSV dice {datos['kilometraje_actual']}; el km no se baja).")
                datos["kilometraje_actual"] = actual.kilometraje_actual
            cambios = {c: (getattr(actual, c), v) for c, v in datos.items() if getattr(actual, c) != v}
            if not cambios:
                iguales += 1
                continue
            for campo, (_, nuevo) in cambios.items():
                setattr(actual, campo, nuevo)
            actual.save()
            modificados.append(f"{pk} ({actual}): " + ", ".join(f"{c} {a!r} -> {n!r}" for c, (a, n) in cambios.items()))

        self.stdout.write(f"{nombre.capitalize()} ({nombre_archivo}): {len(creados)} nuevos, "
                          f"{len(modificados)} modificados, {iguales} sin cambios.")
        for texto in creados:
            self.stdout.write(f"  + {texto}")
        for texto in modificados:
            self.stdout.write(f"  ~ {texto}")
        for texto in avisos:
            self.stdout.write(self.style.WARNING(f"  ! {texto}"))
        fuera = modelo.objects.exclude(pk__in=ids)
        if fuera.exists():
            self.stdout.write(f"  En la base pero no en el CSV (no se borran): "
                              + ", ".join(f"{o.pk}: {o}" for o in fuera))

    def controlar_unicos(self, modelo, nombre_archivo, leidas):
        """Antes de guardar nada: si un valor único (el legajo del móvil, la descripción de un tipo) ya lo
        tiene otro registro con otro id, la base de destino no es la misma de donde salió el CSV.
        Se frena todo y se listan los choques, para decidir a mano en vez de pisar un móvil con otro."""
        choques = []
        for campo in modelo._meta.concrete_fields:
            if not campo.unique or campo.primary_key or campo.attname not in next(iter(leidas.values()), {}):
                continue
            en_csv = {pk: datos[campo.attname] for pk, datos in leidas.items()}
            repetidos = {v for v in en_csv.values() if list(en_csv.values()).count(v) > 1}
            choques += [f"{campo.name} «{v}» está repetido en el CSV" for v in sorted(map(str, repetidos))]
            for pk, valor in en_csv.items():
                otro = modelo.objects.filter(**{campo.attname: valor}).exclude(pk=pk).first()
                if otro is not None:
                    choques.append(f"id {pk}: {campo.name} «{valor}» ya lo tiene el id {otro.pk} ({otro}) en la base")
        if choques:
            raise CommandError(f"{nombre_archivo}: no se cargó nada, los ids del CSV no coinciden con la base:\n  "
                               + "\n  ".join(choques))
