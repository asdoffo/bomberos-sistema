from django.contrib import admin
from django.utils.html import format_html

from .models import (
    AsistenciaCapacitacion, Barrio, Calle, Capacitacion, CentroSalud, Departamento, Firma, Mantenimiento, Medico, ParticipacionMantenimiento,
    Periodo, ResumenPeriodo, Combustible, Damnificado, EntidadIntervino, EstadoIntegrante,
    EstadoParte, Familiar, HistorialIntegrante, IncendioDato, Integrante, Motivo, NegativaAtencion, NumeroMerito,
    ParteFoto, ParteSiniestro, PartePersonal, PersonalE100, Rango, TipoEntidad,
    TipoSiniestro, TipoVehiculo, Vehiculo, VehiculoSiniestrado,
)

admin.site.site_header = "Bomberos Voluntarios de Villa María"
admin.site.site_title = "Bomberos Villa María"
admin.site.index_title = "Administración del sistema"

COLORES_TRIAGE = {
    "rojo": "#c0392b",
    "amarillo": "#f1c40f",
    "verde": "#27ae60",
    "negro": "#000000",
    "blanco": "#bdc3c7",
}


def etiqueta_triage(triage, texto):
    color = COLORES_TRIAGE.get(triage, "#bdc3c7")
    color_texto = "#000" if triage in ("amarillo", "blanco") else "#fff"
    return format_html(
        '<span style="background:{};color:{};padding:2px 8px;border-radius:10px;">{}</span>',
        color, color_texto, texto,
    )


# --- Catálogos -------------------------------------------------------------

@admin.register(Rango)
class RangoAdmin(admin.ModelAdmin):
    list_display = ["nombre_rango", "orden_jerarquico", "escalafon"]
    list_filter = ["escalafon"]


@admin.register(Barrio)
class BarrioAdmin(admin.ModelAdmin):
    list_display = ["nombre", "latitud", "longitud"]
    search_fields = ["nombre"]


@admin.register(Calle)
class CalleAdmin(admin.ModelAdmin):
    search_fields = ["nombre"]


@admin.register(Departamento)
class DepartamentoAdmin(admin.ModelAdmin):
    """Los departamentos de las capacitaciones de departamento (10/11). Uno que ya no está se desactiva."""
    list_display = ["nombre", "codigo", "activo", "orden"]
    list_editable = ["activo", "orden"]
    prepopulated_fields = {"codigo": ["nombre"]}


@admin.register(Medico)
class MedicoAdmin(admin.ModelAdmin):
    list_display = ["nombre", "matricula"]
    search_fields = ["nombre", "matricula"]


@admin.register(CentroSalud)
class CentroSaludAdmin(admin.ModelAdmin):
    list_display = ["nombre", "direccion", "localidad", "telefono"]
    search_fields = ["nombre"]


for catalogo in (EstadoIntegrante, EstadoParte, TipoVehiculo, Combustible, TipoEntidad):
    admin.site.register(catalogo)


@admin.register(TipoSiniestro)
class TipoSiniestroAdmin(admin.ModelAdmin):
    list_display = ["codigo", "descripcion", "parte", "secciones", "se_cobra"]
    list_display_links = ["descripcion"]
    list_editable = ["codigo", "parte", "se_cobra"]
    list_filter = ["parte"]


@admin.register(Motivo)
class MotivoAdmin(admin.ModelAdmin):
    list_display = ["codigo", "descripcion", "detalle", "activo", "tipos_de_siniestro"]
    list_display_links = ["descripcion"]
    list_editable = ["codigo", "detalle", "activo"]
    list_filter = ["activo", "tipos__parte", "tipos"]
    search_fields = ["codigo", "descripcion", "detalle"]
    filter_horizontal = ["tipos"]

    @admin.display(description="tipos de siniestro")
    def tipos_de_siniestro(self, motivo):
        return ", ".join(str(t) for t in motivo.tipos.all()) or "Todos"


# --- Personal --------------------------------------------------------------

class NumeroMeritoInline(admin.TabularInline):
    model = NumeroMerito
    extra = 0


class FamiliarInline(admin.TabularInline):
    model = Familiar
    extra = 0


class HistorialInline(admin.TabularInline):
    model = HistorialIntegrante
    extra = 0


@admin.register(Integrante)
class IntegranteAdmin(admin.ModelAdmin):
    list_display = ["legajo", "apellido", "nombre", "rango", "estado", "telefono", "usuario"]
    list_filter = ["estado", "rango", "es_jefe_activo"]
    search_fields = ["legajo", "apellido", "nombre", "dni"]
    autocomplete_fields = ["usuario"]
    inlines = [NumeroMeritoInline, FamiliarInline, HistorialInline]
    fieldsets = [
        ("Identificación", {"fields": ["usuario", "legajo", "apellido", "nombre", "dni", "foto"]}),
        ("Contacto", {"fields": ["fecha_nacimiento", "domicilio", "localidad", "telefono", "email"]}),
        ("Datos físicos y salud", {
            "classes": ["collapse"],
            "fields": ["sexo", "altura", "peso", "grupo_sanguineo", "factor_rh", "donante_sangre",
                       "ficha_medica"],
        }),
        ("Formación", {
            "classes": ["collapse"],
            "fields": ["formacion", "nivel_educativo", "titulo_obtenido", "matricula_profesional",
                       "tiene_hijos"],
        }),
        ("Carrera en el cuerpo", {
            "fields": ["rango", "estado", "cargo", "fecha_ingreso", "fecha_cadete", "fecha_aspirante",
                       "fecha_bombero", "fecha_ultimo_ascenso", "es_jefe_activo",
                       "instructor_cuartel", "instructor_regional", "instructor_federacion",
                       "es_bombero_forestal", "tipo_forestal", "fecha_certificacion_forestal"],
        }),
    ]


@admin.register(NumeroMerito)
class NumeroMeritoAdmin(admin.ModelAdmin):
    list_display = ["anio", "numero_merito", "integrante", "rango"]
    list_filter = ["anio"]
    search_fields = ["integrante__apellido", "integrante__legajo", "numero_merito"]


@admin.register(PersonalE100)
class PersonalE100Admin(admin.ModelAdmin):
    list_display = ["integrante", "fecha_asignacion", "activo"]
    list_filter = ["activo"]


# --- Vehículos -------------------------------------------------------------

@admin.register(Vehiculo)
class VehiculoAdmin(admin.ModelAdmin):
    list_display = ["legajo", "tipo_vehiculo", "marca", "modelo", "patente",
                    "kilometraje_actual", "fecha_vto_verificacion", "en_servicio", "dado_de_baja"]
    list_filter = ["tipo_vehiculo", "en_servicio", "dado_de_baja"]
    search_fields = ["legajo", "patente", "marca"]


# --- Parte de siniestro ----------------------------------------------------

class PartePersonalInline(admin.TabularInline):
    model = PartePersonal
    extra = 0
    autocomplete_fields = ["integrante"]
    exclude = ["fecha_captura_local", "sincronizado"]


class VehiculoSiniestradoInline(admin.TabularInline):
    model = VehiculoSiniestrado
    extra = 0
    show_change_link = True  # los damnificados se cargan dentro de cada rodado
    exclude = ["fecha_captura_local", "sincronizado"]


class EntidadIntervinoInline(admin.TabularInline):
    model = EntidadIntervino
    extra = 0
    exclude = ["fecha_captura_local", "sincronizado"]


class IncendioDatoInline(admin.StackedInline):
    model = IncendioDato
    extra = 0
    exclude = ["fecha_captura_local", "sincronizado"]


class ParteFotoInline(admin.TabularInline):
    model = ParteFoto
    extra = 0
    fields = ["archivo", "descripcion", "fecha_carga"]
    readonly_fields = ["fecha_carga"]


@admin.register(ParteSiniestro)
class ParteSiniestroAdmin(admin.ModelAdmin):
    list_display = ["numero", "fecha", "tipo", "motivo", "direccion", "estado_parte",
                    "guardia", "unidad_salio"]
    list_filter = ["estado_parte", "anio", "tipo", "unidad_salio"]
    search_fields = ["numero_correlativo", "ubicacion_calle", "barrio", "observaciones"]
    date_hierarchy = "fecha"
    autocomplete_fields = ["guardia", "autorizo", "cerro"]
    readonly_fields = ["numero_correlativo", "anio"]
    inlines = [PartePersonalInline, VehiculoSiniestradoInline, EntidadIntervinoInline,
               IncendioDatoInline, ParteFotoInline]
    fieldsets = [
        ("Llamada", {"fields": [
            ("numero_correlativo", "anio"), "fecha", ("hora_llamada", "toque_sirena"),
            "telefono_origen", "guardia", "personal_e100", "autorizo", ("tipo", "motivo"),
            "estado_parte",
        ]}),
        ("Ubicación", {"fields": [
            ("ubicacion_calle", "ubicacion_numero"), "barrio", "referencias", ("latitud", "longitud"),
        ]}),
        ("Salida de la unidad", {"fields": [
            "unidad_salio", "vehiculo", ("hora_salida", "hora_llegada", "hora_regreso"),
            "km_regreso", ("se_solicito_apoyo", "unidad_apoyo_numero"),
            ("combustible_nivel", "carga_combustible", "litros", "pesos"),
        ]}),
        ("Cierre", {"fields": [
            ("cerro", "fecha_cierre"), "observaciones", "descripcion_confidencial",
        ]}),
    ]

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if request.user.has_perm("partes.ver_confidencial"):
            return fieldsets
        return [(nombre, {**opciones, "fields": [c for c in opciones["fields"]
                                                 if c != "descripcion_confidencial"]})
                for nombre, opciones in fieldsets]

    @admin.display(description="N°", ordering="numero_correlativo")
    def numero(self, obj):
        return f"{obj.numero_correlativo}/{obj.anio}"

    @admin.display(description="Dirección")
    def direccion(self, obj):
        return f"{obj.ubicacion_calle} {obj.ubicacion_numero}".strip()


class DamnificadoInline(admin.StackedInline):
    model = Damnificado
    extra = 0
    autocomplete_fields = ["hacia"]
    exclude = ["fecha_captura_local", "sincronizado", "recibe_firma"]
    fieldsets = [
        (None, {"fields": [("apellido_nombre", "edad", "dni"), ("rol", "triage"),
                           ("domicilio", "domicilio_numero", "localidad", "provincia"), "telefono"]}),
        ("Atención y traslado", {"fields": ["trasladado", "trasladado_por",
                                            ("hacia", "hacia_otro"), ("recibe_dr", "recibe_matricula")]}),
    ]


@admin.register(VehiculoSiniestrado)
class VehiculoSiniestradoAdmin(admin.ModelAdmin):
    list_display = ["parte", "rodado_numero", "marca", "modelo", "dominio", "seguro"]
    search_fields = ["dominio", "marca"]
    inlines = [DamnificadoInline]


@admin.register(Damnificado)
class DamnificadoAdmin(admin.ModelAdmin):
    list_display = ["apellido_nombre", "edad", "rol", "triage_color", "herido", "trasladado",
                    "hacia", "parte", "vehiculo_siniestrado"]
    list_filter = ["triage", "rol", "herido", "trasladado", "hacia"]
    search_fields = ["apellido_nombre", "dni"]
    autocomplete_fields = ["hacia"]

    @admin.display(description="Triage", ordering="triage")
    def triage_color(self, obj):
        return etiqueta_triage(obj.triage, obj.get_triage_display())


@admin.register(NegativaAtencion)
class NegativaAtencionAdmin(admin.ModelAdmin):
    list_display = ["fecha", "nombre_firmante", "dni_firmante", "tipo_firmante", "atendio", "parte"]
    list_filter = ["tipo_firmante", "fecha"]
    search_fields = ["nombre_firmante", "dni_firmante"]
    autocomplete_fields = ["atendio"]
    exclude = ["fecha_captura_local", "sincronizado"]
    fieldsets = [
        (None, {"fields": ["fecha", "parte", "damnificado"]}),
        ("Atendió", {"fields": [("atendio", "matricula_atendio")]}),
        ("Paciente / Responsable / Testigo", {"fields": [
            "tipo_firmante", ("nombre_firmante", "dni_firmante"), ("edad_firmante", "relacion_paciente"),
            "domicilio_firmante",
        ]}),
        ("Testigo (opcional)", {"fields": [("testigo_nombre", "testigo_dni")]}),
    ]


class AsistenciaCapacitacionInline(admin.TabularInline):
    model = AsistenciaCapacitacion
    extra = 0
    autocomplete_fields = ["integrante"]


@admin.register(Capacitacion)
class CapacitacionAdmin(admin.ModelAdmin):
    list_display = ["numero_correlativo", "anio", "fecha", "tema", "modalidad", "sentido", "nivel"]
    list_filter = ["anio", "modalidad", "sentido", "nivel", "obligatoria"]
    search_fields = ["tema", "lugar", "a_cargo"]
    filter_horizontal = ["ayudantes"]
    inlines = [AsistenciaCapacitacionInline]


class ResumenPeriodoInline(admin.TabularInline):
    model = ResumenPeriodo
    extra = 0
    can_delete = False
    readonly_fields = [f.name for f in ResumenPeriodo._meta.fields if f.name != "id"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Periodo)
class PeriodoAdmin(admin.ModelAdmin):
    """Cerrar se hace desde la pantalla del tomo (guarda el resumen). Acá solo se puede
    reabrir un período cambiando su estado, si hiciera falta corregir algo."""
    list_display = ["anio", "estado", "fecha_apertura", "fecha_cierre", "cerro"]
    readonly_fields = ["fecha_cierre", "cerro", "abrio"]
    inlines = [ResumenPeriodoInline]


class ParticipacionMantenimientoInline(admin.TabularInline):
    model = ParticipacionMantenimiento
    extra = 0
    autocomplete_fields = ["integrante"]


@admin.register(Mantenimiento)
class MantenimientoAdmin(admin.ModelAdmin):
    list_display = ["numero_correlativo", "anio", "fecha", "vehiculo", "tipo", "motivo", "lugar"]
    list_filter = ["anio", "tipo", "lugar", "vehiculo"]
    search_fields = ["motivo", "descripcion", "taller"]
    inlines = [ParticipacionMantenimientoInline]


@admin.register(Firma)
class FirmaAdmin(admin.ModelAdmin):
    """Las firmas solo se consultan: nadie las agrega, cambia ni borra desde el admin."""
    list_display = ["pk", "fecha", "accion", "objeto", "firmante", "codigo", "integra"]
    list_filter = ["accion"]
    search_fields = ["objeto", "firmante", "hash"]
    readonly_fields = [f.name for f in Firma._meta.fields]

    @admin.display(boolean=True, description="íntegra")
    def integra(self, firma):
        return firma.calcular_hash() == firma.hash

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
