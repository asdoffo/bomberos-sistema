import re
from datetime import timedelta

from django import forms
from django.db.models import F, OuterRef, Q, Subquery
from django.utils import timezone

from . import validadores
from .geocodificacion import calles, nombre_oficial
from .moviles import opciones_agrupadas
from .models import (
    AsistenciaCapacitacion, Calle, Capacitacion, CentroSalud, Damnificado, Departamento, EstadoIntegrante, Mantenimiento,
    ParticipacionMantenimiento, EntidadIntervino, Familiar, HistorialIntegrante, IncendioDato, Integrante,
    NegativaAtencion, NumeroMerito,
    Motivo, OrdenEntrega, ElementoOrden, ParteSiniestro, PartePersonal, PersonalE100, Rango, TipoEntidad,
    TipoSiniestro, Vehiculo, VehiculoSiniestrado,
)

# Reconoce "Belgrano 1450", "San Martín 250", etc:
# todo lo que va antes del último grupo de dígitos es la calle,
# el grupo de dígitos final es el número.
REGEX_DIRECCION = re.compile(r"^(?P<calle>.+?)\s+(?P<numero>\d{1,6})\s*$")
# Una esquina: "General Paz y Buenos Aires", "Mendoza esq. Salta", "Lavalle esquina Chile".
REGEX_ESQUINA = re.compile(r"^(?P<calle>.+?)\s+(?:y|e|esq\.?|esquina)\s+(?P<esquina>.+?)\s*$", re.IGNORECASE)


def separar_ubicacion(texto):
    """(calle, número, esquina) de lo que escribió el guardia, o None si no se entiende.
    Con altura al final es una dirección; si no, puede ser una esquina."""
    texto = texto.strip()
    match = REGEX_DIRECCION.match(texto)
    if match:
        return match.group("calle").strip(), match.group("numero"), ""
    match = REGEX_ESQUINA.match(texto)
    if match:
        return match.group("calle").strip(), "", match.group("esquina").strip()
    return None

HORA = forms.TimeInput(attrs={"type": "time"}, format="%H:%M")
OPCION_VACIA = "Elegí una opción"  # primera opción de las listas desplegables


class ControlDeDatos:
    """DNI, edad, teléfono, patente y nombres: teclado adecuado en el celular y datos
    limpios antes de que el modelo los valide (ver validadores.py)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nombre, campo in self.fields.items():
            # Django 6 trae la opción vacía de las listas en inglés ("- Select an option -").
            if isinstance(campo, forms.ModelChoiceField) and campo.empty_label is not None:
                campo.empty_label = OPCION_VACIA
            elif isinstance(campo, forms.TypedChoiceField) and campo.choices and campo.choices[0][0] == "":
                campo.choices = [("", OPCION_VACIA)] + list(campo.choices)[1:]
            if nombre in validadores.CAMPOS_DNI:
                campo.label = re.sub(r"\bdni\b", "DNI", str(campo.label), flags=re.IGNORECASE)
            atributos = validadores.atributos_html(nombre)
            if "type" in atributos:
                campo.widget.input_type = atributos.pop("type")
            campo.widget.attrs.update(atributos)

    def clean(self):
        datos = super().clean()
        for nombre, valor in datos.items():
            datos[nombre] = validadores.limpiar(nombre, valor)
        return datos


OPCIONES_RODADO = [("", OPCION_VACIA)] + [(i, f"{i}°") for i in range(1, VehiculoSiniestrado.MAXIMO + 1)]


class TelefonoConCaracteristica:
    """"TE de donde llaman" en dos casilleros, solo números: característica (353) y número.
    Se guarda junto en telefono_origen como "353 4123456"."""

    telefono_obligatorio = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # El teléfono solo se pide si el llamado es telefónico: se controla en clean().
        self.fields.pop("telefono_origen")
        if "medio_llamado" in self.fields:
            # Una pantalla vieja guardada en un celular no lo manda: queda el que tenía el parte.
            self.fields["medio_llamado"].required = False
        caracteristica, numero = validadores.separar_telefono(self.instance.telefono_origen)
        solo_numeros = {"inputmode": "numeric", "autocomplete": "off"}
        self.fields["caracteristica"] = forms.CharField(
            label="Característica", initial=caracteristica, required=False, max_length=4,
            help_text="Sin el 0", widget=forms.TextInput(attrs={
                **solo_numeros, "pattern": "[0-9]{2,4}", "title": "Entre 2 y 4 números", "size": "4"}))
        self.fields["telefono_numero"] = forms.CharField(
            label="TE de donde llaman", initial=numero, required=False, max_length=8,
            help_text="Sin el 15", widget=forms.TextInput(attrs={
                **solo_numeros, "pattern": "[0-9]{6,8}", "title": "Entre 6 y 8 números"}))

    def clean(self):
        datos = super().clean()
        if "telefono_numero" not in self.fields:
            return datos  # quien completa no ve el encabezado: el teléfono no se toca
        medio = datos.get("medio_llamado") or self.instance.medio_llamado
        datos["medio_llamado"] = medio
        if medio != ParteSiniestro.MedioLlamado.TELEFONICO:
            datos["telefono_origen"] = ""
            return datos
        caracteristica = (datos.get("caracteristica") or "").strip()
        numero = (datos.get("telefono_numero") or "").strip()
        if not numero:
            datos["telefono_origen"] = ""
            if self.telefono_obligatorio:
                self.add_error("telefono_numero", "Si el llamado es telefónico, anotá el número.")
            return datos
        if not caracteristica.isdigit() or not numero.isdigit():
            self.add_error("telefono_numero", "Solo números, sin espacios ni guiones.")
        elif len(caracteristica) + len(numero) != validadores.DIGITOS_TELEFONO:
            self.add_error("telefono_numero", "Característica y número tienen que sumar 10 números "
                                              "(por ejemplo 353 y 4123456).")
        else:
            datos["telefono_origen"] = f"{caracteristica} {numero}"
        return datos


class ParteSiniestroForm(TelefonoConCaracteristica, ControlDeDatos, forms.ModelForm):
    """Alta del parte: lo que carga el guardia al recibir la llamada.

    Fecha y hora de la llamada no se tipean: el navegador manda la hora de la PC
    en el momento en que se abrió la pantalla (cuando entra la llamada)."""
    es_esquina = forms.BooleanField(label="Es una esquina", required=False)
    direccion_input = forms.CharField(
        label="Ubicación calle y N°",
        help_text="Escribí calle y altura juntas, o una esquina: General Paz y Buenos Aires.",
        widget=forms.TextInput(attrs={"placeholder": "Ej: Belgrano 450", "autofocus": True,
                                      "autocomplete": "off"}),
    )
    esquina_input = forms.CharField(
        label="Esquina con", required=False, max_length=150,
        widget=forms.TextInput(attrs={"placeholder": "Ej: Buenos Aires", "list": "calles-dl",
                                      "autocomplete": "off"}),
    )
    momento_llamada = forms.DateTimeField(
        required=False, widget=forms.HiddenInput,
        input_formats=["%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"],
    )
    # Si el reloj de la PC difiere más que esto del servidor, se usa la hora del servidor.
    # Acota cuánto se puede "atrasar" una llamada manipulando el campo oculto. Después
    # del alta la hora de llamada ya no se modifica (solo un administrador, desde el admin).
    TOLERANCIA_RELOJ = timedelta(hours=2)

    class Meta:
        model = ParteSiniestro
        fields = ["medio_llamado", "telefono_origen", "nombre_llamante", "toque_sirena", "tipo", "motivo",
                  "barrio", "referencias", "latitud", "longitud", "observaciones"]
        labels = {"telefono_origen": "TE de donde llaman", "medio_llamado": "Llamado",
                  "nombre_llamante": "Quién llama"}
        widgets = {
            "medio_llamado": forms.RadioSelect,
            "barrio": forms.TextInput(attrs={"list": "barrios-villa-maria", "autocomplete": "off"}),
            "latitud": forms.HiddenInput(), "longitud": forms.HiddenInput(),
            "observaciones": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        preparar_tipo_y_motivo(self, self.instance)
        # Sin estos datos no se crea el parte.
        self.fields["barrio"].required = True
        self.telefono_obligatorio = True

    def clean_momento_llamada(self):
        ahora = timezone.localtime()
        momento = self.cleaned_data.get("momento_llamada")
        if momento is None or abs(momento - ahora) > self.TOLERANCIA_RELOJ:
            return ahora
        return timezone.localtime(momento)

    def clean(self):
        datos = super().clean()
        texto = (datos.get("direccion_input") or "").strip()
        if not texto:
            return datos
        esquina = (datos.get("esquina_input") or "").strip()
        if datos.get("es_esquina") or esquina:
            # Opción "Es una esquina": las dos calles en dos casilleros (o juntas en el primero).
            separada = separar_ubicacion(texto)
            if not esquina and separada and separada[2]:
                texto, esquina = separada[0], separada[2]
            if not esquina:
                self.add_error("esquina_input", "Escribí la otra calle de la esquina.")
                return datos
            self._ubicacion = (nombre_oficial(texto), "", nombre_oficial(esquina))
            return datos
        separada = separar_ubicacion(texto)
        if not separada:
            self.add_error("direccion_input", "Escribí la calle y la altura (Belgrano 450) "
                                              "o una esquina (General Paz y Buenos Aires).")
            return datos
        calle, numero, esquina = separada
        self._ubicacion = (nombre_oficial(calle), numero, nombre_oficial(esquina) if esquina else "")
        return datos

    def _post_clean(self):
        # La calle tiene que estar en la instancia antes de que el modelo se valide.
        if hasattr(self, "_ubicacion"):
            (self.instance.ubicacion_calle, self.instance.ubicacion_numero,
             self.instance.ubicacion_esquina) = self._ubicacion
        momento = self.cleaned_data.get("momento_llamada")
        if momento:
            self.instance.fecha = momento.date()
            self.instance.hora_llamada = momento.time().replace(second=0, microsecond=0)
        super()._post_clean()


class CompletarParteForm(TelefonoConCaracteristica, ControlDeDatos, forms.ModelForm):
    """Segunda etapa: lo que se completa en el lugar o al volver al cuartel.
    Los campos siguen el orden y los nombres de la planilla "Control de salida".
    La fecha y la hora de llamada quedan fijas desde el alta: acá no se cambian."""

    class Meta:
        model = ParteSiniestro
        fields = [
            "toque_sirena", "medio_llamado", "telefono_origen", "nombre_llamante", "autorizo",
            "salida_cancelada", "hora_salida", "hora_llegada", "hora_regreso",
            "tipo", "motivo", "ubicacion_calle", "ubicacion_numero", "ubicacion_esquina", "referencias", "barrio",
            "se_solicito_apoyo", "unidad_apoyo_numero",
            "vehiculo", "km_regreso", "combustible_nivel", "carga_combustible", "litros", "pesos",
            "personal_e100",
            "latitud", "longitud", "observaciones", "descripcion_confidencial",
        ]
        labels = {
            "toque_sirena": "Toque de sirena", "telefono_origen": "TE de donde llaman",
            "medio_llamado": "Llamado", "nombre_llamante": "Quién llama",
            "autorizo": "Autorizó", "salida_cancelada": "La salida se canceló",
            "hora_salida": "Salida Hs.", "hora_llegada": "Llegada Hs.", "hora_regreso": "Regreso Hs.",
            "ubicacion_calle": "Ubicación calle", "ubicacion_numero": "N°", "ubicacion_esquina": "Esquina con",
            "se_solicito_apoyo": "Se solicitó apoyo", "unidad_apoyo_numero": "Unidad en apoyo a U N°",
            "vehiculo": "U N°", "km_regreso": "Km regreso", "combustible_nivel": "Combustible nivel",
            "carga_combustible": "Carga", "personal_e100": "Personal E-100",
        }
        widgets = {
            "medio_llamado": forms.RadioSelect,
            "hora_salida": HORA, "hora_llegada": HORA, "hora_regreso": HORA,
            "ubicacion_calle": forms.TextInput(attrs={"list": "calles-villa-maria", "autocomplete": "off"}),
            "ubicacion_esquina": forms.TextInput(attrs={"list": "calles-villa-maria", "autocomplete": "off"}),
            "barrio": forms.TextInput(attrs={"list": "barrios-villa-maria", "autocomplete": "off"}),
            "latitud": forms.HiddenInput(), "longitud": forms.HiddenInput(),
            "observaciones": forms.Textarea(attrs={"rows": 3}),
            "descripcion_confidencial": forms.Textarea(attrs={"rows": 3}),
        }

    # Encabezado de la planilla: lo carga el guardia desde el cuartel, no el bombero en el lugar.
    # También la unidad y el combustible (pedido del tutor).
    CAMPOS_GUARDIA = ["toque_sirena", "medio_llamado", "caracteristica", "telefono_numero", "nombre_llamante",
                      "autorizo", "salida_cancelada", "hora_salida", "hora_llegada", "hora_regreso",
                      "vehiculo", "km_regreso", "combustible_nivel", "carga_combustible", "litros", "pesos"]

    # Lo que anotó el guardia al crear el parte y las horas (se marcan con los botones del detalle):
    # en Completar se ven pero no se tocan; el guardia los cambia desde "Corregir datos".
    CAMPOS_SOLO_CORREGIR = ["toque_sirena", "medio_llamado", "caracteristica", "telefono_numero",
                            "nombre_llamante", "salida_cancelada", "hora_salida", "hora_llegada", "hora_regreso"]

    def __init__(self, *args, puede_confidencial=False, es_guardia=True, corregir=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not puede_confidencial:
            del self.fields["descripcion_confidencial"]
        quitar = (self.CAMPOS_GUARDIA if not es_guardia else []) + (
            self.CAMPOS_SOLO_CORREGIR if not (es_guardia and corregir) else [])
        for campo in quitar:
            self.fields.pop(campo, None)
        preparar_salida(self, self.instance)
        preparar_tipo_y_motivo(self, self.instance)
        if "personal_e100" in self.fields:
            # Los activos de la tabla Personal E-100, más el que ya tenía el parte aunque hoy no esté.
            activos = Q(activo=True)
            if self.instance.personal_e100_id:
                activos |= Q(pk=self.instance.personal_e100_id)
            self.fields["personal_e100"].queryset = PersonalE100.objects.filter(activos)
            self.fields["personal_e100"].empty_label = OPCION_VACIA

    def clean_ubicacion_calle(self):
        return nombre_oficial(self.cleaned_data["ubicacion_calle"])

    def clean_ubicacion_esquina(self):
        esquina = self.cleaned_data["ubicacion_esquina"].strip()
        return nombre_oficial(esquina) if esquina else ""


def graduados(incluir=()):
    """Quienes pueden autorizar una salida: oficiales y suboficiales en actividad, de mayor a
    menor rango (pedido del tutor). Incluye al que ya figura, aunque hoy no lo sea."""
    filtro = Q(rango__escalafon__in=[Rango.Escalafon.OFICIAL, Rango.Escalafon.SUBOFICIAL],
               estado__nombre_estado="Activo")
    incluir = [pk for pk in incluir if pk]
    if incluir:
        filtro |= Q(pk__in=incluir)
    return Integrante.objects.filter(filtro).select_related("rango").order_by(
        F("rango__orden_jerarquico").asc(nulls_last=True), "apellido", "nombre")


def etiqueta_graduado(integrante):
    return f"{integrante.rango} {integrante.apellido}, {integrante.nombre}"


def preparar_salida(form, parte):
    """Unidad y Autorizó, como los usan Completar y la salida que marca el guardia en el detalle."""
    if "autorizo" in form.fields:
        form.fields["autorizo"].queryset = graduados([parte.autorizo_id])
        form.fields["autorizo"].label_from_instance = etiqueta_graduado
    if "vehiculo" in form.fields:
        moviles = Vehiculo.objects.filter(dado_de_baja=False)
        if parte.vehiculo_id:
            moviles = (moviles | Vehiculo.objects.filter(pk=parte.vehiculo_id)).distinct()
        form.fields["vehiculo"].queryset = moviles
        # Agrupados por categoría (Ambulancias, Rescate, Autobombas...), como la lista de Móviles.
        form.fields["vehiculo"].choices = opciones_agrupadas(moviles.select_related("tipo_vehiculo"), OPCION_VACIA)
        if not moviles.exists():
            form.fields["vehiculo"].help_text = "No hay móviles cargados: los carga Estadística en Móviles."


def preparar_tipo_y_motivo(form, parte):
    """Tipo y Motivo: en el parte de siniestro se ofrecen solo los tipos de la columna PARTE =
    SINIESTRO de los códigos de intervención, y solo los motivos activos (los "Reserva" de la
    planilla están apagados hasta que el cuartel los use).

    Lo que el parte ya tenía guardado se sigue ofreciendo aunque hoy esté apagado o sea de otra
    planilla: si no, no se podría guardar nada más de ese parte."""
    de_siniestro = TipoSiniestro.Parte.SINIESTRO
    if "tipo" in form.fields:
        tipos = Q(parte=de_siniestro)
        if parte.tipo_id:
            tipos |= Q(pk=parte.tipo_id)
        form.fields["tipo"].queryset = TipoSiniestro.objects.filter(tipos).distinct()
    if "motivo" in form.fields:
        # Un motivo sin tipos vale para todos (Motivo.vale_para), así que también se ofrece.
        motivos = Q(activo=True) & (Q(tipos__isnull=True) | Q(tipos__parte=de_siniestro))
        if parte.motivo_id:
            motivos |= Q(pk=parte.motivo_id)
        form.fields["motivo"].queryset = Motivo.objects.filter(motivos).distinct()


class SalidaForm(forms.ModelForm):
    """Lo primero que carga el guardia cuando sale la unidad (pedido del tutor): qué unidad sale y
    quién lo autorizó. Con eso se marca la hora de salida, en el mismo botón."""

    class Meta:
        model = ParteSiniestro
        fields = ["vehiculo", "autorizo"]
        labels = {"vehiculo": "U N°", "autorizo": "Autorizó"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            campo.required = True
            campo.empty_label = OPCION_VACIA
        self.fields["vehiculo"].error_messages["required"] = "Elegí la unidad que sale."
        self.fields["autorizo"].error_messages["required"] = "Elegí quién autorizó la salida."
        preparar_salida(self, self.instance)


def integrantes_por_merito(anio, parte=None, capacitacion=None, mantenimiento=None, incluir=(),
                           solo_activos=False):
    """Integrantes en actividad con su N° de mérito del año. Incluye a quienes ya figuran en el
    parte (o la capacitación, o el mantenimiento) aunque hoy estén de baja, para no perderlos al guardar.
    A una emergencia solo va el personal activo (pedido del tutor): `solo_activos` deja afuera a
    aspirantes, auxiliares y retirados."""
    merito = NumeroMerito.objects.filter(integrante=OuterRef("pk"), anio=anio)
    filtro = Q(estado__nombre_estado__in=["Activo"] if solo_activos else ["Activo", "Aspirante", "Auxiliar"])
    incluir = [pk for pk in incluir if pk]
    if incluir:
        filtro |= Q(pk__in=incluir)
    if parte is not None and parte.pk:
        filtro |= Q(participaciones__parte=parte)
    if capacitacion is not None and capacitacion.pk:
        filtro |= Q(capacitaciones=capacitacion) | Q(capacitaciones_ayudante=capacitacion)
    if mantenimiento is not None and mantenimiento.pk:
        filtro |= (Q(mantenimientos=mantenimiento) | Q(mantenimientos_autorizados=mantenimiento)
                   | Q(mantenimientos_a_cargo=mantenimiento) | Q(mantenimientos_controlados=mantenimiento))
    return (Integrante.objects.filter(filtro).distinct()
            .annotate(merito=Subquery(merito.values("numero_merito")[:1]))
            .order_by(F("merito").asc(nulls_last=True), "apellido"))


class DatosPorIntegrante:
    """Campos de cada persona marcada en la grilla (participación, concurrencia, si cobró...).
    Se crean para todos los de la grilla y la pantalla muestra solo los marcados.
    `self.filas` = [(integrante, [campos])] para la tabla de debajo de la grilla."""

    def agregar_campos_por_integrante(self, integrantes, campos):
        """`campos` = [(prefijo, fabrica(integrante) -> campo de formulario)]."""
        self.campos_por_integrante = [prefijo for prefijo, _ in campos]
        self.filas = []
        for integrante in integrantes:
            for prefijo, fabrica in campos:
                self.fields[f"{prefijo}_{integrante.pk}"] = fabrica(integrante)
            self.filas.append((integrante, [self[f"{prefijo}_{integrante.pk}"] for prefijo, _ in campos]))

    def dato_de(self, prefijo, integrante_pk, por_defecto=None):
        valor = self.cleaned_data.get(f"{prefijo}_{integrante_pk}")
        return por_defecto if valor in (None, "") else valor


class PersonalConcurrioForm(DatosPorIntegrante, forms.Form):
    """Grilla de números de mérito, como en la planilla: se marca quién concurrió y, de cada uno,
    la concurrencia y la participación (y si cobró, en los tipos que se cobran)."""
    integrantes = forms.ModelMultipleChoiceField(
        queryset=Integrante.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple, label="Personal que concurrió")

    def __init__(self, *args, parte, con_cobro=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.parte = parte
        campo = self.fields["integrantes"]
        campo.queryset = integrantes_por_merito(parte.anio, parte, solo_activos=True)
        campo.label_from_instance = etiqueta_merito
        actuales = {p.integrante_id: p for p in parte.personal.all()}
        self.initial.setdefault("integrantes", list(actuales))
        self.con_cobro = con_cobro

        def opciones(choices, campo_modelo):
            def fabrica(integrante):
                fila = actuales.get(integrante.pk)
                return forms.ChoiceField(
                    choices=choices, required=False, label=f"{campo_modelo.verbose_name.capitalize()} de {integrante}",
                    initial=getattr(fila, campo_modelo.name) if fila else campo_modelo.default)
            return fabrica

        campos = [
            ("conc", opciones(PartePersonal.Concurrencia.choices, PartePersonal._meta.get_field("tipo_concurrencia"))),
            ("part", opciones(PartePersonal.Participacion.choices, PartePersonal._meta.get_field("tipo_participacion"))),
        ]
        if con_cobro:
            campos.append(("cobro", lambda i: forms.BooleanField(
                required=False, label=f"Cobró: {i}", initial=bool(actuales.get(i.pk) and actuales[i.pk].cobro))))
        self.columnas = ["Concurrencia", "Participación"] + (["Cobró"] if con_cobro else [])
        self.agregar_campos_por_integrante(campo.queryset, campos)

    def save(self):
        elegidos = {i.pk: i for i in self.cleaned_data["integrantes"]}
        for fila in self.parte.personal.all():
            if fila.integrante_id not in elegidos:
                fila.delete()
        for pk, integrante in elegidos.items():
            fila = (PartePersonal.objects.filter(parte=self.parte, integrante=integrante).first()
                    or PartePersonal(parte=self.parte, integrante=integrante))
            fila.tipo_concurrencia = self.dato_de("conc", pk, fila.tipo_concurrencia)
            fila.tipo_participacion = self.dato_de("part", pk, fila.tipo_participacion)
            if self.con_cobro:
                fila.cobro = bool(self.cleaned_data.get(f"cobro_{pk}"))
            fila.save()


class EntidadesForm(forms.Form):
    """Policial / Tránsito / Forense / Juzgado...: Sí o No, N° de móvil y A cargo."""

    def __init__(self, *args, parte, **kwargs):
        super().__init__(*args, **kwargs)
        self.parte = parte
        self.tipos = list(TipoEntidad.objects.order_by("pk"))
        existentes = {}
        for entidad in parte.entidades.order_by("-fecha_captura_local"):
            existentes[entidad.tipo_entidad_id] = entidad  # queda la primera cargada
        self.renglones = []
        for tipo in self.tipos:
            entidad = existentes.get(tipo.pk)
            p = f"entidad_{tipo.pk}"
            self.fields[f"{p}_intervino"] = forms.BooleanField(
                required=False, label=tipo.descripcion, initial=entidad is not None)
            self.fields[f"{p}_movil"] = forms.CharField(
                required=False, max_length=30, label="N° de móvil",
                initial=entidad.numero_movil if entidad else "")
            self.fields[f"{p}_cargo"] = forms.CharField(
                required=False, max_length=120, label="A cargo",
                initial=entidad.a_cargo if entidad else "")
            self.renglones.append((self[f"{p}_intervino"], self[f"{p}_movil"], self[f"{p}_cargo"]))

    def clean(self):
        datos = super().clean()
        for tipo in self.tipos:
            p = f"entidad_{tipo.pk}"
            # Si anotaron el móvil o quién estaba a cargo, intervino aunque no tildaran "Sí".
            if datos.get(f"{p}_movil") or datos.get(f"{p}_cargo"):
                datos[f"{p}_intervino"] = True
        return datos

    def save(self):
        for tipo in self.tipos:
            p = f"entidad_{tipo.pk}"
            filas = list(self.parte.entidades.filter(tipo_entidad=tipo).order_by("fecha_captura_local"))
            if not self.cleaned_data[f"{p}_intervino"]:
                for fila in filas:
                    fila.delete()
                continue
            fila = filas[0] if filas else EntidadIntervino(parte=self.parte, tipo_entidad=tipo)
            fila.numero_movil = self.cleaned_data[f"{p}_movil"]
            fila.a_cargo = self.cleaned_data[f"{p}_cargo"]
            fila.save()


class VehiculoSiniestradoForm(ControlDeDatos, forms.ModelForm):
    # Como en la planilla, se elige el número de una lista.
    rodado_numero = forms.TypedChoiceField(label="Rodado N°", coerce=int, choices=OPCIONES_RODADO)

    class Meta:
        model = VehiculoSiniestrado
        fields = ["rodado_numero", "marca", "modelo", "dominio", "seguro", "observaciones"]
        labels = {"dominio": "Dominio (patente)"}


class BaseRodados(forms.BaseInlineFormSet):
    """Muestra 4 lugares para rodados, como la planilla (contando los ya cargados); más, con
    "Agregar rodado". Los lugares que quedan vacíos no se guardan."""
    LUGARES = 4

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.extra = max(0, self.LUGARES - self.get_queryset().count())

    def clean(self):
        # Antes que la validación de Django (que avisaría con un mensaje genérico de "datos repetidos").
        usados = set()
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or (form in self.extra_forms and not form.has_changed()):
                continue
            if self.can_delete and self._should_delete_form(form):
                continue
            numero = form.cleaned_data.get("rodado_numero")
            if numero in usados:
                raise forms.ValidationError(
                    f"El rodado N° {numero} está repetido: cada rodado del parte lleva un número distinto.")
            if numero is not None:
                usados.add(numero)
        super().clean()


VehiculoSiniestradoFormSet = forms.inlineformset_factory(
    ParteSiniestro, VehiculoSiniestrado, form=VehiculoSiniestradoForm, formset=BaseRodados,
    can_delete=True,
)

CAMPOS_DAMNIFICADO = ["apellido_nombre", "edad", "sexo", "dni", "telefono", "rol", "triage", "lesion",
                      "trasladado", "trasladado_por", "hacia", "hacia_otro",
                      "recibe_dr", "recibe_matricula", "recibe_firma",
                      "domicilio", "domicilio_numero", "localidad", "provincia"]
ETIQUETAS_DAMNIFICADO = {"apellido_nombre": "Apellido y nombre", "telefono": "N° teléfono",
                         "lesion": "Descripción de la lesión",
                         "rol": "Propietario / conductor / acompañante / peatón",
                         "recibe_dr": "Recibe Dr./a", "recibe_matricula": "Matrícula",
                         "hacia": "Hacia", "hacia_otro": "Otro: especificá"}
WIDGETS_DAMNIFICADO = {
    "triage": forms.RadioSelect,
    "localidad": forms.TextInput(attrs={"list": "localidades", "autocomplete": "off"}),
    "provincia": forms.TextInput(attrs={"list": "provincias", "autocomplete": "off"}),
    "recibe_dr": forms.TextInput(attrs={"list": "medicos", "autocomplete": "off"}),
    "recibe_firma": forms.HiddenInput(attrs={"data-firma": "1"}),
}
OTRO_CENTRO = "otro"  # opción "Otro" de la lista Hacia


class CampoDamnificado:
    """Triage: con un color queda herido; "Sin lesiones" es una opción más. Hacia: lista de
    centros de salud más "Otro", que pide especificar."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        triage = self.fields["triage"]
        triage.choices = [c for c in triage.choices if c[0]]  # se destilda tocando de nuevo el color
        triage.required = False
        hacia = self.fields["hacia"]
        hacia.required = False
        self.fields["hacia"] = forms.ChoiceField(
            label=hacia.label, required=False,
            choices=[("", OPCION_VACIA)] + [(str(c.pk), str(c)) for c in hacia.queryset] + [(OTRO_CENTRO, "Otro")])
        if self.instance.hacia_id:
            self.initial["hacia"] = str(self.instance.hacia_id)
        elif self.instance.hacia_otro:
            self.initial["hacia"] = OTRO_CENTRO

    def clean_hacia(self):
        valor = self.cleaned_data["hacia"]
        if not valor or valor == OTRO_CENTRO:
            return None
        return CentroSalud.objects.filter(pk=valor).first()

    def clean(self):
        datos = super().clean()
        if self.data.get(self.add_prefix("hacia")) != OTRO_CENTRO:
            datos["hacia_otro"] = ""
        elif not datos.get("hacia_otro"):
            self.add_error("hacia_otro", "Escribí a qué centro de salud lo trasladaron.")
        return datos


class DamnificadoParteForm(CampoDamnificado, ControlDeDatos, forms.ModelForm):
    """Damnificado cargado desde Completar: se indica el Rodado N°, como en la planilla,
    o "Sin rodado" (incendio, rescate, emergencia médica)."""
    rodado_numero = forms.TypedChoiceField(
        label="Rodado N°", coerce=int, required=False, empty_value=None,
        choices=[("", "Sin rodado")] + OPCIONES_RODADO[1:])

    class Meta:
        model = Damnificado
        fields = CAMPOS_DAMNIFICADO
        labels = ETIQUETAS_DAMNIFICADO
        widgets = WIDGETS_DAMNIFICADO

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.vehiculo_siniestrado_id:
            self.initial["rodado_numero"] = self.instance.vehiculo_siniestrado.rodado_numero


class BaseDamnificadosParte(forms.BaseModelFormSet):
    def __init__(self, *args, parte, **kwargs):
        self.parte = parte
        kwargs["queryset"] = (Damnificado.objects.filter(parte=parte)
                              .select_related("vehiculo_siniestrado")
                              .order_by(F("vehiculo_siniestrado__rodado_numero").asc(nulls_last=True),
                                        "fecha_captura_local"))
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        super().save(commit=False)
        for damnificado in self.deleted_objects:
            damnificado.delete()
        # Primero los que traen firma: los demás del mismo médico la reusan en este mismo guardado.
        for form in sorted(self.saved_forms, key=lambda f: not f.cleaned_data.get("recibe_firma")):
            numero = form.cleaned_data["rodado_numero"]
            rodado = None
            if numero:
                # Si el rodado todavía no se cargó arriba, se crea vacío con ese número.
                rodado, _ = VehiculoSiniestrado.objects.get_or_create(parte=self.parte, rodado_numero=numero)
            form.instance.parte = self.parte
            form.instance.vehiculo_siniestrado = rodado
            form.instance.save()


DamnificadosParteFormSet = forms.modelformset_factory(
    Damnificado, form=DamnificadoParteForm, formset=BaseDamnificadosParte,
    extra=1, can_delete=True,
)

class DamnificadoForm(CampoDamnificado, ControlDeDatos, forms.ModelForm):
    class Meta:
        model = Damnificado
        fields = CAMPOS_DAMNIFICADO
        labels = ETIQUETAS_DAMNIFICADO
        widgets = WIDGETS_DAMNIFICADO


DamnificadoFormSet = forms.inlineformset_factory(
    VehiculoSiniestrado, Damnificado, form=DamnificadoForm,
    extra=1, can_delete=True,
)


LO_DANADO = ["Rodado", "Casa", "Galpón", "Fábrica", "Industria", "Campo", "Rollo de fardo",
             "Pastizales", "Baldío", "Otros"]


class IncendioDatoForm(ControlDeDatos, forms.ModelForm):
    """INCENDIO - Datos damnificado y datos sobre lo dañado, como en la planilla."""
    lo_danado = forms.MultipleChoiceField(
        label="Datos sobre lo dañado", required=False, widget=forms.CheckboxSelectMultiple,
        choices=[(x, x) for x in LO_DANADO])

    class Meta:
        model = IncendioDato
        fields = ["damnificado_nombre", "edad", "dni", "descripcion"]
        labels = {"damnificado_nombre": "Apellido y nombre", "descripcion": "Descripción"}
        help_texts = {"descripcion": "Si marcaste Otros, contá qué se dañó."}
        widgets = {"descripcion": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        marcados = [x.strip() for x in self.instance.tipo_bien_danado.split(",") if x.strip()]
        extras = [(x, x) for x in marcados if x not in LO_DANADO]  # cargados a mano en el admin
        self.fields["lo_danado"].choices += extras
        self.initial.setdefault("lo_danado", marcados)

    def tiene_datos(self):
        return bool(self.instance.pk) or any(
            self.cleaned_data.get(c) for c in ["lo_danado", *self.Meta.fields])

    def save(self, commit=True):
        self.instance.tipo_bien_danado = ", ".join(self.cleaned_data["lo_danado"])
        return super().save(commit)


class NegativaAtencionForm(ControlDeDatos, forms.ModelForm):
    class Meta:
        model = NegativaAtencion
        fields = ["fecha", "damnificado", "atendio", "matricula_atendio",
                  "tipo_firmante", "nombre_firmante", "dni_firmante", "edad_firmante",
                  "relacion_paciente", "domicilio_firmante", "testigo_nombre", "testigo_dni"]
        widgets = {"fecha": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def __init__(self, *args, parte, **kwargs):
        super().__init__(*args, **kwargs)
        self.parte = parte
        self.fields["damnificado"].queryset = Damnificado.objects.filter(parte=parte)
        self.fields["damnificado"].required = False
        self.fields["damnificado"].help_text = "Si la persona figura como damnificado de este parte."

    def clean(self):
        datos = super().clean()
        if datos.get("tipo_firmante") != NegativaAtencion.TipoFirmante.PACIENTE \
                and not datos.get("relacion_paciente"):
            self.add_error("relacion_paciente", "Indicá el vínculo con el paciente.")
        return datos


class SubidaMultiple(forms.ClearableFileInput):
    allow_multiple_selected = True


class CampoArchivosMultiples(forms.FileField):
    """FileField que acepta varios archivos (patrón de la documentación de Django)."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", SubidaMultiple(attrs={"accept": "image/*"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        if isinstance(data, (list, tuple)):
            return [super(CampoArchivosMultiples, self).clean(d, initial) for d in data]
        return [super().clean(data, initial)]


class FotosForm(forms.Form):
    archivos = CampoArchivosMultiples(label="Fotos del hecho")
    descripcion = forms.CharField(label="Descripción (opcional)", max_length=200, required=False)

    def clean_archivos(self):
        archivos = self.cleaned_data["archivos"]
        for archivo in archivos:
            if not (archivo.content_type or "").startswith("image/"):
                raise forms.ValidationError(f"«{archivo.name}» no es una imagen.")
            if archivo.size > 15 * 1024 * 1024:
                raise forms.ValidationError(f"«{archivo.name}» pesa más de 15 MB.")
        return archivos


# ---------------------------------------------------------------------------
# Ficha del bombero (como en RUBA)
# ---------------------------------------------------------------------------

FECHA = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")

# Secciones de la ficha, en el orden de RUBA. Las usan el formulario y la pantalla de la ficha.
SECCIONES_FICHA = [
    ("Identificación", ["legajo", "apellido", "nombre", "dni", "fecha_nacimiento", "sexo", "foto", "usuario"]),
    ("Contacto", ["domicilio", "localidad", "telefono", "email"]),
    ("Salud", ["grupo_sanguineo", "factor_rh", "donante_sangre", "altura", "peso", "ficha_medica"]),
    ("Carrera en el cuerpo", ["rango", "estado", "cargo", "grupo_guardia", "fecha_ingreso", "fecha_cadete", "fecha_aspirante",
                              "fecha_bombero", "fecha_ultimo_ascenso", "es_jefe_activo"]),
    ("Instructor y forestal", ["instructor_cuartel", "instructor_regional", "instructor_federacion",
                               "es_bombero_forestal", "tipo_forestal", "fecha_certificacion_forestal"]),
    ("Formación y familia", ["formacion", "nivel_educativo", "titulo_obtenido", "matricula_profesional",
                             "tiene_hijos"]),
]


class IntegranteForm(ControlDeDatos, forms.ModelForm):
    numero_merito = forms.IntegerField(
        label="N° de este año", required=False, min_value=1, max_value=999,
        help_text="Del 001 al 999, lo asigna Estadística. Cambia cada año.")

    class Meta:
        model = Integrante
        fields = [campo for _, campos in SECCIONES_FICHA for campo in campos]
        labels = {"es_jefe_activo": "Es Jefe de Cuerpo Activo", "telefono": "Teléfono", "dni": "DNI",
                  "fecha_ultimo_ascenso": "Fecha del último ascenso", "fecha_nacimiento": "Fecha de nacimiento",
                  "grupo_sanguineo": "Grupo sanguíneo", "factor_rh": "Factor RH",
                  "donante_sangre": "Donante de sangre", "grupo_guardia": "Grupo de guardia", "altura": "Altura (m)", "peso": "Peso (kg)",
                  "ficha_medica": "Ficha médica", "fecha_ingreso": "Fecha de ingreso",
                  "fecha_cadete": "Fecha de cadete", "fecha_aspirante": "Fecha de aspirante",
                  "fecha_bombero": "Fecha de bombero", "instructor_cuartel": "Instructor de cuartel",
                  "instructor_regional": "Instructor regional", "instructor_federacion": "Instructor de federación",
                  "es_bombero_forestal": "Es bombero forestal", "tipo_forestal": "Tipo de bombero forestal",
                  "fecha_certificacion_forestal": "Fecha de certificación forestal", "formacion": "Formación",
                  "titulo_obtenido": "Título obtenido", "matricula_profesional": "Matrícula profesional",
                  "email": "Email"}
        widgets = {nombre: FECHA for nombre in [
            "fecha_nacimiento", "fecha_ingreso", "fecha_cadete", "fecha_aspirante", "fecha_bombero",
            "fecha_ultimo_ascenso", "fecha_certificacion_forestal"]}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.anio = timezone.localdate().year
        if self.instance.pk:
            self.initial.setdefault("numero_merito", self.instance.numero_merito_en(self.anio))
        self.fields["usuario"].help_text = "Usuario con el que inicia sesión (opcional)."
        self.secciones = [(titulo, [self[c] for c in campos]) for titulo, campos in SECCIONES_FICHA]

    def clean_numero_merito(self):
        numero = self.cleaned_data["numero_merito"]
        if numero and NumeroMerito.objects.filter(anio=self.anio, numero_merito=numero).exclude(
                integrante=self.instance.pk).exists():
            raise forms.ValidationError(f"El N° {numero} ya lo tiene otro integrante en {self.anio}.")
        return numero

    def save(self, commit=True):
        integrante = super().save(commit)
        numero = self.cleaned_data.get("numero_merito")
        if commit and numero:
            NumeroMerito.objects.update_or_create(
                integrante=integrante, anio=self.anio,
                defaults={"numero_merito": numero, "rango": integrante.rango})
        return integrante


class FamiliarForm(ControlDeDatos, forms.ModelForm):
    class Meta:
        model = Familiar
        fields = ["vinculo", "apellido", "nombre", "dni", "fecha_nacimiento", "vive", "observaciones"]
        labels = {"vinculo": "Vínculo"}
        widgets = {"fecha_nacimiento": FECHA}


class HistorialForm(forms.ModelForm):
    class Meta:
        model = HistorialIntegrante
        fields = ["fecha_desde", "cuerpo", "estado", "formacion", "motivo_baja", "fin_licencia"]
        labels = {"motivo_baja": "Motivo de baja", "fin_licencia": "Fin de licencia", "formacion": "Formación"}
        widgets = {"fecha_desde": FECHA, "fin_licencia": FECHA}


FamiliarFormSet = forms.inlineformset_factory(Integrante, Familiar, form=FamiliarForm, extra=0, can_delete=True)
HistorialFormSet = forms.inlineformset_factory(Integrante, HistorialIntegrante, form=HistorialForm,
                                               extra=0, can_delete=True)


def sincronizar_grilla(filas, elegidos, nueva_fila):
    """Deja en la tabla intermedia (`filas`, un related manager) solo a los integrantes
    elegidos en la grilla: quita a los destildados y crea con `nueva_fila(pk)` a los nuevos."""
    elegidos = {i.pk for i in elegidos}
    filas.exclude(integrante__in=elegidos).delete()
    ya = set(filas.values_list("integrante", flat=True))
    filas.model.objects.bulk_create(nueva_fila(pk) for pk in elegidos - ya)


def etiqueta_merito(integrante):
    return f"{integrante.merito:03d}" if integrante.merito else integrante.apellido


def etiqueta_integrante(integrante):
    return (f"{integrante.merito:03d} " if integrante.merito else "") + f"{integrante.apellido}, {integrante.nombre}"


class CapacitacionForm(DatosPorIntegrante, ControlDeDatos, forms.ModelForm):
    """Planilla "Control de Capacitación": datos del curso y grilla de quienes asistieron."""
    lista_destinos = forms.MultipleChoiceField(
        label="Destino de la misma", required=False, choices=Capacitacion.DESTINOS,
        widget=forms.CheckboxSelectMultiple)
    # 10/11: si es de un departamento, se tilda y se eligen de la tabla Departamento. No hay campo en
    # la base: se deduce de si la capacitación tiene códigos de departamento.
    de_departamento = forms.BooleanField(label="Capacitación de departamento", required=False)
    departamentos = forms.MultipleChoiceField(label="Departamentos", required=False,
                                              widget=forms.CheckboxSelectMultiple)
    unidad = forms.BooleanField(label="Unidad", required=False)
    asistentes = forms.ModelMultipleChoiceField(
        queryset=Integrante.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple, label="Personal que asistió")

    class Meta:
        model = Capacitacion
        fields = ["obligatoria", "modalidad", "curso_especial", "fecha", "hora_inicio", "fecha_fin", "hora_fin",
                  "vehiculo", "km_regreso", "guardia", "guardia_desde", "guardia_hasta", "lugar", "nivel",
                  "a_cargo_integrante", "a_cargo", "ayudantes", "sentido", "datos", "otro_destino"]
        labels = {"km_regreso": "Km de regreso", "a_cargo_integrante": "A cargo de", "vehiculo": "N°",
                  "curso_especial": "Cursos especiales", "guardia": "Personal de guardia",
                  "fecha_fin": "Fecha (terminó)", "guardia_desde": "Empezó Hs.", "guardia_hasta": "Terminó Hs.",
                  "hora_inicio": "Empezó Hs.", "hora_fin": "Terminó Hs.", "modalidad": "Interna / Externa",
                  "nivel": "Nivel de la capacitación", "sentido": "Dictada / recibida",
                  "ayudantes": "Ayud. Inst."}
        widgets = {
            "fecha": FECHA, "fecha_fin": FECHA, "hora_inicio": HORA, "hora_fin": HORA,
            "guardia_desde": HORA, "guardia_hasta": HORA,
            "modalidad": forms.RadioSelect, "nivel": forms.RadioSelect, "sentido": forms.RadioSelect,
            "ayudantes": forms.CheckboxSelectMultiple,
            "lugar": forms.Textarea(attrs={"rows": 2}),
            "datos": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.anio = anio = (self.instance.fecha or timezone.localdate()).year
        self.fields["datos"].required = True
        self.fields["datos"].error_messages["required"] = "Anotá los datos de la capacitación."
        self.initial.setdefault("unidad", bool(self.instance.vehiculo_id))
        asistentes = self.fields["asistentes"]
        asistentes.queryset = integrantes_por_merito(anio, capacitacion=self.instance)
        if self.instance.pk:
            self.initial.setdefault("asistentes", list(self.instance.asistentes.values_list("pk", flat=True)))
        asistentes.label_from_instance = etiqueta_merito
        ayudantes = self.fields["ayudantes"]
        ayudantes.queryset = integrantes_por_merito(anio, capacitacion=self.instance)
        ayudantes.label_from_instance = etiqueta_merito  # grilla, como la de asistentes
        integrantes = integrantes_por_merito(anio, capacitacion=self.instance,
                                             incluir=[self.instance.a_cargo_integrante_id])
        for nombre in ("guardia", "a_cargo_integrante"):
            self.fields[nombre].queryset = integrantes
            self.fields[nombre].label_from_instance = etiqueta_integrante
        self.fields["a_cargo_integrante"].help_text = "Del cuerpo. Si es de afuera, dejalo vacío y escribilo abajo."
        moviles = Vehiculo.objects.filter(dado_de_baja=False)
        if self.instance.vehiculo_id:
            moviles = (moviles | Vehiculo.objects.filter(pk=self.instance.vehiculo_id)).distinct()
        self.fields["vehiculo"].queryset = moviles
        if not moviles.exists():
            self.fields["vehiculo"].help_text = "No hay móviles cargados: los carga Estadística en Móviles."
        codigos = self.instance.codigos_destinos
        fijos = {c for c, _ in Capacitacion.DESTINOS}
        todos = {d.codigo: d for d in Departamento.objects.all()}
        elegidos = [c for c in codigos if c in todos]
        # Los activos y, si la capacitación ya tenía uno desactivado, también ese (para no perderlo).
        self.fields["departamentos"].choices = [(d.codigo, d.nombre) for d in todos.values()
                                                if d.activo or d.codigo in elegidos]
        self.initial.setdefault("lista_destinos", [c for c in codigos if c in fijos])
        self.initial.setdefault("departamentos", elegidos)
        self.initial.setdefault("de_departamento", bool(elegidos))
        # Códigos que ya no se ofrecen (ej. "IV Nivel", sacado el 10/11): se conservan al guardar.
        self.destinos_conservados = [c for c in codigos if c not in fijos and c not in todos]
        cobros = (set(self.instance.asistencias.filter(cobro=True).values_list("integrante", flat=True))
                  if self.instance.pk else set())
        self.columnas = ["Cobró"]
        self.agregar_campos_por_integrante(asistentes.queryset, [("cobro", lambda i: forms.BooleanField(
            required=False, label=f"Cobró: {i}", initial=i.pk in cobros))])

    def clean(self):
        datos = super().clean()
        if not datos.get("de_departamento"):
            datos["departamentos"] = []
        elif not datos.get("departamentos"):
            self.add_error("departamentos", "Elegí el departamento (o destildá «Capacitación de departamento»).")
        if not datos.get("unidad"):  # Unidad: No
            datos["vehiculo"], datos["km_regreso"] = None, None
        vehiculo, km = datos.get("vehiculo"), datos.get("km_regreso")
        if vehiculo and km is not None:
            # Más que lo anotado antes para ese móvil (pedido del tutor).
            minimo = vehiculo.km_anterior(datos.get("fecha"), excluir=self.instance,
                                          con_actual=not self.instance.pk)
            if km <= minimo:
                self.add_error("km_regreso", f"Debe ser mayor al último km anotado para el móvil ({minimo} km).")
        return datos

    def save(self, commit=True):
        self.instance.destinos = ",".join([*self.cleaned_data["lista_destinos"], *self.cleaned_data["departamentos"],
                                           *self.destinos_conservados])
        capacitacion = super().save(commit=commit)
        if commit:
            self._guardar_asistentes()
        return capacitacion

    def _guardar_asistentes(self):
        sincronizar_grilla(self.instance.asistencias, self.cleaned_data["asistentes"],
                           lambda pk: AsistenciaCapacitacion(capacitacion=self.instance, integrante_id=pk))
        cobraron = [i.pk for i in self.cleaned_data["asistentes"] if self.cleaned_data.get(f"cobro_{i.pk}")]
        self.instance.asistencias.update(cobro=False)
        self.instance.asistencias.filter(integrante__in=cobraron).update(cobro=True)


class NumerosPeriodoForm(forms.Form):
    """Armado del período: Estadística asigna a mano el N° de mérito de cada bombero
    (viene propuesto el del año anterior). Las bajas se dan, firmadas, desde la ficha."""

    def __init__(self, *args, periodo, **kwargs):
        from .periodos import candidatos_numeros
        super().__init__(*args, **kwargs)
        self.periodo = periodo
        self.anio = anio = periodo.anio
        actuales = dict(NumeroMerito.objects.filter(anio=anio).values_list("integrante", "numero_merito"))
        anteriores = dict(NumeroMerito.objects.filter(anio=anio - 1).values_list("integrante", "numero_merito"))
        # Si todavía no se asignó ningún número este año, se propone el del año anterior.
        propuestos = actuales or anteriores
        self.filas = []
        for integrante in candidatos_numeros(anio):
            self.fields[f"numero_{integrante.pk}"] = forms.IntegerField(
                required=False, min_value=1, max_value=999, initial=propuestos.get(integrante.pk),
                label=f"N° de {integrante}", widget=forms.TextInput(attrs={
                    "inputmode": "numeric", "pattern": "[0-9]{1,3}", "size": "4", "autocomplete": "off"}))
            self.filas.append({
                "integrante": integrante, "numero": self[f"numero_{integrante.pk}"],
                "anterior": anteriores.get(integrante.pk),
            })

    def clean(self):
        datos = super().clean()
        vistos = {}
        for fila in self.filas:
            pk = fila["integrante"].pk
            numero = datos.get(f"numero_{pk}")
            if numero is None:
                continue
            if numero in vistos:
                self.add_error(f"numero_{pk}", f"El {numero:03d} ya se lo diste a {vistos[numero]}.")
            vistos[numero] = fila["integrante"]
        return datos

    def save(self):
        """Reemplaza los números del año por los de la pantalla."""
        NumeroMerito.objects.filter(anio=self.anio).delete()
        nuevos = []
        for fila in self.filas:
            integrante = fila["integrante"]
            numero = self.cleaned_data.get(f"numero_{integrante.pk}")
            if numero is not None:
                nuevos.append(NumeroMerito(integrante=integrante, anio=self.anio,
                                           numero_merito=numero, rango=integrante.rango))
        NumeroMerito.objects.bulk_create(nuevos)
        return len(nuevos)


class MantenimientoForm(ControlDeDatos, forms.ModelForm):
    """Planilla "Control de Mantenimiento": el móvil, qué se hizo, costos y quiénes trabajaron."""
    participantes = forms.ModelMultipleChoiceField(
        queryset=Integrante.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple, label="Personal que trabajó")

    class Meta:
        model = Mantenimiento
        fields = ["obligatoria", "fecha", "hora_inicio", "hora_fin", "guardia", "guardia_desde", "guardia_hasta",
                  "lugar", "taller", "autorizo", "a_cargo", "vehiculo", "elemento", "descripcion", "tipo", "registro_libros",
                  "motivo", "costo_repuestos", "repuestos_pedidos", "horas_taller", "costo_hora", "costo_bombero",
                  "observaciones", "controlo"]
        labels = {"vehiculo": "Vehículo en reparación (si es un móvil)", "motivo": "Motivo del mismo", "guardia": "Guardia",
                  "lugar": "Lugar de reparación", "autorizo": "Rep. autorizada por",
                  "descripcion": "Datos mant. efectuado", "tipo": "Tipo de mantenimiento",
                  "registro_libros": "Registro en libros", "costo_repuestos": "Costo aprox. repuestos",
                  "repuestos_pedidos": "Pedidos el", "horas_taller": "Horas taller empleados",
                  "costo_hora": "Costo real hora", "costo_bombero": "Costo p/ bombero", "controlo": "Controló"}
        widgets = {
            "fecha": FECHA, "repuestos_pedidos": FECHA, "hora_inicio": HORA, "hora_fin": HORA,
            "guardia_desde": HORA, "guardia_hasta": HORA,
            "tipo": forms.RadioSelect, "lugar": forms.RadioSelect,
            "descripcion": forms.Textarea(attrs={"rows": 4}),
            "observaciones": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.anio = anio = (self.instance.fecha or timezone.localdate()).year
        integrantes = integrantes_por_merito(anio, mantenimiento=self.instance)
        participantes = self.fields["participantes"]
        participantes.queryset = integrantes
        participantes.label_from_instance = etiqueta_merito
        if self.instance.pk:
            self.initial.setdefault("participantes",
                                    list(self.instance.participantes.values_list("pk", flat=True)))
        for nombre in ("autorizo", "a_cargo", "controlo", "guardia"):
            self.fields[nombre].queryset = integrantes
            self.fields[nombre].label_from_instance = etiqueta_integrante
        moviles = Vehiculo.objects.filter(dado_de_baja=False)
        if self.instance.vehiculo_id:
            moviles = moviles | Vehiculo.objects.filter(pk=self.instance.vehiculo_id)
        self.fields["vehiculo"].queryset = moviles.distinct()
        if not self.fields["vehiculo"].queryset.exists():
            self.fields["vehiculo"].help_text = "No hay móviles cargados: los carga Estadística en Móviles."

    def save(self, commit=True):
        mantenimiento = super().save(commit=commit)
        if commit:
            sincronizar_grilla(self.instance.participaciones, self.cleaned_data["participantes"],
                               lambda pk: ParticipacionMantenimiento(mantenimiento=self.instance, integrante_id=pk))
        return mantenimiento


class CorreccionCerradoForm(forms.Form):
    """Para corregir un parte ya cerrado: el motivo es obligatorio y se confirma con la contraseña.
    Queda una firma nueva con lo que cambió; la del cierre no se toca."""
    motivo_correccion = forms.CharField(
        label="Motivo de la corrección", min_length=10,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Por qué se corrige un parte que ya estaba cerrado. Queda registrado a tu nombre.")
    clave = forms.CharField(label="Tu contraseña", strip=False,
                            widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))

    def __init__(self, *args, usuario, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario

    def clean_clave(self):
        clave = self.cleaned_data["clave"]
        if not self.usuario.check_password(clave):
            raise forms.ValidationError("La contraseña no es correcta.")
        return clave


class FirmarForm(forms.Form):
    """Confirmación de una decisión importante con la contraseña de quien la toma: la
    firma queda ligada a sus credenciales."""
    clave = forms.CharField(label="Tu contraseña", strip=False,
                            widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))

    def __init__(self, *args, usuario, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario

    def clean_clave(self):
        clave = self.cleaned_data["clave"]
        if not self.usuario.check_password(clave):
            raise forms.ValidationError("La contraseña no es correcta: no se firmó nada.")
        return clave


class BajaForm(FirmarForm):
    motivo = forms.CharField(label="Motivo de la baja", max_length=150,
                             help_text="Ej: renuncia, pase a otro cuerpo, inasistencias.")
    field_order = ["motivo", "clave"]


class CalleForm(forms.ModelForm):
    """Calle que falta en el listado (la agrega Estadística)."""

    class Meta:
        model = Calle
        fields = ["nombre"]
        labels = {"nombre": "Nombre de la calle"}

    def clean_nombre(self):
        nombre = " ".join(self.cleaned_data["nombre"].split())
        if nombre_oficial(nombre).casefold() != nombre.casefold() or nombre_oficial(nombre) in calles():
            raise forms.ValidationError(f"Ya está en el listado como «{nombre_oficial(nombre)}».")
        return nombre


class VehiculoForm(ControlDeDatos, forms.ModelForm):
    """Alta y edición de un móvil del cuartel (sin pasar por el admin)."""

    class Meta:
        model = Vehiculo
        fields = ["legajo", "tipo_vehiculo", "marca", "modelo", "patente", "anio", "combustible",
                  "kilometraje_actual", "fecha_vto_verificacion", "en_servicio", "dado_de_baja"]
        labels = {"legajo": "U N° (número de móvil)", "tipo_vehiculo": "Tipo", "patente": "Dominio (patente)",
                  "anio": "Año", "kilometraje_actual": "Kilometraje actual",
                  "fecha_vto_verificacion": "Vencimiento de la verificación"}
        widgets = {"fecha_vto_verificacion": FECHA}


class PersonalE100Form(ControlDeDatos, forms.ModelForm):
    """Alta y edición del personal E-100 (lo pone la municipalidad)."""

    class Meta:
        model = PersonalE100
        fields = ["apellido_nombre", "dni", "telefono", "fecha_asignacion", "hasta", "activo"]
        widgets = {"fecha_asignacion": FECHA, "hasta": FECHA}


class AnotacionForm(forms.Form):
    """Una anotación del libro de guardia: la hora del hecho y lo que pasó, como se escribe en el libro."""
    hora = forms.TimeField(label="Hs.", widget=HORA)
    texto = forms.CharField(label="Anotación", widget=forms.Textarea(attrs={"rows": 3}),
                            help_text="Ej: Ingresa a la guardia el Bv. Turaglio F. / Sale U°6 por un 02-111, a cargo del Bv. Leyba G.")


class CorreccionAnotacionForm(forms.Form):
    texto = forms.CharField(label="Texto corregido", widget=forms.Textarea(attrs={"rows": 3}))
    motivo = forms.CharField(label="Motivo de la corrección", min_length=10, max_length=250)


class OrdenEntregaForm(ControlDeDatos, forms.ModelForm):
    class Meta:
        model = OrdenEntrega
        exclude = []
        widgets = {"fecha": FECHA, "fecha_devolucion": FECHA, "movimiento": forms.RadioSelect,
                   "calidad": forms.RadioSelect, "motivo": forms.Textarea(attrs={"rows": 2}),
                   "novedades": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nombre in ("entregado_por", "autoriza"):
            self.fields[nombre].queryset = integrantes_por_merito(timezone.localdate().year, solo_activos=True,
                                                                incluir=[getattr(self.instance, f"{nombre}_id", None)])
            self.fields[nombre].label_from_instance = etiqueta_integrante


ElementosFormSet = forms.inlineformset_factory(
    OrdenEntrega, ElementoOrden, fields=["cantidad", "descripcion", "estado"], extra=4, can_delete=True)
