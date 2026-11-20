from datetime import date, time

from django.contrib.auth.models import Group, Permission, User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from partes.codigos_intervencion import CODIGOS_INTERVENCION
from partes.geocodificacion import barrios_del_mapa
from partes.models import (
    AsistenciaCapacitacion, Capacitacion, Mantenimiento, ParticipacionMantenimiento,
    Barrio, CentroSalud, Combustible, Damnificado, EntidadIntervino, EstadoIntegrante,
    EstadoParte, Integrante, Motivo, NumeroMerito, ParteSiniestro, PartePersonal,
    PersonalE100, Rango, TipoEntidad, TipoSiniestro, TipoVehiculo, Vehiculo,
    VehiculoSiniestrado,
)

# De mayor a menor jerarquía, agrupados por escalafón. El primero es la cabeza del cuerpo.
ESCALAFONES = {
    "oficial": ["Comisario General", "Comisario Mayor", "Comisario Inspector", "Comisario", "Subcomisario",
                "Oficial Principal", "Oficial Inspector", "Oficial Subinspector", "Oficial Ayudante"],
    "suboficial": ["Suboficial Mayor", "Suboficial Principal", "Sargento Ayudante",
                   "Sargento Primero", "Sargento", "Cabo Primero", "Cabo"],
    "bombero": ["Bombero", "Aspirante"],
}
RANGOS = [(nombre, escalafon) for escalafon, nombres in ESCALAFONES.items() for nombre in nombres]
# Nombres que usábamos antes de tener la lista de orden operativa del cuartel.
RANGOS_RENOMBRADOS = {"Comandante General": "Comisario General", "Comandante Mayor": "Comisario Mayor",
                      "Comandante": "Comisario", "Subcomandante": "Subcomisario"}
ESTADOS_INTEGRANTE = ["Activo", "Aspirante", "Retirado", "Reserva", "Auxiliar", "Baja"]
ESTADOS_PARTE = ["Iniciado", "En curso", "Cerrado"]
# Los "códigos de intervención" del cuartel están en partes/codigos_intervencion.py: la misma
# lista que usa la migración 0026. Acá solo se crea lo que falte (instalación nueva).
# Tipos en los que se marca si el bombero cobró: los de la planilla con se_cobra, salvo que el
# cuartel lo haya cambiado en el admin.
TIPOS_ENTIDAD = [
    "Policía", "Tránsito", "Forense", "Juzgado", "Gendarmería", "Fiscalía", "Guardia Local",
]
COMBUSTIBLES = ["Gas Oil", "Nafta", "GNC"]
TIPOS_VEHICULO = ["Autobomba", "Cisterna", "Unidad de Rescate", "Unidad sanitaria",
                  "Camioneta de apoyo"]
# Centros de Villa María (01/11): salen de cartillas de prestadores y guías locales, falta que el
# cuartel confirme cuáles atienden emergencias. Se editan en el admin; "Otro" siempre está disponible.
CENTROS_SALUD = {
    "Hospital Regional Louis Pasteur": {},
    "Sanatorio Cruz Azul": {"direccion": "San Juan 1255", "telefono": "0353 4527174"},
    "Clínica de Especialidades": {"direccion": "Corrientes 733", "telefono": "0353 4646500"},
    "Sanatorio de La Cañada": {"direccion": "Coronel Larrabure 1466", "telefono": "0353 4618900"},
    "Clínica Privada del Niño": {"direccion": "José Ingenieros 223"},
    "Clínica Dr. Gregorio Marañón": {"direccion": "Catamarca 1364", "telefono": "0353 4523119"},
    "Clínica San Martín": {"direccion": "Mendoza 1237", "telefono": "0353 4520304"},
    "Fusavim": {"direccion": "Chile 67"},
}

# Roles (05/11, decisión del usuario): bombero, guardia, Estadística y jefe, que es un admin.
# Completar un parte (datos del lugar, damnificados, negativas, fotos) lo puede hacer cualquier
# integrante logueado. El bombero ve solo los partes en curso: ver los cerrados y los tomos va con view_periodo.
# El guardia crea y cierra partes y carga capacitaciones y mantenimientos. Estadística (el tutor)
# administra bomberos, números, períodos, móviles y calles. El jefe puede todo eso.
CONSULTA = ["view_partesiniestro", "view_capacitacion", "view_mantenimiento", "view_integrante"]
PERMISOS_GUARDIA = [
    "add_partesiniestro", "view_partesiniestro", "cerrar_partesiniestro", "view_periodo",
    "add_capacitacion", "change_capacitacion", "view_capacitacion",
    "add_mantenimiento", "change_mantenimiento", "view_mantenimiento", "view_vehiculo",
    "add_anotacionlibroguardia", "view_anotacionlibroguardia",
    "add_ordenentrega", "change_ordenentrega", "view_ordenentrega",
]
PERMISOS_ESTADISTICA = [
    *CONSULTA, "view_periodo", "gestionar_periodo",
    "add_capacitacion", "change_capacitacion",
    "add_mantenimiento", "change_mantenimiento",
    "add_integrante", "change_integrante",
    "add_numeromerito", "change_numeromerito", "view_numeromerito",
    "add_familiar", "change_familiar", "delete_familiar", "view_familiar",
    "add_historialintegrante", "change_historialintegrante", "delete_historialintegrante",
    "view_historialintegrante",
    "add_vehiculo", "change_vehiculo", "delete_vehiculo", "view_vehiculo",
    "add_barrio", "change_barrio", "view_barrio",
    "add_calle", "change_calle", "delete_calle", "view_calle",
    "add_personale100", "change_personale100", "view_personale100", "view_anotacionlibroguardia",
    "add_ordenentrega", "change_ordenentrega", "view_ordenentrega",
]
# Roles del 10/11: Graduado (N° 103 a 199) por ahora hace lo mismo que el guardia, salvo anotar en el
# libro de guardia (lo ve; anotan solo el Jefe y el guardia). Aspirante, como Bombero. Invitado
# (auxiliares y retirados) solo ve: tampoco completa partes (roles.puede_completar).
# La constancia de intervención la emiten solo el Jefe y Estadística.
PERMISOS_GRADUADO = [p for p in PERMISOS_GUARDIA if p != "add_anotacionlibroguardia"]
ROLES = {
    "Bombero": ["view_partesiniestro"],
    "Aspirante": ["view_partesiniestro"],
    "Invitado": ["view_partesiniestro"],
    "Personal de guardia": PERMISOS_GUARDIA,
    "Graduado": PERMISOS_GRADUADO,
    "Estadística": [*PERMISOS_ESTADISTICA, "emitir_constancia"],
    "Jefe": [*PERMISOS_GUARDIA, *PERMISOS_ESTADISTICA, "change_partesiniestro", "ver_confidencial",
             "corregir_cerrado", "corregir_libro", "emitir_constancia"],
}
ROLES_RENOMBRADOS = {"Jefe de Cuerpo Activo": "Jefe"}
ROLES_QUE_YA_NO_ESTAN = ["Instructor", "Administración"]
# Usuarios de la demo que no son Personal de guardia.
ROLES_DEMO = {"jefe": "Jefe", "estadistica": "Estadística"}


def cargar_lista(modelo, campo, valores):
    creados = 0
    for valor in valores:
        _, creado = modelo.objects.get_or_create(**{campo: valor})
        creados += creado
    return creados


def cargar_codigos_intervencion():
    """Crea los tipos y motivos que falten, reconociéndolos por código: si el cuartel cambia una
    descripción o un detalle en el admin, no se vuelve a crear ni se pisa.

    El catálogo completo lo rehace la migración 0026; acá solo se completa lo que falte, que en
    una instalación nueva es todo."""
    creados = 0
    cobran = []
    for codigo, descripcion, parte, secciones, se_cobra, motivos in CODIGOS_INTERVENCION:
        if se_cobra:
            cobran.append(codigo)
        tipo = TipoSiniestro.objects.filter(codigo=codigo).first()
        if tipo is None:
            tipo = TipoSiniestro.objects.create(codigo=codigo, descripcion=descripcion, parte=parte,
                                                secciones=secciones)
            creados += 1
        for codigo_motivo, descripcion_motivo, detalle, activo in motivos:
            motivo = Motivo.objects.filter(codigo=codigo_motivo).first()
            if motivo is None:
                motivo = Motivo.objects.create(codigo=codigo_motivo, descripcion=descripcion_motivo,
                                               detalle=detalle, activo=activo)
                creados += 1
            if not motivo.tipos.exists():
                motivo.tipos.add(tipo)
    if not TipoSiniestro.objects.filter(se_cobra=True).exists():
        TipoSiniestro.objects.filter(codigo__in=cobran).update(se_cobra=True)
    return creados


def cargar_centros_salud():
    creados = 0
    for nombre, datos in CENTROS_SALUD.items():
        _, creado = CentroSalud.objects.get_or_create(nombre=nombre, defaults=datos)
        creados += creado
    return creados


def cargar_barrios():
    """Suma los barrios de partes/datos/barrios.json; no toca los que el cuartel editó."""
    creados = 0
    for nombre, (latitud, longitud) in barrios_del_mapa().items():
        _, creado = Barrio.objects.get_or_create(
            nombre=nombre, defaults={"latitud": latitud, "longitud": longitud})
        creados += creado
    return creados


class Command(BaseCommand):
    help = ("Carga los catálogos iniciales (rangos, tipos, motivos, entidades, etc.). "
            "Con --demo agrega móviles, integrantes, usuarios y partes de ejemplo. "
            "Se puede correr varias veces: no duplica nada.")

    def add_arguments(self, parser):
        parser.add_argument("--demo", action="store_true",
                            help="Agrega datos de ejemplo para mostrar el sistema.")

    @transaction.atomic
    def handle(self, *args, **options):
        for viejo, nuevo in RANGOS_RENOMBRADOS.items():
            if not Rango.objects.filter(nombre_rango=nuevo).exists():
                Rango.objects.filter(nombre_rango=viejo).update(nombre_rango=nuevo)
        for orden, (nombre, escalafon) in enumerate(RANGOS, start=1):
            Rango.objects.update_or_create(
                nombre_rango=nombre, defaults={"orden_jerarquico": orden, "escalafon": escalafon})
        creados = {
            "estados de integrante": cargar_lista(EstadoIntegrante, "nombre_estado", ESTADOS_INTEGRANTE),
            "estados de parte": cargar_lista(EstadoParte, "nombre_estado", ESTADOS_PARTE),
            "tipos y motivos": cargar_codigos_intervencion(),
            "tipos de entidad": cargar_lista(TipoEntidad, "descripcion", TIPOS_ENTIDAD),
            "combustibles": cargar_lista(Combustible, "descripcion", COMBUSTIBLES),
            "tipos de vehículo": cargar_lista(TipoVehiculo, "descripcion", TIPOS_VEHICULO),
            "centros de salud": cargar_centros_salud(),
            "barrios": cargar_barrios(),
        }
        for viejo, nuevo in ROLES_RENOMBRADOS.items():
            if not Group.objects.filter(name=nuevo).exists():
                Group.objects.filter(name=viejo).update(name=nuevo)
        Group.objects.filter(name__in=ROLES_QUE_YA_NO_ESTAN).delete()
        for rol, codigos in ROLES.items():
            grupo, _ = Group.objects.get_or_create(name=rol)
            grupo.permissions.set(Permission.objects.filter(
                content_type__app_label="partes", codename__in=codigos))
        creados["roles (grupos)"] = len(ROLES)
        for nombre, cantidad in creados.items():
            self.stdout.write(f"  {nombre}: {cantidad} nuevos")
        self.stdout.write(self.style.SUCCESS("Catálogos cargados."))

        if options["demo"]:
            self.cargar_demo()

    def cargar_demo(self):
        gas_oil = Combustible.objects.get(descripcion="Gas Oil")
        moviles = [
            ("1", "Autobomba", "Mercedes-Benz", "Atego 1726", 84250),
            ("2", "Cisterna", "Ford", "Cargo 1722", 120310),
            ("3", "Unidad de Rescate", "Toyota", "Hilux", 56780),
        ]
        for legajo, tipo, marca, modelo, km in moviles:
            Vehiculo.objects.get_or_create(legajo=legajo, defaults={
                "tipo_vehiculo": TipoVehiculo.objects.get(descripcion=tipo),
                "marca": marca, "modelo": modelo, "combustible": gas_oil,
                "kilometraje_actual": km,
            })

        activo = EstadoIntegrante.objects.get(nombre_estado="Activo")
        anio = date.today().year
        integrantes = [
            # usuario, legajo, apellido, nombre, dni, rango, número de mérito
            ("guardia", "1001", "Gómez", "Lucía", "30111222", "Sargento", 115),
            ("jefe", "1002", "Martínez", "Carlos", "25333444", "Comisario General", 101),
            ("estadistica", "1005", "Bustos", "Pablo", "33222111", "Sargento Primero", 120),
            (None, "1003", "Rossi", "Mateo", "38555666", "Bombero", 142),
            (None, "1004", "Fernández", "Sofía", "40777888", "Cabo", 131),
        ]
        por_legajo = {}
        for usuario, legajo, apellido, nombre, dni, rango, merito in integrantes:
            rango = Rango.objects.get(nombre_rango=rango)
            user = None
            if usuario:
                user, creado = User.objects.get_or_create(username=usuario, defaults={
                    "first_name": nombre, "last_name": apellido})
                if creado:
                    user.set_password("bomberos2026")
                    user.save()
                rol = ROLES_DEMO.get(usuario, "Personal de guardia")
                user.groups.add(Group.objects.get(name=rol))
            integrante, _ = Integrante.objects.get_or_create(legajo=legajo, defaults={
                "usuario": user, "apellido": apellido, "nombre": nombre, "dni": dni,
                "rango": rango, "estado": activo, "localidad": "Villa María",
            })
            if integrante.rango != rango:  # demo: el jefe es la cabeza del cuerpo
                integrante.rango = rango
                integrante.save(update_fields=["rango"])
            NumeroMerito.objects.get_or_create(integrante=integrante, anio=anio, defaults={
                "numero_merito": merito, "rango": rango})
            por_legajo[legajo] = integrante

        PersonalE100.objects.get_or_create(apellido_nombre="Ambrosio, Juan Carlos",
                                                     defaults={"telefono": "353 4123000"})
        por_legajo["1004"].matricula_profesional = por_legajo["1004"].matricula_profesional or "ENF-4521"
        por_legajo["1004"].save()

        if not User.objects.filter(username="admin").exists():
            User.objects.create_superuser("admin", "", "admin1234")

        if not ParteSiniestro.objects.exists():
            self.cargar_partes_demo(por_legajo)
        if not Capacitacion.objects.exists() and not Mantenimiento.objects.exists():
            self.cargar_capacitacion_y_mantenimiento_demo(por_legajo)

        self.stdout.write(self.style.SUCCESS(
            "Demo cargada. Usuarios: guardia / jefe / estadistica (clave bomberos2026), admin / admin1234."))

    def cargar_capacitacion_y_mantenimiento_demo(self, por_legajo):
        hoy = date.today()
        guardia, jefe = por_legajo["1001"], por_legajo["1002"]
        capacitacion = Capacitacion.objects.create(
            fecha=hoy, hora_inicio=time(9, 0), hora_fin=time(12, 0), datos="Uso de equipos de respiración autónoma",
            obligatoria=True, modalidad=Capacitacion.Modalidad.INTERNA, sentido=Capacitacion.Sentido.DICTADA,
            nivel=Capacitacion.Nivel.CUARTEL, destinos="aspirantes_1,aspirantes_2,era", lugar="Cuartel central",
            a_cargo_integrante=jefe, guardia=guardia)
        capacitacion.ayudantes.add(jefe)
        for legajo in ("1001", "1003", "1004"):
            AsistenciaCapacitacion.objects.create(capacitacion=capacitacion, integrante=por_legajo[legajo])
        mantenimiento = Mantenimiento.objects.create(
            fecha=hoy, hora_inicio=time(18, 0), hora_fin=time(20, 30), vehiculo=Vehiculo.objects.get(legajo="2"),
            tipo=Mantenimiento.Tipo.CORRECTIVO, motivo="Pérdida en la bomba",
            descripcion="Se cambió el retén de la bomba y se probó a presión.", autorizo=jefe,
            a_cargo=por_legajo["1004"], controlo=jefe, registro_libros=True, costo_repuestos=18500,
            horas_taller=2.5, costo_hora=6000, guardia=guardia)
        for legajo in ("1003", "1004"):
            ParticipacionMantenimiento.objects.create(mantenimiento=mantenimiento, integrante=por_legajo[legajo])

    def cargar_partes_demo(self, por_legajo):
        guardia, jefe = por_legajo["1001"], por_legajo["1002"]
        iniciado = EstadoParte.objects.get(nombre_estado="Iniciado")
        cerrado = EstadoParte.objects.get(nombre_estado="Cerrado")
        hoy = date.today()

        accidente = ParteSiniestro.objects.create(
            fecha=hoy, hora_llamada=time(14, 5), telefono_origen="353 4123456",
            guardia=guardia, autorizo=jefe, personal_e100=PersonalE100.objects.order_by("pk").first(),
            tipo=TipoSiniestro.objects.get(descripcion="Accidentes"),
            motivo=Motivo.objects.get(codigo="111"), estado_parte=iniciado,
            ubicacion_calle="Bv. Sarmiento", ubicacion_numero="1450", barrio="Centro",
            vehiculo=Vehiculo.objects.get(legajo="3"), hora_salida=time(14, 8),
            hora_llegada=time(14, 15),
        )
        for integrante in (jefe, por_legajo["1003"], por_legajo["1004"]):
            PartePersonal.objects.create(parte=accidente, integrante=integrante)
        EntidadIntervino.objects.create(
            parte=accidente, tipo_entidad=TipoEntidad.objects.get(descripcion="Policía"),
            numero_movil="4521", a_cargo="Cabo Pereyra")
        rodado = VehiculoSiniestrado.objects.create(
            parte=accidente, rodado_numero=1, marca="Fiat", modelo="Cronos", dominio="AE123CD")
        Damnificado.objects.create(
            vehiculo_siniestrado=rodado, apellido_nombre="López, Ana", edad=34,
            rol=Damnificado.Rol.CONDUCTOR, triage=Damnificado.Triage.AMARILLO,
            lesion="Fractura de brazo", sexo="F",
            trasladado=True, trasladado_por="Unidad sanitaria 107",
            hacia=CentroSalud.objects.first())
        Damnificado.objects.create(
            vehiculo_siniestrado=rodado, apellido_nombre="López, Tomás", edad=9,
            rol=Damnificado.Rol.ACOMPANANTE, triage=Damnificado.Triage.VERDE,
            lesion="Escoriaciones", sexo="M")

        incendio = ParteSiniestro.objects.create(
            fecha=hoy, hora_llamada=time(3, 40), toque_sirena=True, guardia=guardia,
            tipo=TipoSiniestro.objects.get(descripcion="Incendio"),
            motivo=Motivo.objects.get(codigo="108"), estado_parte=cerrado,
            ubicacion_calle="Ruta 9", referencias="Km 553, frente a la estación de servicio",
            vehiculo=Vehiculo.objects.get(legajo="1"), hora_salida=time(3, 44),
            hora_llegada=time(3, 58), hora_regreso=time(5, 10), km_regreso=84290,
            cerro=jefe, fecha_cierre=timezone.now(),
            observaciones="Foco controlado sin propagación.",
        )
        for integrante in (guardia, por_legajo["1003"]):
            PartePersonal.objects.create(parte=incendio, integrante=integrante)
        movil = incendio.vehiculo
        movil.kilometraje_actual = incendio.km_regreso
        movil.save()
