"""Crea, por única vez, un usuario para cada integrante, con el rol que le toca por su N° de mérito.

Decisión del usuario (10/11), para ahorrar la carga a mano: los roles NO se recalculan después,
aunque cambien los números de mérito de otro año.

    Activo, N° 101-102 ............ Jefe
    Activo, N° 103-199 ............ Graduado
    Activo, N° 201-299 ............ Bombero
    Activo o Aspirante, N° 301-399  Aspirante
    Auxiliar o Retirado ........... Invitado
    Baja, Reserva o sin N° ........ sin usuario (se listan)

Más los roles extra de GUARDIAS y ESTADISTICA. Usuario: inicial del nombre + apellido, en minúsculas
y sin tildes (Augusto Doffo -> adoffo); si dos coinciden, a los dos se les agrega su N° de mérito.
Clave inicial: el usuario + "_358" (cada uno la cambia desde Mi perfil).

Además saca del medio los usuarios de la prueba (guardia1-3, jefe, bombero, estadistica), que estaban
vinculados a integrantes reales, y deja la cuenta admin como superusuario sin integrante.
"""
import csv
import re
import unicodedata
from collections import defaultdict

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from partes import roles
from partes.models import Integrante

SUFIJO_CLAVE = "_358"
USUARIOS_DE_PRUEBA = ["guardia1", "guardia2", "guardia3", "jefe", "bombero", "estadistica"]
CUENTA_ADMIN = "admin"
# Roles que se suman al que da el número: (N° de mérito, apellido) para ubicar a cada uno.
GUARDIAS = [(212, "Leyba"), (324, "De los Dolores"), (236, "Turaglio"), (264, "Spat")]
ESTADISTICA = [(114, "Doffo")]
# Nombres que conviene revisar antes de repartir el usuario: abreviados ("D", "Fco.") y los que
# parecen tener parte del apellido en el nombre.
ABREVIADO = re.compile(r"^\w{1,3}\.?$")
APELLIDO_EN_EL_NOMBRE = [("Perez", "Zamora Gonzalo")]


def normalizar(texto):
    """Minúsculas, sin tildes ni ñ, solo letras: "De los Dolores" -> "delosdolores"."""
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", texto)


def usuario_base(integrante):
    return normalizar(integrante.nombre)[:1] + normalizar(integrante.apellido)


def rol_por_numero(estado, numero):
    """El rol que da el N° de mérito, o None si no le corresponde usuario."""
    if estado in ("Auxiliar", "Retirado"):
        return roles.INVITADO if numero else None
    if estado not in ("Activo", "Aspirante") or not numero:
        return None
    if numero in (101, 102):
        return roles.JEFE
    if 103 <= numero <= 199:
        return roles.GRADUADO
    if 201 <= numero <= 299:
        return roles.BOMBERO
    if 301 <= numero <= 399:
        return roles.ASPIRANTE
    return None


class Command(BaseCommand):
    help = ("Crea por única vez el usuario de cada integrante con su rol según el N° de mérito "
            "(clave inicial: usuario + _358) y quita los usuarios de la prueba.")

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Muestra lo que haría, sin guardar nada.")
        parser.add_argument("--anio", type=int, default=None,
                            help="Año de los números de mérito (por defecto, el actual).")
        parser.add_argument("--salida", help="Guarda el listado (con las claves iniciales) en este CSV.")

    def handle(self, *args, **options):
        anio = options["anio"] or timezone.localdate().year
        faltan = [r for r in roles.PRIORIDAD if not Group.objects.filter(name=r).exists()]
        if faltan:
            raise CommandError(f"Faltan los roles {', '.join(faltan)}: corré antes cargar_catalogos.")
        with transaction.atomic():
            self.limpiar_prueba()
            filas = self.crear(anio)
            if options["dry_run"]:
                transaction.set_rollback(True)
        if options["salida"] and not options["dry_run"]:
            with open(options["salida"], "w", newline="", encoding="utf-8-sig") as archivo:
                escritor = csv.writer(archivo)
                escritor.writerow(["N° mérito", "Apellido y nombre", "Usuario", "Clave inicial", "Roles"])
                escritor.writerows(filas)
            self.stdout.write(self.style.WARNING(
                f"El listado {options['salida']} tiene las claves iniciales: guardalo en un lugar seguro "
                "y borralo después de repartirlas."))
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("--dry-run: no se guardó nada."))

    # --- Usuarios de la prueba y cuenta admin -------------------------------------------------

    def limpiar_prueba(self):
        self.stdout.write("Usuarios de la prueba:")
        for nombre in USUARIOS_DE_PRUEBA:
            user = User.objects.filter(username=nombre).first()
            if user is None:
                continue
            vinculado = Integrante.objects.filter(usuario=user).first()
            Integrante.objects.filter(usuario=user).update(usuario=None)
            de = f" (estaba vinculado a {vinculado.apellido}, {vinculado.nombre})" if vinculado else ""
            try:
                with transaction.atomic():
                    user.delete()
                self.stdout.write(f"  {nombre}: borrado{de}.")
            except (ProtectedError, IntegrityError):
                # Firmó partes o anotó en el libro: no se borra nada de eso; queda desactivado.
                user.is_active = False
                user.save(update_fields=["is_active"])
                user.groups.clear()
                self.stdout.write(f"  {nombre}: tiene registros a su nombre (firmas, anotaciones...): "
                                  f"queda desactivado y sin roles{de}.")
        admin = User.objects.filter(username=CUENTA_ADMIN).first()
        if admin:
            vinculado = Integrante.objects.filter(usuario=admin).first()
            if vinculado:
                Integrante.objects.filter(usuario=admin).update(usuario=None)
                self.stdout.write(f"  {CUENTA_ADMIN}: queda como superusuario sin integrante "
                                  f"(estaba vinculado a {vinculado.apellido}, {vinculado.nombre}).")

    # --- Usuarios de los integrantes ----------------------------------------------------------

    def ubicar(self, lista, por_numero, que):
        encontrados = []
        for numero, apellido in lista:
            integrante = por_numero.get(numero)
            if integrante is None or normalizar(integrante.apellido) != normalizar(apellido):
                raise CommandError(f"No encontré a {apellido} con el N° {numero} ({que}): revisá antes de seguir.")
            encontrados.append(integrante)
        return encontrados

    def crear(self, anio):
        integrantes = list(Integrante.objects.select_related("estado", "usuario").order_by("apellido", "nombre"))
        numeros = {i.pk: i.numero_merito_en(anio) for i in integrantes}
        por_numero = defaultdict(list)
        for i in integrantes:
            if numeros[i.pk]:
                por_numero[numeros[i.pk]].append(i)
        repetidos = [n for n, lista in por_numero.items() if len(lista) > 1]
        if repetidos:
            raise CommandError(f"Números de mérito {anio} repetidos: {repetidos}.")
        por_numero = {n: lista[0] for n, lista in por_numero.items()}
        extras = defaultdict(list)
        for integrante in self.ubicar(GUARDIAS, por_numero, roles.GUARDIA):
            extras[integrante.pk].append(roles.GUARDIA)
        for integrante in self.ubicar(ESTADISTICA, por_numero, roles.ESTADISTICA):
            extras[integrante.pk].append(roles.ESTADISTICA)

        nuevos, sin_usuario, ya_tenian, dudosos = [], [], [], []
        for i in integrantes:
            rol = rol_por_numero(i.estado.nombre_estado if i.estado_id else "", numeros[i.pk])
            if i.usuario_id:
                ya_tenian.append(i)
            elif rol is None:
                sin_usuario.append(i)
            else:
                nuevos.append((i, [rol, *extras[i.pk]]))
                if ABREVIADO.match(i.nombre.strip()) or (i.apellido, i.nombre) in APELLIDO_EN_EL_NOMBRE:
                    dudosos.append(i)

        # Usuarios repetidos (entre sí o con uno que ya existe): a todos se les agrega el N° de mérito.
        cuenta = defaultdict(int)
        for i, _ in nuevos:
            cuenta[usuario_base(i)] += 1
        existentes = set(User.objects.values_list("username", flat=True))
        filas = []
        self.stdout.write(f"Usuarios nuevos ({len(nuevos)}):")
        for i, roles_de in nuevos:
            usuario = usuario_base(i)
            if cuenta[usuario] > 1 or usuario in existentes:
                usuario = f"{usuario}{numeros[i.pk]}"
            if usuario in existentes:
                raise CommandError(f"El usuario {usuario} ya existe: revisá a {i.apellido}, {i.nombre}.")
            existentes.add(usuario)
            clave = usuario + SUFIJO_CLAVE
            user = User(username=usuario, first_name=i.nombre, last_name=i.apellido)
            user.set_password(clave)
            user.save()
            user.groups.set(Group.objects.filter(name__in=roles_de))
            i.usuario = user
            i.save(update_fields=["usuario"])
            nombre = f"{i.apellido}, {i.nombre}"
            filas.append([numeros[i.pk], nombre, usuario, clave, " + ".join(roles_de)])
            self.stdout.write(f"  {numeros[i.pk]:>3}  {nombre:<32} {usuario:<18} {clave:<22} {' + '.join(roles_de)}")
        if ya_tenian:
            self.stdout.write(f"Ya tenían usuario, no se tocaron ({len(ya_tenian)}):")
            for i in ya_tenian:
                self.stdout.write(f"  {numeros[i.pk] or '-':>3}  {i.apellido}, {i.nombre}: {i.usuario.username}")
        if sin_usuario:
            self.stdout.write(f"Sin usuario ({len(sin_usuario)}):")
            for i in sin_usuario:
                motivo = (f"sin N° de mérito {anio}" if not numeros[i.pk]
                          else f"estado {i.estado} con el N° {numeros[i.pk]}")
                self.stdout.write(f"  {i.apellido}, {i.nombre}: {motivo}")
        if dudosos:
            self.stdout.write(self.style.WARNING("Revisar el nombre antes de repartir el usuario:"))
            for i in dudosos:
                self.stdout.write(f"  {numeros[i.pk]}  apellido «{i.apellido}», nombre «{i.nombre}»")
        self.stdout.write(self.style.SUCCESS(f"{len(nuevos)} usuarios nuevos. Clave inicial: usuario + {SUFIJO_CLAVE}."))
        return filas
