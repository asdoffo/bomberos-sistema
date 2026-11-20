import hashlib
import json
import uuid
from datetime import datetime, time, timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from . import validadores


# ---------------------------------------------------------------------------
# Catálogos (listas fijas que se cargan desde el admin o con `cargar_catalogos`)
# ---------------------------------------------------------------------------

class Rango(models.Model):
    """En el DER: tabla RANGO. Cada rango pertenece a un escalafón (oficiales,
    suboficiales o bomberos); el de menor orden es la cabeza del cuerpo."""

    class Escalafon(models.TextChoices):
        OFICIAL = "oficial", "Oficiales"
        SUBOFICIAL = "suboficial", "Suboficiales"
        BOMBERO = "bombero", "Bomberos"

    nombre_rango = models.CharField(max_length=50, unique=True)
    orden_jerarquico = models.IntegerField(help_text="Menor número = mayor jerarquía")
    escalafon = models.CharField(max_length=10, choices=Escalafon.choices, default=Escalafon.BOMBERO)

    class Meta:
        ordering = ["orden_jerarquico"]

    def __str__(self):
        return self.nombre_rango

    @property
    def es_cabeza(self):
        """El rango más alto del cuerpo (Comisario General)."""
        return not Rango.objects.filter(orden_jerarquico__lt=self.orden_jerarquico).exists()


class EstadoIntegrante(models.Model):
    """En el DER: tabla ESTADO_INTEGRANTE."""
    nombre_estado = models.CharField(max_length=30, unique=True)

    class Meta:
        verbose_name_plural = "estados de integrante"

    def __str__(self):
        return self.nombre_estado


class EstadoParte(models.Model):
    """Ciclo de vida del parte: Iniciado / En curso / Cerrado.
    En el DER: tabla ESTADO_PARTE."""
    nombre_estado = models.CharField(max_length=30, unique=True)

    class Meta:
        verbose_name_plural = "estados de parte"

    def __str__(self):
        return self.nombre_estado


def con_codigo(catalogo):
    """"02 - Accidente de tránsito": el código de la planilla adelante, si lo tiene."""
    return f"{catalogo.codigo} - {catalogo.descripcion}" if catalogo.codigo else catalogo.descripcion


class TipoSiniestro(models.Model):
    """En el DER: tabla TIPO_SINIESTRO.

    `secciones` dice qué partes de la planilla se piden al completar un parte de
    este tipo (pedido del tutor: al elegir Incendio, que aparezca solo lo de incendio).
    `parte` dice en qué planilla se ofrece el tipo: el parte de siniestro solo muestra
    los tipos de `Parte.SINIESTRO` (columna PARTE de la planilla de códigos)."""

    class Seccion(models.TextChoices):
        RODADOS = "rodados", "Datos de los vehículos siniestrados"
        DAMNIFICADOS = "damnificados", "Datos de los damnificados"
        INCENDIO = "incendio", "Incendio - Datos damnificado"

    class Parte(models.TextChoices):
        SINIESTRO = "siniestro", "Parte de siniestro"
        GENERAL = "general", "Parte general"
        MANTENIMIENTO = "mantenimiento", "Mantenimiento"
        CAPACITACION = "capacitacion", "Capacitación"
        ACTIVIDAD_COMERCIAL = "actividad_comercial", "Actividad comercial"

    codigo = models.CharField(max_length=5, blank=True, verbose_name="código",
                              help_text="Código de la planilla (ej. 02). Se muestra adelante: 02 - Accidentes.")
    descripcion = models.CharField(max_length=100, unique=True)
    parte = models.CharField(
        max_length=20, choices=Parte.choices, default=Parte.SINIESTRO,
        verbose_name="tipo de parte",
        help_text="En qué planilla se ofrece este tipo (columna PARTE de los códigos de intervención).")
    se_cobra = models.BooleanField(
        default=False, verbose_name="se cobra la participación",
        help_text="En las salidas de este tipo se marca qué bomberos cobraron su participación.")
    secciones = models.CharField(
        max_length=100, blank=True,
        help_text="Secciones de Completar que se muestran para este tipo, separadas por coma "
                  "(rodados, damnificados, incendio). Vacío: ninguna de esas tres.")

    class Meta:
        ordering = ["codigo", "descripcion"]
        verbose_name_plural = "tipos de siniestro"

    def __str__(self):
        return con_codigo(self)

    def lista_secciones(self):
        validas = set(self.Seccion.values)
        return [s.strip() for s in self.secciones.split(",") if s.strip() in validas]


class Motivo(models.Model):
    """En el DER: tabla MOTIVO. Si tiene tipos, solo se ofrece para esos tipos;
    sin tipos vale para todos.

    Los códigos "Reserva" de la planilla son lugares libres que el cuartel va a ir llenando:
    quedan cargados con activo=False para que no aparezcan en los desplegables."""
    codigo = models.CharField(max_length=5, blank=True, verbose_name="código",
                              help_text="Código de la planilla (ej. 114). Se muestra adelante: 114 - Colisión auto-moto.")
    descripcion = models.CharField(max_length=100, unique=True)
    detalle = models.CharField(
        max_length=200, blank=True,
        help_text="Texto de ayuda: ejemplos o aclaraciones que se muestran al elegir este motivo.")
    activo = models.BooleanField(
        default=True,
        help_text="Desmarcado, el motivo no se ofrece al cargar un parte (los partes viejos lo siguen mostrando).")
    tipos = models.ManyToManyField(TipoSiniestro, blank=True, related_name="motivos",
                                   verbose_name="tipos de siniestro")

    class Meta:
        ordering = ["codigo", "descripcion"]

    def __str__(self):
        return con_codigo(self)

    def vale_para(self, tipo):
        return not self.tipos.exists() or self.tipos.filter(pk=tipo.pk).exists()


class TipoVehiculo(models.Model):
    """En el DER: tabla TIPO_VEHICULO."""
    descripcion = models.CharField(max_length=60, unique=True)

    class Meta:
        verbose_name_plural = "tipos de vehículo"

    def __str__(self):
        return self.descripcion


class Combustible(models.Model):
    """En el DER: tabla COMBUSTIBLE (Gas Oil, Nafta, GNC)."""
    descripcion = models.CharField(max_length=30, unique=True)

    def __str__(self):
        return self.descripcion


class TipoEntidad(models.Model):
    """En el DER: tabla TIPO_ENTIDAD (Policía, Tránsito, Forense, ...)."""
    descripcion = models.CharField(max_length=60, unique=True)

    class Meta:
        verbose_name_plural = "tipos de entidad"

    def __str__(self):
        return self.descripcion


class CentroSalud(models.Model):
    """Agregado al DER (inspirado en "Lugares de derivación" de Shaman):
    hospitales/clínicas a donde se traslada un damnificado. Reemplaza el
    texto libre `hacia` para que las estadísticas no dependan de cómo se tipeó."""
    nombre = models.CharField(max_length=120, unique=True)
    direccion = models.CharField(max_length=150, blank=True)
    localidad = models.CharField(max_length=60, default="Villa María")
    telefono = models.CharField(max_length=30, blank=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name = "centro de salud"
        verbose_name_plural = "centros de salud"

    def __str__(self):
        return self.nombre


class Calle(models.Model):
    """Calles que agrega Estadística, además del listado del mapa (partes/datos/calles.json)."""
    nombre = models.CharField(max_length=150, unique=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Medico(models.Model):
    """Médicos que recibieron damnificados: se guardan solos para no volver a escribirlos."""
    nombre = models.CharField(max_length=80, unique=True)
    matricula = models.CharField(max_length=30, blank=True, verbose_name="matrícula")

    class Meta:
        ordering = ["nombre"]
        verbose_name = "médico"

    def __str__(self):
        return f"{self.nombre} (MP {self.matricula})" if self.matricula else self.nombre


# ---------------------------------------------------------------------------
# Personal
# ---------------------------------------------------------------------------

class Barrio(models.Model):
    """Catálogo de barrios de Villa María. Se sugiere al cargar un parte; el cuartel
    puede sumar los que falten desde el admin."""
    nombre = models.CharField(max_length=100, unique=True)
    latitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Integrante(models.Model):
    """En el DER: tabla INTEGRANTE."""

    class Sexo(models.TextChoices):
        MASCULINO = "M", "Masculino"
        FEMENINO = "F", "Femenino"
        OTRO = "X", "Otro / No informa"

    class GrupoSanguineo(models.TextChoices):
        A = "A", "A"
        B = "B", "B"
        AB = "AB", "AB"
        CERO = "0", "0"

    class FactorRh(models.TextChoices):
        POSITIVO = "+", "Positivo"
        NEGATIVO = "-", "Negativo"

    class GrupoGuardia(models.IntegerChoices):
        UNO = 1, "Grupo 1"
        DOS = 2, "Grupo 2"
        TRES = 3, "Grupo 3"

    class SiNoIndefinido(models.TextChoices):
        SI = "si", "Sí"
        NO = "no", "No"
        INDEFINIDO = "indefinido", "Indefinido"

    usuario = models.OneToOneField(
        User, on_delete=models.CASCADE, null=True, blank=True,
        related_name="integrante",
        help_text="Usuario de Django con el que este integrante inicia sesión.",
    )
    legajo = models.CharField(max_length=20, unique=True)
    apellido = models.CharField(max_length=60)
    nombre = models.CharField(max_length=60)
    dni = models.CharField(max_length=15, unique=True, validators=[validadores.dni])
    fecha_nacimiento = models.DateField(null=True, blank=True)
    domicilio = models.CharField(max_length=150, blank=True)
    localidad = models.CharField(max_length=60, blank=True)
    telefono = models.CharField(max_length=30, blank=True, validators=[validadores.telefono])
    email = models.EmailField(blank=True)
    sexo = models.CharField(max_length=1, choices=Sexo.choices, blank=True)
    altura = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True,
                                 help_text="En metros, ej. 1.75")
    peso = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True,
                               help_text="En kg")
    grupo_sanguineo = models.CharField(max_length=2, choices=GrupoSanguineo.choices, blank=True)
    factor_rh = models.CharField(max_length=1, choices=FactorRh.choices, blank=True)
    donante_sangre = models.CharField(max_length=10, choices=SiNoIndefinido.choices,
                                      default=SiNoIndefinido.INDEFINIDO)
    ficha_medica = models.FileField(upload_to="integrantes/fichas/", blank=True,
                                    help_text="Apto físico / ficha médica escaneada (PDF o imagen).")
    tiene_hijos = models.BooleanField(default=False)
    nivel_educativo = models.CharField(max_length=60, blank=True)
    titulo_obtenido = models.CharField(max_length=100, blank=True)
    formacion = models.CharField(max_length=120, blank=True,
                                 help_text="Formación bomberil (como figura en RUBA).")
    foto = models.FileField(upload_to="integrantes/", blank=True)

    rango = models.ForeignKey(Rango, on_delete=models.PROTECT)
    estado = models.ForeignKey(EstadoIntegrante, on_delete=models.PROTECT)
    fecha_ingreso = models.DateField(null=True, blank=True)
    fecha_cadete = models.DateField(null=True, blank=True)
    fecha_aspirante = models.DateField(null=True, blank=True)
    fecha_bombero = models.DateField(null=True, blank=True)
    fecha_ultimo_ascenso = models.DateField(null=True, blank=True)
    cargo = models.CharField(max_length=80, blank=True,
                             help_text="Función en el cuerpo, ej. Jefe de Cuerpo Activo, Encargado de móviles.")
    grupo_guardia = models.PositiveSmallIntegerField(
        choices=GrupoGuardia.choices, null=True, blank=True,
        help_text="Columna G de la lista de orden operativa.")
    es_bombero_forestal = models.BooleanField(default=False)
    tipo_forestal = models.CharField(max_length=60, blank=True, verbose_name="tipo de bombero forestal")
    fecha_certificacion_forestal = models.DateField(null=True, blank=True)
    es_jefe_activo = models.BooleanField(default=False)
    instructor_cuartel = models.BooleanField(default=False)
    instructor_regional = models.BooleanField(default=False)
    instructor_federacion = models.BooleanField(default=False)
    matricula_profesional = models.CharField(
        max_length=30, blank=True,
        help_text="Matrícula de enfermero/médico/paramédico, si tiene. "
                  "Se usa al registrar una negativa a la atención.",
    )

    class Meta:
        ordering = ["apellido", "nombre"]

    def __str__(self):
        return f"{self.apellido}, {self.nombre} ({self.legajo})"

    def numero_merito_en(self, anio):
        registro = self.numeros_merito.filter(anio=anio).first()
        return registro.numero_merito if registro else None


class Familiar(models.Model):
    """Grupo familiar del integrante, como en la ficha de RUBA."""
    integrante = models.ForeignKey(Integrante, on_delete=models.CASCADE, related_name="familiares")
    vinculo = models.CharField(max_length=40, help_text="Ej. Hijo/a, Cónyuge, Madre, Padre.")
    apellido = models.CharField(max_length=60, validators=[validadores.nombre])
    nombre = models.CharField(max_length=60, validators=[validadores.nombre])
    dni = models.CharField(max_length=15, blank=True, validators=[validadores.dni])
    fecha_nacimiento = models.DateField(null=True, blank=True)
    vive = models.BooleanField(default=True)
    observaciones = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["vinculo", "apellido", "nombre"]
        verbose_name_plural = "familiares"

    def __str__(self):
        return f"{self.vinculo}: {self.apellido}, {self.nombre}"


class HistorialIntegrante(models.Model):
    """Historial del integrante en RUBA: pases entre cuerpos, licencias y bajas."""
    integrante = models.ForeignKey(Integrante, on_delete=models.CASCADE, related_name="historial")
    fecha_desde = models.DateField()
    cuerpo = models.CharField(max_length=100, default="CBV Villa María")
    formacion = models.CharField(max_length=120, blank=True)
    estado = models.ForeignKey(EstadoIntegrante, on_delete=models.PROTECT, null=True, blank=True)
    motivo_baja = models.CharField(max_length=150, blank=True)
    fin_licencia = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-fecha_desde"]
        verbose_name = "registro de historial"
        verbose_name_plural = "historial"

    def __str__(self):
        return f"{self.integrante} desde {self.fecha_desde:%d/%m/%Y}"


class NumeroMerito(models.Model):
    """En el DER: tabla NUMERO_MERITO. El número (001 a 999, lo asigna Estadística) cambia cada año
    según el escalafón, por eso se guarda histórico y no en Integrante."""
    integrante = models.ForeignKey(Integrante, on_delete=models.PROTECT,
                                   related_name="numeros_merito")
    anio = models.IntegerField()
    numero_merito = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(999)])
    rango = models.ForeignKey(Rango, on_delete=models.PROTECT,
                              help_text="Rango vigente ese año.")

    class Meta:
        ordering = ["-anio", "numero_merito"]
        verbose_name = "número de mérito"
        verbose_name_plural = "números de mérito"
        constraints = [
            models.UniqueConstraint(fields=["integrante", "anio"],
                                    name="un_numero_por_integrante_y_anio"),
            models.UniqueConstraint(fields=["anio", "numero_merito"],
                                    name="numero_merito_unico_por_anio"),
        ]

    def __str__(self):
        return f"{self.numero_merito:03d}/{self.anio} — {self.integrante}"


class PersonalE100(models.Model):
    """En el DER: tabla PERSONAL_E100. Quienes atienden el 100: los pone la municipalidad y cambian
    durante el año, así que no son integrantes del cuerpo (pedido del tutor, 05/11)."""
    apellido_nombre = models.CharField(max_length=120, default="", verbose_name="apellido y nombre")
    dni = models.CharField(max_length=15, blank=True, validators=[validadores.dni], verbose_name="DNI")
    telefono = models.CharField(max_length=30, blank=True, validators=[validadores.telefono], verbose_name="teléfono")
    fecha_asignacion = models.DateField(default=timezone.localdate, verbose_name="desde")
    hasta = models.DateField(null=True, blank=True)
    activo = models.BooleanField(default=True, help_text="Solo los activos se eligen al cargar un parte.")
    integrante = models.OneToOneField(Integrante, on_delete=models.PROTECT, null=True, blank=True,
                                      related_name="personal_e100",
                                      help_text="Solo si además es integrante del cuerpo (los de antes del 05/11).")

    class Meta:
        ordering = ["-activo", "apellido_nombre"]
        verbose_name = "personal E-100"
        verbose_name_plural = "personal E-100"

    def __str__(self):
        return self.apellido_nombre


# ---------------------------------------------------------------------------
# Vehículos del cuerpo
# ---------------------------------------------------------------------------

class Vehiculo(models.Model):
    """En el DER: tabla VEHICULO (móviles propios del cuartel)."""
    legajo = models.CharField(max_length=20, unique=True,
                              help_text="Número de móvil / legajo interno.")
    tipo_vehiculo = models.ForeignKey(TipoVehiculo, on_delete=models.PROTECT)
    marca = models.CharField(max_length=60)
    modelo = models.CharField(max_length=60, blank=True)
    patente = models.CharField(max_length=10, blank=True)
    anio = models.IntegerField(null=True, blank=True)
    fecha_adquisicion = models.DateField(null=True, blank=True)
    combustible = models.ForeignKey(Combustible, on_delete=models.PROTECT)
    kilometraje_actual = models.PositiveIntegerField(default=0)
    fecha_vto_verificacion = models.DateField(null=True, blank=True)
    es_propiedad_cuerpo = models.BooleanField(default=True)
    en_servicio = models.BooleanField(default=True)
    dado_de_baja = models.BooleanField(default=False)

    class Meta:
        ordering = ["legajo"]
        verbose_name = "vehículo"

    def __str__(self):
        return f"Móvil {self.legajo} — {self.marca} {self.modelo}".strip()

    @property
    def tiene_historial(self):
        """Con partes, capacitaciones o mantenimientos no se borra: se da de baja."""
        return (self.partes.exists() or self.capacitaciones.exists() or self.mantenimientos.exists())

    def km_anterior(self, fecha, excluir=None, con_actual=True):
        """El mayor km anotado para este móvil hasta `fecha` en partes, capacitaciones y
        mantenimientos (sin contar `excluir`, el registro que se está cargando). Con
        `con_actual` también cuenta el kilometraje actual del móvil."""
        valores = [self.kilometraje_actual] if con_actual else []
        for consulta, campo in ((self.partes, "km_regreso"), (self.capacitaciones, "km_regreso"),
                                (self.mantenimientos, "kilometraje")):
            consulta = consulta.filter(**{f"{campo}__isnull": False})
            if fecha:
                consulta = consulta.filter(fecha__lte=fecha)
            if excluir is not None and isinstance(excluir, consulta.model) and excluir.pk:
                consulta = consulta.exclude(pk=excluir.pk)
            valores.append(consulta.aggregate(m=models.Max(campo))["m"] or 0)
        return max(valores)


# ---------------------------------------------------------------------------
# Parte de siniestro (núcleo)
# ---------------------------------------------------------------------------

def horas_entre(inicio, fin):
    """Duración en horas de un horario de un día (contempla que termine pasada la medianoche)."""
    if not (inicio and fin):
        return None
    minutos = (fin.hour * 60 + fin.minute) - (inicio.hour * 60 + inicio.minute)
    return round((minutos % (24 * 60)) / 60, 1)


class CorrelativoAnual:
    """Número correlativo por año (N° 12/2026) que asigna el sistema al crear: Max+1 del año
    de `fecha` (no de hoy). Si dos personas guardan a la vez y chocan con el unique de
    (numero_correlativo, anio), se reintenta con el número siguiente."""

    def save(self, *args, **kwargs):
        if self.pk is not None:
            return super().save(*args, **kwargs)
        self.anio = self.fecha.year
        Periodo.del_anio(self.anio)  # lo primero que se carga en un año abre su tomo
        for _ in range(5):
            ultimo = (type(self).objects.filter(anio=self.anio)
                      .aggregate(m=models.Max("numero_correlativo"))["m"]) or 0
            self.numero_correlativo = ultimo + 1
            try:
                with transaction.atomic():
                    return super().save(*args, **kwargs)
            except IntegrityError:
                continue
        raise IntegrityError("No se pudo asignar un número correlativo libre.")


class ParteSiniestro(CorrelativoAnual, models.Model):
    """En el DER: tabla PARTE_SINIESTRO. Se crea online en el cuartel
    (datos de la llamada) y se completa/cierra después."""

    class NivelCombustible(models.TextChoices):
        RESERVA = "reserva", "Reserva"
        CUARTO = "1/4", "1/4"
        MEDIO = "1/2", "1/2"
        TRES_CUARTOS = "3/4", "3/4"
        LLENO = "lleno", "Lleno"

    class MedioLlamado(models.TextChoices):
        # El aviso no siempre es por teléfono: a veces llega por radio o alguien se acerca.
        TELEFONICO = "telefonico", "Telefónico"
        RADIO = "radio", "Radio"
        EN_PERSONA = "en_persona", "En persona"

    # --- Llamada (la carga el guardia en el cuartel) ---
    numero_correlativo = models.IntegerField(editable=False)
    anio = models.IntegerField(editable=False)
    fecha = models.DateField()
    toque_sirena = models.BooleanField(default=False)
    medio_llamado = models.CharField(max_length=12, choices=MedioLlamado.choices,
                                     default=MedioLlamado.RADIO, verbose_name="llamado")
    telefono_origen = models.CharField(max_length=30, blank=True, validators=[validadores.telefono])
    # Pedido del tutor: por si hay que volver a llamar o ubicar a quien avisó.
    nombre_llamante = models.CharField(max_length=120, blank=True, verbose_name="quién llama",
                                       help_text="Nombre y apellido o identificación (ej. Policía, móvil 12).")
    hora_llamada = models.TimeField(null=True, blank=True)

    guardia = models.ForeignKey(
        Integrante, on_delete=models.PROTECT,
        related_name="partes_como_guardia",
        help_text="Se completa solo con el usuario logueado, no lo tipea nadie.",
    )
    autorizo = models.ForeignKey(
        Integrante, on_delete=models.PROTECT, null=True, blank=True,
        related_name="partes_autorizados", verbose_name="autorizó",
    )
    personal_e100 = models.ForeignKey(
        PersonalE100, on_delete=models.PROTECT, null=True, blank=True,
        related_name="partes", verbose_name="recibió la llamada (E-100)",
    )
    tipo = models.ForeignKey(TipoSiniestro, on_delete=models.PROTECT)
    motivo = models.ForeignKey(Motivo, on_delete=models.PROTECT)
    estado_parte = models.ForeignKey(EstadoParte, on_delete=models.PROTECT)

    ubicacion_calle = models.CharField(max_length=150)
    ubicacion_numero = models.CharField(max_length=10, blank=True)
    # Muchos accidentes son en una esquina: calle + la otra calle, sin altura.
    ubicacion_esquina = models.CharField(max_length=150, blank=True, verbose_name="esquina con")
    referencias = models.CharField(max_length=200, blank=True)
    barrio = models.CharField(max_length=100, blank=True)
    latitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    # --- Salida de la unidad (puede cancelarse a último momento) ---
    # unidad_salio no se tilda a mano: queda en Sí cuando el guardia marca la hora de salida.
    unidad_salio = models.BooleanField(default=False, verbose_name="la unidad salió")
    salida_cancelada = models.BooleanField(
        default=False, verbose_name="la salida se canceló",
        help_text="La unidad no llegó a salir (aviso cancelado a último momento).")
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="partes", verbose_name="unidad utilizada")
    hora_salida = models.TimeField(null=True, blank=True)
    hora_llegada = models.TimeField(null=True, blank=True)
    hora_regreso = models.TimeField(null=True, blank=True)
    km_regreso = models.PositiveIntegerField(null=True, blank=True)
    se_solicito_apoyo = models.BooleanField(default=False, verbose_name="se solicitó apoyo")
    unidad_apoyo_numero = models.CharField(max_length=30, blank=True)
    combustible_nivel = models.CharField(max_length=10, choices=NivelCombustible.choices,
                                         blank=True)
    carga_combustible = models.BooleanField(default=False)
    litros = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    pesos = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # --- Cierre ---
    cerro = models.ForeignKey(
        Integrante, on_delete=models.PROTECT, null=True, blank=True,
        related_name="partes_cerrados", verbose_name="cerró",
    )
    HORAS_PARA_CERRAR = 24  # pasadas estas horas sin cerrar, se avisa
    fecha_cierre = models.DateTimeField(null=True, blank=True)
    observaciones = models.TextField(blank=True)
    descripcion_confidencial = models.TextField(
        blank=True, help_text="Visible solo para roles autorizados.")

    class Meta:
        unique_together = [("numero_correlativo", "anio")]
        ordering = ["-anio", "-numero_correlativo"]
        verbose_name = "parte de siniestro"
        verbose_name_plural = "partes de siniestro"
        permissions = [
            ("cerrar_partesiniestro", "Puede cerrar partes de siniestro"),
            ("ver_confidencial", "Puede ver y editar la descripción confidencial"),
            ("corregir_cerrado", "Puede corregir un parte cerrado (con motivo; queda registrado)"),
            ("emitir_constancia", "Puede emitir constancias de intervención"),
        ]

    def __str__(self):
        return f"Parte {self.numero_correlativo}/{self.anio} — {self.tipo}"

    # --- Reglas de negocio ---------------------------------------------------

    CAMPOS_SALIDA = ["hora_salida", "hora_llegada", "hora_regreso", "km_regreso"]

    @property
    def esta_cerrado(self):
        return self.estado_parte.nombre_estado == "Cerrado"

    @property
    def ubicacion(self):
        """"Belgrano 450" o, si es una esquina, "General Paz y Buenos Aires"."""
        if self.ubicacion_esquina:
            return f"{self.ubicacion_calle} y {self.ubicacion_esquina}"
        return f"{self.ubicacion_calle} {self.ubicacion_numero}".strip()

    @property
    def salida_resuelta(self):
        """Ya se marcó la salida de la unidad, o se anotó que se canceló."""
        return self.salida_cancelada or self.hora_salida is not None

    @property
    def linea_de_tiempo(self):
        """Barra de avance del detalle: cada momento con su fecha y hora. La fecha avanza un día si
        una hora es menor que la anterior (salida que cruza la medianoche)."""
        pasos = [{"nombre": "Llamada", "hecho": True, "fecha": self.fecha, "hora": self.hora_llamada}]
        dia, anterior = self.fecha, self.hora_llamada
        for nombre, hora in (("Salida", self.hora_salida), ("Llegada", self.hora_llegada),
                             ("Retorno", self.hora_regreso)):
            if hora and anterior and hora < anterior:
                dia += timedelta(days=1)
            pasos.append({"nombre": nombre, "hecho": hora is not None, "fecha": dia if hora else None, "hora": hora})
            anterior = hora or anterior
        if self.salida_cancelada:
            pasos[1].update(hecho=True, nota="Cancelada")
        cierre = timezone.localtime(self.fecha_cierre) if self.fecha_cierre else None
        pasos.append({"nombre": "Cerrado", "hecho": self.esta_cerrado, "fecha": cierre and cierre.date(),
                      "hora": cierre and cierre.time()})
        return pasos

    @property
    def demorado(self):
        """Sigue sin cerrar pasado el plazo: no debería pasar y hay que atenderlo enseguida (pedido del tutor)."""
        if self.esta_cerrado:
            return False
        inicio = timezone.make_aware(datetime.combine(self.fecha, self.hora_llamada or time.min))
        return timezone.now() - inicio > timedelta(hours=self.HORAS_PARA_CERRAR)

    @classmethod
    def demorados(cls):
        abiertos = cls.objects.exclude(estado_parte__nombre_estado="Cerrado").select_related("estado_parte")
        return [p for p in abiertos if p.demorado]

    def km_minimo(self):
        """El km de regreso tiene que superar al último anotado para el móvil antes de este parte."""
        if not self.vehiculo_id:
            return None
        return self.vehiculo.km_anterior(self.fecha, excluir=self)

    def clean(self):
        super().clean()
        if self.medio_llamado != self.MedioLlamado.TELEFONICO:
            self.telefono_origen = ""
        if self.tipo_id and self.motivo_id and not self.motivo.vale_para(self.tipo):
            # Un parte cargado antes de la regla conserva su combinación hasta que alguien cambie
            # el tipo o el motivo (si no, no se podría guardar nada más de ese parte).
            antes = (ParteSiniestro.objects.filter(pk=self.pk).values_list("tipo_id", "motivo_id").first()
                     if self.pk else None)
            if antes != (self.tipo_id, self.motivo_id):
                raise ValidationError({"motivo": f"Ese motivo no corresponde a {self.tipo.descripcion}."})
        if self.ubicacion_esquina:
            self.ubicacion_numero = ""
        # "La unidad salió" no se tilda a mano: sale de la hora de salida que marca el guardia.
        if self.salida_cancelada:
            # Salida cancelada a último momento: no hay horarios ni km.
            for campo in self.CAMPOS_SALIDA:
                setattr(self, campo, None)
        self.unidad_salio = not self.salida_cancelada and self.hora_salida is not None
        if (self.km_regreso is not None and self.vehiculo_id
                and not (self.pk and self.esta_cerrado)):
            minimo = self.km_minimo()
            if self.km_regreso <= minimo:
                raise ValidationError({"km_regreso": (
                    f"Debe ser mayor al último km anotado para el móvil ({minimo} km).")})

    def save(self, *args, **kwargs):
        # También vale para lo que no pasa por clean() (admin, datos de demo, marcar_hora).
        self.unidad_salio = not self.salida_cancelada and self.hora_salida is not None
        super().save(*args, **kwargs)

    def problemas_para_cerrar(self):
        problemas = []
        if self.esta_cerrado:
            problemas.append("El parte ya está cerrado.")
        if not self.salida_resuelta:
            problemas.append("Marcá la hora de salida de la unidad (si no llegó a salir, indicalo en "
                             "Corregir datos: «La salida se canceló»).")
        if self.hora_salida and not self.salida_cancelada and not self.hora_regreso:
            problemas.append("Marcá el regreso de la unidad antes de cerrar el parte.")
        if not self.personal.exists() and not self.salida_cancelada:
            problemas.append("Cargá al menos un integrante que haya concurrido.")
        # El móvil es opcional (decisión del usuario); si se indicó, hace falta el km de regreso.
        if self.unidad_salio and self.vehiculo_id:
            minimo = self.km_minimo()
            if self.km_regreso is None:
                problemas.append("Cargá el kilometraje de regreso.")
            elif self.km_regreso <= minimo:
                problemas.append(
                    f"El km de regreso debe ser mayor al último km anotado para el móvil ({minimo} km).")
        for damnificado in self.damnificados.all():
            if damnificado.herido and not damnificado.lesion.strip():
                problemas.append(f"Falta la descripción de la lesión de {damnificado.apellido_nombre or 'un damnificado'}.")
        incendio = getattr(self, "incendio", None)
        if incendio and "Otros" in incendio.tipo_bien_danado.split(", ") and not incendio.descripcion.strip():
            problemas.append("Marcaste «Otros» en lo dañado por el incendio: contá qué fue en la descripción.")
        return problemas

    # Qué se muestra de cada dato al registrar una corrección después del cierre.
    NOMBRES_EN_FIRMA = {
        "parte": "Parte", "tipo": "Tipo", "motivo": "Motivo", "ubicacion": "Ubicación", "barrio": "Barrio",
        "referencias": "Referencias", "llamado": "Llamado", "personal": "Personal que concurrió", "movil": "Móvil",
        "km_regreso": "Km de regreso", "horas": "Horas (llamada, salida, llegada, regreso)",
        "damnificados": "Damnificados", "rodados": "Vehículos siniestrados", "incendio": "Datos de incendio",
        "entidades": "Policial / tránsito / forense / juzgado", "observaciones": "Observaciones",
    }

    def datos_para_firma(self):
        """Foto de los datos del parte: se sella al cerrar y se compara al corregir después."""
        incendio = getattr(self, "incendio", None)
        return {
            "parte": f"{self.numero_correlativo}/{self.anio}", "tipo": str(self.tipo),
            "motivo": str(self.motivo), "ubicacion": self.ubicacion, "barrio": self.barrio,
            "referencias": self.referencias, "llamado": self.get_medio_llamado_display(),
            "personal": sorted(str(p.integrante) for p in self.personal.select_related("integrante")),
            "movil": str(self.vehiculo) if self.vehiculo_id else None, "km_regreso": self.km_regreso,
            "horas": [str(h) if h else None for h in (self.hora_llamada, self.hora_salida,
                                                      self.hora_llegada, self.hora_regreso)],
            "damnificados": sorted(f"{d.apellido_nombre} ({d.get_triage_display() or 'sin triage'}"
                                   f"{', ' + d.lesion if d.lesion else ''})" for d in self.damnificados.all()),
            "rodados": sorted(f"{r.rodado_numero}: {r.marca} {r.modelo} {r.dominio}".strip()
                              for r in self.vehiculos_siniestrados.all()),
            "incendio": f"{incendio.tipo_bien_danado} {incendio.descripcion}".strip() if incendio else "",
            "entidades": sorted(f"{e.tipo_entidad}: {e.numero_movil} {e.a_cargo}".strip()
                                for e in self.entidades.select_related("tipo_entidad")),
            "observaciones": self.observaciones,
        }

    def cerrar(self, integrante, usuario=None):
        """Cierra el parte, actualiza el kilometraje del móvil y, si se indica quién lo
        hizo (usuario), deja la firma inmutable del cierre.
        Lanza ValidationError con la lista de problemas si no se puede."""
        problemas = self.problemas_para_cerrar()
        if problemas:
            raise ValidationError(problemas)
        with transaction.atomic():
            if usuario is not None:
                Firma.sellar(usuario, Firma.Accion.CIERRE_PARTE, self, self.datos_para_firma())
            if self.unidad_salio and self.vehiculo_id:  # el móvil es opcional
                self.vehiculo.kilometraje_actual = self.km_regreso
                self.vehiculo.save(update_fields=["kilometraje_actual"])
            self.estado_parte, _ = EstadoParte.objects.get_or_create(nombre_estado="Cerrado")
            self.cerro = integrante
            self.fecha_cierre = timezone.now()
            self.save()
            self.descargas.update(activa=False)

    # --- Tiempos operativos (como en Shaman: despacho, desplazamiento, atención) ---

    @staticmethod
    def _minutos(desde, hasta):
        if desde is None or hasta is None:
            return None
        minutos = (hasta.hour * 60 + hasta.minute) - (desde.hour * 60 + desde.minute)
        return minutos % (24 * 60)  # si cruza la medianoche

    @property
    def minutos_despacho(self):
        """Desde la llamada hasta que sale el móvil."""
        return self._minutos(self.hora_llamada, self.hora_salida)

    @property
    def minutos_desplazamiento(self):
        return self._minutos(self.hora_salida, self.hora_llegada)

    @property
    def minutos_respuesta(self):
        """Desde la llamada hasta llegar al lugar."""
        return self._minutos(self.hora_llamada, self.hora_llegada)

    @property
    def minutos_en_servicio(self):
        """Desde que sale hasta que regresa al cuartel."""
        return self._minutos(self.hora_salida, self.hora_regreso)



# ---------------------------------------------------------------------------
# Datos que se cargan en el lugar del siniestro (pueden crearse sin señal)
# ---------------------------------------------------------------------------

class CapturaOffline(models.Model):
    """Base común de las tablas "verdes" del DER: el id se genera en el
    dispositivo (UUID, no choca entre celulares sin conexión) y se registra
    cuándo se capturó y si ya se sincronizó con el servidor."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fecha_captura_local = models.DateTimeField(default=timezone.now)
    sincronizado = models.BooleanField(default=False)

    class Meta:
        abstract = True


class ParteFoto(CapturaOffline):
    """En el DER: tabla PARTE_FOTO."""
    parte = models.ForeignKey(ParteSiniestro, on_delete=models.CASCADE, related_name="fotos")
    archivo = models.FileField(upload_to="partes/%Y/")
    fecha_carga = models.DateTimeField(auto_now_add=True,
                                       help_text="Cuándo llegó al servidor.")
    descripcion = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "foto del parte"
        verbose_name_plural = "fotos del parte"

    def __str__(self):
        return f"Foto de {self.parte}"


class VehiculoSiniestrado(CapturaOffline):
    """En el DER: tabla VEHICULO_SINIESTRADO (rodados involucrados).
    La planilla trae lugar para 4; el sistema admite hasta MAXIMO."""
    MAXIMO = 10
    parte = models.ForeignKey(ParteSiniestro, on_delete=models.CASCADE,
                              related_name="vehiculos_siniestrados")
    rodado_numero = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(MAXIMO)])
    marca = models.CharField(max_length=60, blank=True)
    modelo = models.CharField(max_length=60, blank=True)
    dominio = models.CharField(max_length=10, blank=True, validators=[validadores.patente])
    seguro = models.CharField(max_length=80, blank=True)
    observaciones = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["rodado_numero"]
        verbose_name = "vehículo siniestrado"
        verbose_name_plural = "vehículos siniestrados"
        constraints = [
            models.UniqueConstraint(fields=["parte", "rodado_numero"],
                                    name="rodado_unico_por_parte"),
        ]

    def __str__(self):
        return f"Rodado {self.rodado_numero} — {self.marca} {self.dominio}".strip()


class Damnificado(CapturaOffline):
    """En el DER: tabla DAMNIFICADO, con triage y traslado. Pertenece al parte; el rodado
    es opcional (un herido de un incendio o de un rescate no tiene rodado)."""

    class Rol(models.TextChoices):
        PROPIETARIO = "propietario", "Propietario"
        CONDUCTOR = "conductor", "Conductor"
        ACOMPANANTE = "acompanante", "Acompañante"
        PEATON = "peaton", "Peatón"

    class Triage(models.TextChoices):
        ROJO = "rojo", "Rojo — atención inmediata"
        AMARILLO = "amarillo", "Amarillo — urgente"
        VERDE = "verde", "Verde — leve"
        NEGRO = "negro", "Negro — fallecido"
        BLANCO = "blanco", "Blanco / sin talón"
        SIN_LESIONES = "sin_lesiones", "Sin lesiones"

    # Con un color de triage, el damnificado está herido (pedido del tutor): "herido" no se tilda aparte.
    SIN_HERIDAS = {"", Triage.SIN_LESIONES}
    FIRMA_MAXIMA = 300_000  # caracteres del dibujo de la firma (PNG en base64)

    parte = models.ForeignKey(ParteSiniestro, on_delete=models.CASCADE, related_name="damnificados")
    vehiculo_siniestrado = models.ForeignKey(VehiculoSiniestrado, on_delete=models.CASCADE,
                                             null=True, blank=True, related_name="damnificados",
                                             verbose_name="rodado")
    apellido_nombre = models.CharField(max_length=120, validators=[validadores.nombre])
    edad = models.PositiveSmallIntegerField(null=True, blank=True, validators=[validadores.edad])
    dni = models.CharField(max_length=15, blank=True, validators=[validadores.dni])
    domicilio = models.CharField(max_length=150, blank=True)
    domicilio_numero = models.CharField(max_length=10, blank=True, verbose_name="N°")
    localidad = models.CharField(max_length=60, blank=True)
    provincia = models.CharField(max_length=60, blank=True)
    telefono = models.CharField(max_length=30, blank=True, validators=[validadores.telefono])
    rol = models.CharField(max_length=15, choices=Rol.choices, blank=True)
    sexo = models.CharField(max_length=1, choices=Integrante.Sexo.choices, blank=True)
    lesion = models.CharField(max_length=250, blank=True, verbose_name="descripción de la lesión",
                              help_text="Hace falta para cerrar el parte si el damnificado tiene un color de triage.")
    triage = models.CharField(max_length=12, choices=Triage.choices, blank=True)
    herido = models.BooleanField(default=False, editable=False,
                                 help_text="Sale solo del triage: con un color, está herido.")
    trasladado = models.BooleanField(default=False)
    trasladado_por = models.CharField(max_length=80, blank=True,
                                      help_text="Ej: ambulancia 107, móvil propio, particular.")
    hacia = models.ForeignKey(CentroSalud, on_delete=models.PROTECT, null=True, blank=True,
                              related_name="damnificados_recibidos",
                              verbose_name="trasladado hacia")
    hacia_otro = models.CharField(max_length=120, blank=True, verbose_name="otro centro de salud",
                                  help_text="Si no está en la lista.")
    recibe_dr = models.CharField(max_length=80, blank=True, verbose_name="recibe Dr.")
    recibe_matricula = models.CharField(max_length=30, blank=True, verbose_name="matrícula de quien recibe")
    recibe_firma = models.TextField(blank=True, verbose_name="firma de quien recibe",
                                    help_text="Dibujo de la firma hecho con el dedo (imagen PNG).")

    class Meta:
        verbose_name_plural = "damnificados"

    def __str__(self):
        return f"{self.apellido_nombre} ({self.get_triage_display() or 'sin triage'})"

    @property
    def destino(self):
        return str(self.hacia) if self.hacia_id else self.hacia_otro

    def clean(self):
        super().clean()
        if self.vehiculo_siniestrado_id and self.parte_id and self.vehiculo_siniestrado.parte_id != self.parte_id:
            raise ValidationError({"vehiculo_siniestrado": "Ese rodado es de otro parte."})
        if self.recibe_firma and (not self.recibe_firma.startswith("data:image/png;base64,")
                                  or len(self.recibe_firma) > self.FIRMA_MAXIMA):
            raise ValidationError({"recibe_firma": "La firma no se pudo leer: volvé a firmar."})

    def _recordar_medico(self):
        """Guarda al médico que recibe, para sugerir su nombre y matrícula. La firma se completa en
        cada parte (decisión del tutor, 03/11: más adelante podría haber un banco de firmas por matrícula)."""
        nombre = self.recibe_dr.strip()
        if not nombre or not self.parte_id:
            return
        medico = Medico.objects.filter(nombre__iexact=nombre).first() or Medico(nombre=nombre)
        if self.recibe_matricula and medico.matricula != self.recibe_matricula:
            medico.matricula = self.recibe_matricula
            medico.save()
        elif medico.pk is None:
            medico.save()
        self.recibe_matricula = self.recibe_matricula or medico.matricula

    def save(self, *args, **kwargs):
        self.herido = self.triage not in self.SIN_HERIDAS
        # Cargado desde un rodado (admin, pantalla de damnificados): el parte es el del rodado.
        if self.vehiculo_siniestrado_id and not self.parte_id:
            self.parte_id = self.vehiculo_siniestrado.parte_id
        self._recordar_medico()
        super().save(*args, **kwargs)


class IncendioDato(CapturaOffline):
    """En el DER: tabla INCENDIO_DATO (0 o 1 por parte)."""
    parte = models.OneToOneField(ParteSiniestro, on_delete=models.CASCADE,
                                 related_name="incendio")
    damnificado_nombre = models.CharField(max_length=120, blank=True, validators=[validadores.nombre])
    edad = models.PositiveSmallIntegerField(null=True, blank=True, validators=[validadores.edad])
    dni = models.CharField(max_length=15, blank=True, validators=[validadores.dni])
    tipo_bien_danado = models.CharField(max_length=100, blank=True,
                                        verbose_name="tipo de bien dañado")
    descripcion = models.TextField(blank=True)

    class Meta:
        verbose_name = "dato de incendio"
        verbose_name_plural = "datos de incendio"

    def __str__(self):
        return f"Incendio — {self.parte}"


class EntidadIntervino(CapturaOffline):
    """En el DER: tabla ENTIDAD_INTERVINO (puede haber varias por parte)."""
    parte = models.ForeignKey(ParteSiniestro, on_delete=models.CASCADE,
                              related_name="entidades")
    tipo_entidad = models.ForeignKey(TipoEntidad, on_delete=models.PROTECT)
    numero_movil = models.CharField(max_length=30, blank=True)
    a_cargo = models.CharField(max_length=120, blank=True)

    class Meta:
        verbose_name = "entidad que intervino"
        verbose_name_plural = "entidades que intervinieron"

    def __str__(self):
        return f"{self.tipo_entidad} {self.numero_movil}".strip()


class PartePersonal(CapturaOffline):
    """En el DER: tabla PARTE_PERSONAL (quiénes concurrieron)."""

    class Participacion(models.TextChoices):
        INTERVINIENTE = "interviniente", "Interviniente"
        APRESTO = "apresto", "Apresto"
        CHOFER = "chofer", "Chofer"

    class Concurrencia(models.TextChoices):
        # 10/11: primero y por defecto "En el cuartel" (lo más común al salir).
        EN_CUARTEL = "en_cuartel", "En el cuartel"
        CONVOCADO = "convocado", "Convocado"

    parte = models.ForeignKey(ParteSiniestro, on_delete=models.CASCADE,
                              related_name="personal")
    integrante = models.ForeignKey(Integrante, on_delete=models.PROTECT,
                                   related_name="participaciones")
    tipo_participacion = models.CharField(max_length=15, choices=Participacion.choices,
                                          default=Participacion.INTERVINIENTE)
    tipo_concurrencia = models.CharField(max_length=15, choices=Concurrencia.choices,
                                         default=Concurrencia.EN_CUARTEL)
    cobro = models.BooleanField(default=False, verbose_name="cobró",
                                help_text="Si cobró esta participación (solo en los tipos que se cobran).")
    rango = models.ForeignKey(Rango, on_delete=models.PROTECT, null=True, blank=True,
                              help_text="El que tenía al cargar el parte: aunque después ascienda, acá no cambia.")

    def save(self, *args, **kwargs):
        if not self.rango_id:
            self.rango = self.integrante.rango
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = "personal del parte"
        verbose_name_plural = "personal del parte"
        constraints = [
            models.UniqueConstraint(fields=["parte", "integrante"],
                                    name="integrante_una_vez_por_parte"),
        ]

    def __str__(self):
        return f"{self.integrante} en {self.parte}"


class NegativaAtencion(CapturaOffline):
    """En el DER: tabla NEGATIVA_ATENCION. Ampliada siguiendo el formulario
    de Shaman: quién atendió (con matrícula), tipo de firmante y testigo."""

    class TipoFirmante(models.TextChoices):
        PACIENTE = "paciente", "Paciente"
        RESPONSABLE = "responsable", "Responsable"
        TESTIGO = "testigo", "Testigo"

    parte = models.ForeignKey(ParteSiniestro, on_delete=models.PROTECT, null=True, blank=True,
                              related_name="negativas")
    damnificado = models.OneToOneField(Damnificado, on_delete=models.PROTECT,
                                       null=True, blank=True, related_name="negativa")
    fecha = models.DateField(default=timezone.localdate)

    tipo_firmante = models.CharField(max_length=15, choices=TipoFirmante.choices,
                                     default=TipoFirmante.PACIENTE)
    nombre_firmante = models.CharField(max_length=120, validators=[validadores.nombre])
    dni_firmante = models.CharField(max_length=15, validators=[validadores.dni])
    domicilio_firmante = models.CharField(max_length=150, blank=True)
    edad_firmante = models.PositiveSmallIntegerField(null=True, blank=True, validators=[validadores.edad])
    relacion_paciente = models.CharField(max_length=60, blank=True,
                                         verbose_name="vínculo con el paciente")

    atendio = models.ForeignKey(Integrante, on_delete=models.PROTECT, null=True, blank=True,
                                related_name="negativas_atendidas", verbose_name="atendió")
    matricula_atendio = models.CharField(max_length=30, blank=True,
                                         verbose_name="matrícula de quien atendió")

    testigo_nombre = models.CharField(max_length=120, blank=True, validators=[validadores.nombre])
    testigo_dni = models.CharField(max_length=15, blank=True, validators=[validadores.dni])

    class Meta:
        verbose_name = "negativa a la atención"
        verbose_name_plural = "negativas a la atención"

    def __str__(self):
        return f"Negativa de {self.nombre_firmante} ({self.fecha:%d/%m/%Y})"


# ---------------------------------------------------------------------------
# Carga sin señal: qué celulares se llevaron cada parte y qué mandaron
# ---------------------------------------------------------------------------

class DescargaOffline(models.Model):
    """Un parte que alguien descargó en el celular para completarlo sin señal.
    Le sirve al guardia para saber si puede quedar algo sin enviar antes de cerrar."""
    parte = models.ForeignKey(ParteSiniestro, on_delete=models.CASCADE, related_name="descargas")
    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name="descargas_offline")
    fecha_descarga = models.DateTimeField(default=timezone.now)
    ultima_sincronizacion = models.DateTimeField(null=True, blank=True)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "descarga en celular"
        verbose_name_plural = "descargas en celulares"
        constraints = [
            models.UniqueConstraint(fields=["parte", "usuario"], name="una_descarga_por_usuario"),
        ]

    def __str__(self):
        return f"{self.parte} en el celular de {self.usuario}"


class EnvioCompletar(models.Model):
    """Cada guardado de Completar lleva un id generado en el navegador. Si el
    celular reenvía el mismo guardado (se cortó la señal a mitad), no se aplica
    dos veces. También guarda los avisos de datos que pisaron los de otro equipo."""
    id = models.UUIDField(primary_key=True)
    parte = models.ForeignKey(ParteSiniestro, on_delete=models.CASCADE, related_name="envios")
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    fecha = models.DateTimeField(auto_now_add=True)
    avisos = models.TextField(blank=True)

    class Meta:
        ordering = ["-fecha"]
        verbose_name = "guardado de Completar"
        verbose_name_plural = "guardados de Completar"

    def __str__(self):
        return f"{self.parte} — {self.usuario} {self.fecha:%d/%m %H:%M}"


# ---------------------------------------------------------------------------
# Capacitaciones (planilla "Control de Capacitación")
# ---------------------------------------------------------------------------

class Departamento(models.Model):
    """Departamentos del cuerpo (Dto. ERA, Dto. K9...), para las capacitaciones de departamento (10/11).
    Antes estaban escritos en Capacitacion.DESTINOS; se cargaron con los mismos códigos, que son los
    que guarda Capacitacion.destinos. Se administran desde el admin; uno que ya no está se desactiva."""
    codigo = models.SlugField(max_length=30, unique=True,
                              help_text="Lo que se guarda en cada capacitación: no cambiarlo una vez usado.")
    nombre = models.CharField(max_length=80)
    activo = models.BooleanField(default=True, help_text="Solo los activos se eligen en una capacitación nueva.")
    orden = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["orden", "nombre"]

    def __str__(self):
        return self.nombre


class Capacitacion(CorrelativoAnual, models.Model):
    """Una capacitación dictada o recibida, con el personal que asistió.
    Cuenta para la ficha de cada bombero (lo que usa Estadística para el mérito)."""

    class Modalidad(models.TextChoices):
        INTERNA = "interna", "Interna"
        EXTERNA = "externa", "Externa"

    class Nivel(models.TextChoices):
        CUARTEL = "cuartel", "Cuartel"
        REGIONAL = "regional", "Regional"
        FEDERATIVA = "federativa", "Federativa"
        NACIONAL = "nacional", "Nacional"

    class Sentido(models.TextChoices):
        DICTADA = "dictada", "Dictada"
        RECIBIDA = "recibida", "Recibida"

    # "Destino de la misma", en el orden y con los nombres de la planilla. Los departamentos (Dto. ...)
    # están en la tabla Departamento y se listan entre los niveles y Olimpiadas (10/11).
    DESTINOS = [
        ("aspirantes_1", "Aspirantes 1er año"), ("aspirantes_2", "Aspirantes 2do año"),
        ("aspirantes_3", "Aspirantes 3er año"),
        ("pasantias", "Pasantías de Guardia"),
        ("grupo_1", "Instrucción Grupo 1"), ("grupo_2", "Instrucción Grupo 2"), ("grupo_3", "Instrucción Grupo 3"),
        ("nivel_1", "I Nivel"), ("nivel_2", "II Nivel"), ("nivel_3", "III Nivel"),
        ("olimpiadas", "Olimpiadas"),
        ("otros", "Otros"),
    ]
    ANTES_DE_LOS_DEPARTAMENTOS = "olimpiadas"
    # Destinos que se sacaron (10/11): una capacitación vieja que los tenga los sigue mostrando así.
    DESTINOS_RETIRADOS = {"nivel_4": "IV Nivel", "primer_domingo": "1° domingo"}

    numero_correlativo = models.IntegerField(editable=False)
    anio = models.IntegerField(editable=False)
    fecha = models.DateField(default=timezone.localdate)
    fecha_fin = models.DateField(null=True, blank=True, verbose_name="hasta",
                                 help_text="Solo si duró más de un día.")
    hora_inicio = models.TimeField(null=True, blank=True, verbose_name="empezó Hs.")
    hora_fin = models.TimeField(null=True, blank=True, verbose_name="terminó Hs.")
    # La planilla no tiene "tema": es el primer renglón de Datos de la capacitación, para los listados.
    tema = models.CharField(max_length=150, blank=True, editable=False)
    obligatoria = models.BooleanField(default=False)
    modalidad = models.CharField(max_length=10, choices=Modalidad.choices, default=Modalidad.INTERNA)
    curso_especial = models.BooleanField(default=False, verbose_name="curso especial")
    nivel = models.CharField(max_length=12, choices=Nivel.choices, default=Nivel.CUARTEL)
    sentido = models.CharField(max_length=10, choices=Sentido.choices, default=Sentido.DICTADA,
                               verbose_name="dictada / recibida")
    destinos = models.CharField(max_length=300, blank=True,
                                help_text="Códigos de DESTINOS separados por coma.")
    otro_destino = models.CharField(max_length=100, blank=True, verbose_name="otros: especificá")
    lugar = models.CharField(max_length=250, blank=True, verbose_name="lugar, dirección de la capacitación")
    a_cargo_integrante = models.ForeignKey(Integrante, on_delete=models.PROTECT, null=True, blank=True,
                                           related_name="capacitaciones_a_cargo", verbose_name="a cargo de")
    a_cargo = models.CharField(max_length=120, blank=True, verbose_name="instructor de afuera del cuerpo",
                               help_text="Solo si quien estuvo a cargo no es del cuerpo.")
    ayudantes = models.ManyToManyField(Integrante, blank=True, related_name="capacitaciones_ayudante",
                                       verbose_name="ayudantes instructores")
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="capacitaciones", verbose_name="U N°")
    km_regreso = models.PositiveIntegerField(null=True, blank=True)
    guardia = models.ForeignKey(Integrante, on_delete=models.PROTECT, null=True, blank=True,
                                related_name="capacitaciones_como_guardia",
                                verbose_name="personal de guardia")
    guardia_desde = models.TimeField(null=True, blank=True, verbose_name="guardia empezó Hs.")
    guardia_hasta = models.TimeField(null=True, blank=True, verbose_name="guardia terminó Hs.")
    asistentes = models.ManyToManyField(Integrante, blank=True, through="AsistenciaCapacitacion",
                                        related_name="capacitaciones")
    datos = models.TextField(blank=True, verbose_name="datos de la capacitación")
    cargado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="capacitaciones_cargadas", editable=False)

    class Meta:
        ordering = ["-fecha", "-numero_correlativo"]
        verbose_name = "capacitación"
        verbose_name_plural = "capacitaciones"
        unique_together = [("numero_correlativo", "anio")]

    def __str__(self):
        return f"Capacitación {self.numero_correlativo}/{self.anio} — {self.tema}"

    def save(self, *args, **kwargs):
        if self.datos.strip():
            self.tema = self.datos.strip().splitlines()[0][:150]
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.fecha_fin and self.fecha and self.fecha_fin < self.fecha:
            raise ValidationError({"fecha_fin": "No puede ser anterior a la fecha de inicio."})
        if self.km_regreso is not None and not self.vehiculo_id:
            raise ValidationError({"vehiculo": "Indicá qué móvil se usó."})
        if self.fecha and Periodo.esta_cerrado(self.fecha.year):
            raise ValidationError(f"El período {self.fecha.year} está cerrado: no se cargan ni corrigen "
                                  "capacitaciones de ese año.")

    @property
    def instructor(self):
        """Quién estuvo a cargo: el integrante elegido o el instructor de afuera."""
        return str(self.a_cargo_integrante) if self.a_cargo_integrante_id else self.a_cargo

    @property
    def codigos_destinos(self):
        return [c for c in self.destinos.split(",") if c]

    def lista_destinos(self):
        """Los nombres de los destinos, en el orden de la planilla: los fijos, los departamentos antes de
        Olimpiadas y, al final, los que ya no existen (se muestran con su nombre de antes, sin error)."""
        elegidos = self.codigos_destinos
        fijos = dict(self.DESTINOS)
        departamentos = {d.codigo: d.nombre for d in Departamento.objects.filter(codigo__in=elegidos)}
        lista = []
        for codigo, nombre in self.DESTINOS:
            if codigo == self.ANTES_DE_LOS_DEPARTAMENTOS:
                lista += [departamentos[c] for c in Departamento.objects.filter(codigo__in=elegidos)
                          .values_list("codigo", flat=True)]
            if codigo in elegidos and codigo != "otros":
                lista.append(nombre)
        lista += [self.DESTINOS_RETIRADOS.get(c, c) for c in elegidos if c not in fijos and c not in departamentos]
        if self.otro_destino:
            return lista + [self.otro_destino]
        return lista + (["Otros"] if "otros" in elegidos else [])

    @property
    def horas(self):
        return horas_entre(self.hora_inicio, self.hora_fin)


class AsistenciaCapacitacion(models.Model):
    """Quién asistió a una capacitación (la grilla de números de mérito)."""
    capacitacion = models.ForeignKey(Capacitacion, on_delete=models.CASCADE, related_name="asistencias")
    integrante = models.ForeignKey(Integrante, on_delete=models.PROTECT,
                                   related_name="asistencias_capacitacion")
    cobro = models.BooleanField(default=False, verbose_name="cobró")

    class Meta:
        verbose_name = "asistencia a capacitación"
        verbose_name_plural = "asistencias a capacitaciones"
        constraints = [
            models.UniqueConstraint(fields=["capacitacion", "integrante"],
                                    name="una_asistencia_por_capacitacion"),
        ]

    def __str__(self):
        return f"{self.integrante} en {self.capacitacion}"


# ---------------------------------------------------------------------------
# Períodos anuales ("tomos")
# ---------------------------------------------------------------------------

class Periodo(models.Model):
    """Un año de trabajo del cuerpo, como un tomo: los partes, las capacitaciones y los
    números de mérito de ese año. Al cerrarlo se guarda el resumen de cada bombero
    (ResumenPeriodo) y el año queda de solo lectura; el siguiente empieza de nuevo."""

    class Estado(models.TextChoices):
        ABIERTO = "abierto", "Abierto"
        CERRADO = "cerrado", "Cerrado"

    anio = models.IntegerField(unique=True, verbose_name="año")
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.ABIERTO)
    fecha_apertura = models.DateField(default=timezone.localdate)
    abrio = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="periodos_abiertos", verbose_name="abrió")
    fecha_cierre = models.DateTimeField(null=True, blank=True)
    cerro = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="periodos_cerrados", verbose_name="cerró")

    class Meta:
        ordering = ["-anio"]
        verbose_name = "período"
        verbose_name_plural = "períodos"
        permissions = [("gestionar_periodo", "Cerrar y abrir períodos y asignar números de mérito")]

    def __str__(self):
        return f"Período {self.anio}"

    @property
    def cerrado(self):
        return self.estado == self.Estado.CERRADO

    @classmethod
    def esta_cerrado(cls, anio):
        return cls.objects.filter(anio=anio, estado=cls.Estado.CERRADO).exists()

    @classmethod
    def del_anio(cls, anio):
        """El período de ese año; si todavía nadie lo abrió, se abre solo (el cuartel
        no deja de trabajar el 1° de enero porque falte asignar los números)."""
        periodo, _ = cls.objects.get_or_create(anio=anio)
        return periodo

    def problemas_para_cerrar(self):
        problemas = []
        if self.cerrado:
            problemas.append("el período ya está cerrado.")
        abiertos = ParteSiniestro.objects.filter(anio=self.anio).exclude(estado_parte__nombre_estado="Cerrado")
        if abiertos.exists():
            numeros = ", ".join(f"{p.numero_correlativo}/{p.anio}" for p in abiertos[:10])
            cantidad = abiertos.count()
            problemas.append(f"falta{'n' if cantidad > 1 else ''} cerrar {cantidad} parte{'s' if cantidad > 1 else ''} "
                             f"({numeros}).")
        return problemas

    def cerrar(self, usuario):
        """Congela el resumen de cada bombero, deja el año de solo lectura y firma el cierre."""
        from .periodos import resumen_bomberos
        problemas = self.problemas_para_cerrar()
        if problemas:
            raise ValidationError(problemas)
        with transaction.atomic():
            self.resumenes.all().delete()
            filas = resumen_bomberos(self.anio)
            ResumenPeriodo.objects.bulk_create(ResumenPeriodo(periodo=self, **fila) for fila in filas)
            Firma.sellar(usuario, Firma.Accion.CIERRE_PERIODO, self, {
                "periodo": self.anio,
                "bomberos": [{"integrante": str(f["integrante"]), **{k: (float(v) if k.startswith("horas") else v)
                              for k, v in f.items() if k != "integrante"}} for f in filas],
            })
            self.estado = self.Estado.CERRADO
            self.fecha_cierre = timezone.now()
            self.cerro = usuario
            self.save()


class ResumenPeriodo(models.Model):
    """Foto de un bombero al cerrar el período: queda igual aunque después cambien
    su rango, su estado o su número."""
    periodo = models.ForeignKey(Periodo, on_delete=models.CASCADE, related_name="resumenes")
    integrante = models.ForeignKey(Integrante, on_delete=models.PROTECT, related_name="resumenes_periodo")
    numero_merito = models.IntegerField(null=True, blank=True)
    rango = models.CharField(max_length=50, blank=True)
    estado = models.CharField(max_length=30, blank=True)
    salidas = models.PositiveIntegerField(default=0, verbose_name="siniestros asistidos")
    guardias = models.PositiveIntegerField(default=0, verbose_name="partes como guardia")
    capacitaciones = models.PositiveIntegerField(default=0)
    horas_capacitacion = models.DecimalField(max_digits=7, decimal_places=1, default=0)
    como_ayudante = models.PositiveIntegerField(default=0, verbose_name="como ayudante instructor")
    mantenimientos = models.PositiveIntegerField(default=0)
    horas_mantenimiento = models.DecimalField(max_digits=7, decimal_places=1, default=0)

    class Meta:
        ordering = [models.F("numero_merito").asc(nulls_last=True), "integrante__apellido"]
        verbose_name = "resumen del período"
        verbose_name_plural = "resúmenes del período"
        constraints = [
            models.UniqueConstraint(fields=["periodo", "integrante"], name="un_resumen_por_integrante"),
        ]

    def __str__(self):
        return f"{self.integrante} en {self.periodo}"


# ---------------------------------------------------------------------------
# Mantenimiento de los móviles (planilla "Control de Mantenimiento")
# ---------------------------------------------------------------------------

class Mantenimiento(CorrelativoAnual, models.Model):
    """Un trabajo de mantenimiento o reparación, con el personal que trabajó: de un móvil o de cualquier
    otra cosa del cuartel (pedido del tutor, 05/11). Cuenta para la ficha de cada bombero y, si es de un
    móvil, para su historial."""

    class Tipo(models.TextChoices):
        PREVENTIVO = "preventivo", "Preventivo"
        CORRECTIVO = "correctivo", "Correctivo / reparación"

    class Lugar(models.TextChoices):
        CUARTEL = "cuartel", "En el cuartel"
        TALLER = "taller", "Taller externo"

    numero_correlativo = models.IntegerField(editable=False)
    anio = models.IntegerField(editable=False)
    fecha = models.DateField(default=timezone.localdate)
    hora_inicio = models.TimeField(null=True, blank=True, verbose_name="empezó Hs.")
    hora_fin = models.TimeField(null=True, blank=True, verbose_name="terminó Hs.")
    obligatoria = models.BooleanField(default=False)
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.PROTECT, related_name="mantenimientos",
                                 null=True, blank=True, verbose_name="vehículo en reparación")
    elemento = models.CharField(max_length=150, blank=True, verbose_name="qué se mantuvo",
                                help_text="Si no es un móvil: edificio, bomba del cuartel, equipos, herramientas...")
    tipo = models.CharField(max_length=12, choices=Tipo.choices, default=Tipo.PREVENTIVO)
    motivo = models.CharField(max_length=150, blank=True, help_text="Ej: cambio de aceite, pérdida en la bomba.")
    descripcion = models.TextField(blank=True, verbose_name="datos del mantenimiento",
                                   help_text="Qué se hizo, repuestos usados, qué quedó pendiente.")
    lugar = models.CharField(max_length=10, choices=Lugar.choices, default=Lugar.CUARTEL,
                             verbose_name="lugar de reparación")
    taller = models.CharField(max_length=120, blank=True, help_text="Nombre del taller, si fue afuera.")
    autorizo = models.ForeignKey(Integrante, on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="mantenimientos_autorizados",
                                 verbose_name="reparación autorizada por")
    a_cargo = models.ForeignKey(Integrante, on_delete=models.PROTECT, null=True, blank=True,
                                related_name="mantenimientos_a_cargo", verbose_name="a cargo de")
    participantes = models.ManyToManyField(Integrante, blank=True, through="ParticipacionMantenimiento",
                                           related_name="mantenimientos")
    kilometraje = models.PositiveIntegerField(null=True, blank=True,
                                              help_text="Km del móvil al hacer el trabajo.")
    registro_libros = models.BooleanField(default=False, verbose_name="registrado en libros")
    costo_repuestos = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                          verbose_name="costo aprox. de repuestos ($)")
    repuestos_pedidos = models.DateField(null=True, blank=True, verbose_name="repuestos pedidos el")
    horas_taller = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True,
                                       verbose_name="horas de taller")
    costo_hora = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                     verbose_name="costo real hora ($)")
    costo_bombero = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                        verbose_name="costo por bombero ($)")
    controlo = models.ForeignKey(Integrante, on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="mantenimientos_controlados", verbose_name="controló")
    guardia = models.ForeignKey(Integrante, on_delete=models.PROTECT, null=True, blank=True,
                                related_name="mantenimientos_como_guardia", verbose_name="guardia")
    guardia_desde = models.TimeField(null=True, blank=True, verbose_name="desde Hs.")
    guardia_hasta = models.TimeField(null=True, blank=True, verbose_name="hasta Hs.")
    observaciones = models.TextField(blank=True)
    cargado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="mantenimientos_cargados", editable=False)

    class Meta:
        ordering = ["-fecha", "-numero_correlativo"]
        verbose_name = "mantenimiento"
        verbose_name_plural = "mantenimientos"
        unique_together = [("numero_correlativo", "anio")]

    def __str__(self):
        return f"Mantenimiento {self.numero_correlativo}/{self.anio} — {self.que}"

    @property
    def que(self):
        """Lo que se mantuvo, para listados: el móvil o lo que se anotó."""
        return str(self.vehiculo) if self.vehiculo_id else (self.elemento or "Sin indicar")

    def clean(self):
        super().clean()
        if not self.vehiculo_id and not self.elemento.strip():
            raise ValidationError({"elemento": "Indicá qué se mantuvo, o elegí el móvil."})
        if self.fecha and Periodo.esta_cerrado(self.fecha.year):
            raise ValidationError(f"El período {self.fecha.year} está cerrado: no se cargan ni corrigen "
                                  "mantenimientos de ese año.")

    @property
    def horas(self):
        return horas_entre(self.hora_inicio, self.hora_fin)

    @property
    def costo_mano_obra(self):
        if self.horas_taller is None or self.costo_hora is None:
            return None
        return self.horas_taller * self.costo_hora

    @property
    def costo_total(self):
        partes = [c for c in (self.costo_repuestos, self.costo_mano_obra) if c is not None]
        return sum(partes) if partes else None


class ParticipacionMantenimiento(models.Model):
    """Quién trabajó en un mantenimiento (la grilla de números de mérito)."""
    mantenimiento = models.ForeignKey(Mantenimiento, on_delete=models.CASCADE, related_name="participaciones")
    integrante = models.ForeignKey(Integrante, on_delete=models.PROTECT,
                                   related_name="participaciones_mantenimiento")

    class Meta:
        verbose_name = "participación en mantenimiento"
        verbose_name_plural = "participaciones en mantenimientos"
        constraints = [
            models.UniqueConstraint(fields=["mantenimiento", "integrante"],
                                    name="una_participacion_por_mantenimiento"),
        ]

    def __str__(self):
        return f"{self.integrante} en {self.mantenimiento}"


# ---------------------------------------------------------------------------
# Firmas: el sello inmutable de las decisiones importantes
# ---------------------------------------------------------------------------

class FirmaInmutable(Exception):
    pass


class Firma(models.Model):
    """Sello de una decisión importante (cerrar un parte, dar de baja a un bombero, cerrar un
    período): quién la tomó, cuándo y sobre qué datos. Se firma confirmando la contraseña.

    No se modifica ni se borra (save y delete lo impiden). Además cada firma guarda el hash
    SHA-256 de su contenido junto con el de la firma anterior: si alguien la alterara desde
    la base de datos, `Firma.verificar_cadena()` lo detecta."""

    class Accion(models.TextChoices):
        CIERRE_PARTE = "cierre_parte", "Cierre de parte"
        BAJA_INTEGRANTE = "baja_integrante", "Baja de bombero"
        CIERRE_PERIODO = "cierre_periodo", "Cierre de período"
        CORRECCION_PARTE = "correccion_parte", "Corrección de parte cerrado"

    accion = models.CharField(max_length=20, choices=Accion.choices)
    fecha = models.DateTimeField(default=timezone.now)
    usuario = models.ForeignKey(User, on_delete=models.PROTECT, related_name="firmas")
    firmante = models.CharField(max_length=150, help_text="Nombre y legajo de quien firmó, como eran al firmar.")
    objeto_tipo = models.CharField(max_length=30)
    objeto_id = models.CharField(max_length=40)
    objeto = models.CharField(max_length=150, help_text="Qué se firmó, en texto.")
    datos = models.JSONField(default=dict, help_text="Los datos tal como estaban al firmar.")
    hash_anterior = models.CharField(max_length=64, blank=True)
    hash = models.CharField(max_length=64, unique=True)

    class Meta:
        ordering = ["-pk"]
        verbose_name = "firma"
        verbose_name_plural = "firmas"
        indexes = [models.Index(fields=["objeto_tipo", "objeto_id"])]

    def __str__(self):
        return f"{self.get_accion_display()} — {self.objeto} — {self.firmante} ({self.codigo})"

    @property
    def codigo(self):
        """Código corto para mostrar y para citar en papel."""
        return self.hash[:10].upper()

    def _contenido(self):
        return json.dumps({
            "accion": self.accion, "fecha": self.fecha.isoformat(), "usuario": self.usuario_id,
            "firmante": self.firmante, "objeto_tipo": self.objeto_tipo, "objeto_id": self.objeto_id,
            "objeto": self.objeto, "datos": self.datos, "hash_anterior": self.hash_anterior,
        }, sort_keys=True, ensure_ascii=False, default=str)

    def calcular_hash(self):
        return hashlib.sha256(self._contenido().encode("utf-8")).hexdigest()

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise FirmaInmutable("Una firma no se modifica.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise FirmaInmutable("Una firma no se borra.")

    @classmethod
    def sellar(cls, usuario, accion, objeto, datos):
        """Crea la firma encadenada a la anterior. Llamar dentro de la misma transacción
        que la decisión que firma (si la decisión falla, la firma tampoco queda)."""
        integrante = getattr(usuario, "integrante", None)
        firmante = (f"{integrante.apellido}, {integrante.nombre} (legajo {integrante.legajo})" if integrante
                    else usuario.get_full_name() or usuario.username)
        with transaction.atomic():
            anterior = cls.objects.select_for_update().order_by("-pk").first()
            firma = cls(accion=accion, usuario=usuario, firmante=firmante,
                        objeto_tipo=type(objeto).__name__, objeto_id=str(objeto.pk), objeto=str(objeto),
                        datos=json.loads(json.dumps(datos, default=str)),
                        hash_anterior=anterior.hash if anterior else "")
            firma.hash = firma.calcular_hash()
            firma.save()
        return firma

    @classmethod
    def de(cls, objeto, accion=None):
        firmas = cls.objects.filter(objeto_tipo=type(objeto).__name__, objeto_id=str(objeto.pk))
        return firmas.filter(accion=accion) if accion else firmas

    @classmethod
    def verificar_cadena(cls):
        """Recalcula todos los sellos. Devuelve la lista de firmas alteradas (vacía = todo íntegro)."""
        alteradas, anterior = [], ""
        for firma in cls.objects.order_by("pk"):
            if firma.hash_anterior != anterior or firma.calcular_hash() != firma.hash:
                alteradas.append(firma)
            anterior = firma.hash
        return alteradas


class AnotacionLibroGuardia(models.Model):
    """Una anotación del libro de guardia (pedido del tutor, 05/11): lo que el guardia escribe a mano en
    el libro, con su hora. Solo se agregan: no se editan ni se borran. Cada una guarda el hash SHA-256 de
    su contenido y el de la anterior, como las firmas, así que un cambio hecho por fuera se detecta.
    Si hay que corregir algo, el jefe agrega otra anotación que remite a la original (`corrige`)."""
    numero = models.PositiveIntegerField(editable=False, verbose_name="folio")
    anio = models.IntegerField(editable=False)
    fecha = models.DateField(default=timezone.localdate)
    hora = models.TimeField(verbose_name="Hs.")
    texto = models.TextField()
    usuario = models.ForeignKey(User, on_delete=models.PROTECT, related_name="anotaciones_libro")
    autor = models.CharField(max_length=150, editable=False, help_text="Nombre y legajo de quien escribió, como eran.")
    registrada = models.DateTimeField(default=timezone.now, editable=False, help_text="Cuándo se cargó en el sistema.")
    corrige = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="correcciones")
    motivo_correccion = models.CharField(max_length=250, blank=True)
    hash_anterior = models.CharField(max_length=64, blank=True, editable=False)
    hash = models.CharField(max_length=64, unique=True, editable=False)

    class Meta:
        ordering = ["fecha", "hora", "pk"]
        verbose_name = "anotación del libro de guardia"
        verbose_name_plural = "libro de guardia"
        constraints = [models.UniqueConstraint(fields=["anio", "numero"], name="folio_unico_por_anio")]
        permissions = [("corregir_libro", "Puede agregar correcciones al libro de guardia")]

    def __str__(self):
        return f"Libro de guardia {self.numero}/{self.anio} — {self.fecha:%d/%m/%Y} {self.hora:%H:%M}"

    def _contenido(self):
        return json.dumps({
            "numero": self.numero, "anio": self.anio, "fecha": self.fecha.isoformat(), "hora": self.hora.isoformat(),
            "texto": self.texto, "usuario": self.usuario_id, "autor": self.autor,
            "registrada": self.registrada.isoformat(), "corrige": self.corrige_id,
            "motivo_correccion": self.motivo_correccion, "hash_anterior": self.hash_anterior,
        }, sort_keys=True, ensure_ascii=False)

    def calcular_hash(self):
        return hashlib.sha256(self._contenido().encode("utf-8")).hexdigest()

    @property
    def codigo(self):
        return self.hash[:10].upper()

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise FirmaInmutable("Una anotación del libro de guardia no se modifica: se agrega una corrección.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise FirmaInmutable("Una anotación del libro de guardia no se borra.")

    @classmethod
    def anotar(cls, usuario, texto, hora, fecha=None, corrige=None, motivo=""):
        """Agrega la anotación al final del libro, encadenada a la anterior, con el folio siguiente del año."""
        integrante = getattr(usuario, "integrante", None)
        autor = (f"{integrante.apellido}, {integrante.nombre} (legajo {integrante.legajo})" if integrante
                 else usuario.get_full_name() or usuario.username)
        fecha = fecha or timezone.localdate()
        if isinstance(hora, str):
            hora = time.fromisoformat(hora)
        with transaction.atomic():
            anterior = cls.objects.select_for_update().order_by("-pk").first()
            numero = (cls.objects.filter(anio=fecha.year).aggregate(m=models.Max("numero"))["m"] or 0) + 1
            anotacion = cls(numero=numero, anio=fecha.year, fecha=fecha, hora=hora, texto=texto.strip(),
                            usuario=usuario, autor=autor, corrige=corrige, motivo_correccion=motivo.strip(),
                            registrada=timezone.now().replace(microsecond=0),
                            hash_anterior=anterior.hash if anterior else "")
            anotacion.hash = anotacion.calcular_hash()
            anotacion.save()
        return anotacion

    @classmethod
    def verificar_cadena(cls):
        """Las anotaciones alteradas desde fuera del sistema (vacía = el libro está íntegro)."""
        alteradas, anterior = [], ""
        for anotacion in cls.objects.order_by("pk"):
            if anotacion.hash_anterior != anterior or anotacion.calcular_hash() != anotacion.hash:
                alteradas.append(anotacion)
            anterior = anotacion.hash
        return alteradas


class OrdenEntrega(CorrelativoAnual, models.Model):
    """Orden de entrega / remito (funciones del tutor, 05/11): qué elementos salen o entran del cuartel, en
    calidad de qué, quién entrega, quién retira, quién autoriza y si se devolvió. Combina los dos modelos de
    papel del cuartel (la orden de la foto y la del PDF)."""

    class Movimiento(models.TextChoices):
        SALIDA = "salida", "Salida (se entrega)"
        ENTRADA = "entrada", "Entrada (se recibe)"

    class Calidad(models.TextChoices):
        PRESTAMO = "prestamo", "Préstamo"
        DONACION = "donacion", "Donación"
        OTRO = "otro", "Otro"

    numero_correlativo = models.IntegerField(editable=False)
    anio = models.IntegerField(editable=False)
    fecha = models.DateField(default=timezone.localdate)
    movimiento = models.CharField(max_length=10, choices=Movimiento.choices, default=Movimiento.SALIDA)
    calidad = models.CharField(max_length=10, choices=Calidad.choices, default=Calidad.PRESTAMO, verbose_name="en calidad de")
    calidad_otro = models.CharField(max_length=80, blank=True, verbose_name="otro: cuál")
    por_orden = models.CharField(max_length=40, blank=True, verbose_name="por orden de N°")
    origen = models.CharField(max_length=120, blank=True, help_text="De dónde salen o de dónde vienen los elementos.")
    destino = models.CharField(max_length=120, blank=True, verbose_name="área / destino")
    motivo = models.TextField(blank=True, verbose_name="motivo de entrega / destino")
    entregado_por = models.ForeignKey(Integrante, on_delete=models.PROTECT, null=True, blank=True,
                                      related_name="ordenes_entregadas")
    cargo_entrega = models.CharField(max_length=80, blank=True, verbose_name="cargo")
    retira_nombre = models.CharField(max_length=120, verbose_name="quien retira: nombre y apellido")
    retira_dni = models.CharField(max_length=15, blank=True, validators=[validadores.dni], verbose_name="DNI")
    retira_telefono = models.CharField(max_length=30, blank=True, verbose_name="teléfono")
    retira_direccion = models.CharField(max_length=150, blank=True, verbose_name="dirección")
    autoriza = models.ForeignKey(Integrante, on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="ordenes_autorizadas", verbose_name="autoriza el retiro")
    cargo_autoriza = models.CharField(max_length=80, blank=True, verbose_name="cargo")
    fecha_devolucion = models.DateField(null=True, blank=True, verbose_name="fecha de devolución")
    recibido_por = models.CharField(max_length=120, blank=True, verbose_name="devuelto: recibido por")
    estado_devolucion = models.CharField(max_length=150, blank=True, verbose_name="estado al devolver")
    novedades = models.TextField(blank=True)
    cargado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, editable=False,
                                    related_name="ordenes_cargadas")

    class Meta:
        ordering = ["-anio", "-numero_correlativo"]
        verbose_name = "orden de entrega"
        verbose_name_plural = "órdenes de entrega"
        unique_together = [("numero_correlativo", "anio")]

    def __str__(self):
        return f"Orden de entrega N° {self.numero_correlativo:08d}"

    @property
    def estado(self):
        if self.fecha_devolucion:
            return "Devuelto"
        if self.calidad == self.Calidad.PRESTAMO and self.movimiento == self.Movimiento.SALIDA:
            return "Pendiente de devolución"
        return "Recibido" if self.movimiento == self.Movimiento.ENTRADA else "Entregado"


class ElementoOrden(models.Model):
    """Un renglón de la orden: cantidad, descripción del elemento y su estado."""
    orden = models.ForeignKey(OrdenEntrega, on_delete=models.CASCADE, related_name="elementos")
    cantidad = models.PositiveIntegerField(default=1, verbose_name="cant.")
    descripcion = models.CharField(max_length=200, verbose_name="descripción del elemento")
    estado = models.CharField(max_length=150, blank=True, verbose_name="estado / observaciones")

    class Meta:
        verbose_name = "elemento"
        verbose_name_plural = "elementos"

    def __str__(self):
        return f"{self.cantidad} × {self.descripcion}"
