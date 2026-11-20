"""Carga el Personal E-100 del 10/11, vinculado a su integrante (auxiliares y dos retirados).

    python manage.py cargar_e100 [--dry-run]

No duplica: si ya hay un E-100 con el mismo nombre o el mismo integrante, lo deja como está.
Cada integrante se ubica por N° de mérito y se confirma por apellido; si no coincide, frena.
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from partes.management.commands.crear_usuarios_integrantes import normalizar
from partes.models import Integrante, NumeroMerito, PersonalE100

DESDE = date(2026, 11, 1)
# (apellido y nombre como se carga, N° de mérito del integrante, apellido del integrante)
PERSONAL = [
    ("SALGAROLO Rita", 402, "Salgarolo"),
    ("STECHINA Alcides", 403, "Stechina"),
    ("ALTAMIRANDA Jorge", 404, "Altamiranda"),
    ("AMBROSIO Juan", 405, "Ambrosio"),  # no es Ambrosio Nazareno (319)
    ("CABRERA Constanza", 406, "Cabrera"),
    ("TABERNA Daniel", 609, "Taberna"),  # retirado, cargado como "Taberna, D"
    ("DÁVILA Sergio", 616, "Davila"),  # retirado
    ("BORDINO Mauro Jose", 408, "Bordino"),
    ("MAFFEI Stefania", 409, "Maffei"),
]


class Command(BaseCommand):
    help = "Carga el Personal E-100 (desde el 01/11/2026), vinculado a cada integrante."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Muestra lo que haría, sin guardar nada.")
        parser.add_argument("--anio", type=int, default=None, help="Año de los N° de mérito (por defecto, el actual).")

    def handle(self, *args, **options):
        anio = options["anio"] or timezone.localdate().year
        with transaction.atomic():
            for apellido_nombre, numero, apellido in PERSONAL:
                nm = NumeroMerito.objects.filter(anio=anio, numero_merito=numero).select_related("integrante").first()
                integrante = nm.integrante if nm else None
                if integrante is None or normalizar(integrante.apellido) != normalizar(apellido):
                    candidatos = Integrante.objects.filter(apellido__iexact=apellido)
                    raise CommandError(
                        f"{apellido_nombre}: el N° {numero}/{anio} no es de {apellido}. Integrantes con ese "
                        f"apellido: {', '.join(f'{i.apellido}, {i.nombre}' for i in candidatos) or 'ninguno'}. "
                        "Revisalo antes de vincular.")
                existente = (PersonalE100.objects.filter(integrante=integrante).first()
                             or PersonalE100.objects.filter(apellido_nombre__iexact=apellido_nombre).first())
                if existente:
                    self.stdout.write(f"  = {apellido_nombre}: ya estaba ({existente}).")
                    continue
                PersonalE100.objects.create(apellido_nombre=apellido_nombre, fecha_asignacion=DESDE,
                                            activo=True, integrante=integrante)
                self.stdout.write(f"  + {apellido_nombre} -> {integrante.apellido}, {integrante.nombre} "
                                  f"(N° {numero}, {integrante.estado})")
            if options["dry_run"]:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("--dry-run: no se guardó nada."))
            else:
                self.stdout.write(self.style.SUCCESS("Personal E-100 cargado."))
