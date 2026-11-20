import hashlib
import logging
import uuid
from datetime import date, timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from .forms import (
    AnotacionForm, BajaForm, ElementosFormSet, OrdenEntregaForm, CorreccionAnotacionForm, CorreccionCerradoForm, PersonalE100Form, CapacitacionForm, CompletarParteForm, DamnificadoFormSet, FirmarForm, MantenimientoForm,
    NumerosPeriodoForm, DamnificadosParteFormSet, EntidadesForm, FamiliarFormSet,
    FotosForm, HistorialFormSet, IntegranteForm, SECCIONES_FICHA,
    IncendioDatoForm, NegativaAtencionForm, ParteSiniestroForm, PersonalConcurrioForm,
    CalleForm, REGEX_DIRECCION, REGEX_ESQUINA, SalidaForm, VehiculoForm, VehiculoSiniestradoFormSet,
)
from .models import (
    AnotacionLibroGuardia, OrdenEntrega,
    PersonalE100,
    Barrio, Calle, Capacitacion, Damnificado, Firma, HistorialIntegrante, Medico, Mantenimiento, Periodo, DescargaOffline, EnvioCompletar, EstadoIntegrante, EstadoParte, IncendioDato, Integrante, Motivo, NegativaAtencion, NumeroMerito,
    ParteFoto, PartePersonal, ParteSiniestro, Rango, TipoSiniestro, Vehiculo, VehiculoSiniestrado,
)
from . import geocodificacion, offline, periodos
from .moviles import agrupar as agrupar_moviles
from .roles import puede_completar
from .sesion import aviso_sesion

log = logging.getLogger(__name__)


def requiere_permiso(permiso, mensaje, volver="detalle_parte"):
    """Como permission_required, pero avisa con un mensaje en vez de un 403 pelado.
    Vuelve a `volver` (con el pk de la URL) o al menú."""
    def decorador(vista):
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            if not request.user.has_perm(permiso):
                messages.error(request, mensaje)
                if "pk" in kwargs and volver:
                    return redirect(volver, pk=kwargs["pk"])
                return redirect("menu")
            return vista(request, *args, **kwargs)
        return login_required(envoltura)
    return decorador


NO_COMPLETA = "Tu rol (Invitado) solo permite ver los partes, no completarlos."


def completa_partes(vista):
    """Pantallas de Completar (lugar, damnificados, incendio, negativas, fotos): cualquier integrante
    logueado menos el Invitado (roles.puede_completar)."""
    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        if not puede_completar(request.user):
            messages.error(request, NO_COMPLETA)
            return redirect("detalle_parte", pk=kwargs["pk"])
        return vista(request, *args, **kwargs)
    return login_required(envoltura)


@requiere_permiso("partes.add_partesiniestro",
                  "Tu rol no permite crear partes. Lo hace el personal de guardia.")
def crear_parte(request):
    anio = timezone.localdate().year
    if Periodo.esta_cerrado(anio):
        messages.error(request, f"El período {anio} está cerrado: no se cargan partes nuevos.")
        return redirect("menu")
    if request.method == "POST":
        form = ParteSiniestroForm(request.POST)
        if form.is_valid():
            parte = form.save(commit=False)

            # numero_correlativo y anio los asigna ParteSiniestro.save().
            estado_inicial, _ = EstadoParte.objects.get_or_create(nombre_estado="Iniciado")
            parte.estado_parte = estado_inicial

            # El guardia NO se tipea: es el usuario logueado ahora mismo.
            try:
                parte.guardia = request.user.integrante
            except Integrante.DoesNotExist:
                messages.error(
                    request,
                    "Tu usuario todavía no está vinculado a ningún integrante. "
                    "Pedile a un administrador que te asocie desde el panel admin."
                )
                return render(request, "partes/crear_parte.html", _contexto_alta(request, form))

            parte.save()
            messages.success(
                request,
                f"Parte {parte.numero_correlativo}/{parte.anio} creado correctamente "
                f"— dirección detectada: {parte.ubicacion}"
            )
            return redirect("detalle_parte", pk=parte.pk)
        log.warning("Alta de parte rechazada: %s", "; ".join(
            f"{campo}: {' '.join(errores)}" for campo, errores in form.errors.items()))
    else:
        form = ParteSiniestroForm()

    return render(request, "partes/crear_parte.html", _contexto_alta(request, form))


def _contexto_alta(request, form):
    # El guardia se muestra como en la grilla de personal: N° de mérito del año y nombre.
    guardia = getattr(request.user, "integrante", None)
    merito = guardia and (guardia.numeros_merito.filter(anio=timezone.localdate().year)
                          .values_list("numero_merito", flat=True).first())
    # Calle, altura y barrio de partes anteriores: el barrio aparece al instante si ya
    # hubo un parte cerca, mientras el mapa lo confirma.
    historial = [[calle, int(numero), barrio] for calle, numero, barrio in
                 ParteSiniestro.objects.exclude(barrio="").filter(ubicacion_numero__regex=r"^\d{1,6}$")
                 .order_by("-pk").values_list("ubicacion_calle", "ubicacion_numero", "barrio")[:3000]]
    return {
        "form": form, "guardia": guardia, "merito_guardia": merito,
        "calles": geocodificacion.calles(), "historial_barrios": historial,
        "barrios": Barrio.objects.values_list("nombre", flat=True),
        "tipos": datos_por_tipo(),
    }


# Para autocompletar localidad y provincia de los damnificados.
PROVINCIAS = ["Buenos Aires", "Catamarca", "Chaco", "Chubut", "Ciudad Autónoma de Buenos Aires", "Córdoba",
              "Corrientes", "Entre Ríos", "Formosa", "Jujuy", "La Pampa", "La Rioja", "Mendoza", "Misiones",
              "Neuquén", "Río Negro", "Salta", "San Juan", "San Luis", "Santa Cruz", "Santa Fe",
              "Santiago del Estero", "Tierra del Fuego", "Tucumán"]
LOCALIDADES_CERCANAS = ["Villa María", "Villa Nueva", "Arroyo Cabral", "Ausonia", "Ana Zumarán", "Arroyo Algodón",
                        "Bell Ville", "Chazón", "Etruria", "James Craik", "La Laguna", "La Playosa", "Las Perdices",
                        "Luca", "Pasco", "Sanabria", "Silvio Pellico", "Tío Pujio", "Ticino", "Las Varillas",
                        "Oliva", "Río Tercero", "Hernando", "General Deheza", "Córdoba"]


def _datos_damnificado():
    """Listas para los damnificados: localidades, provincias y médicos."""
    return {
        "localidades": _localidades(), "provincias": PROVINCIAS,
        "medicos": [{"nombre": m.nombre, "matricula": m.matricula} for m in Medico.objects.all()],
    }


def _localidades():
    """Las cercanas y las que ya se anotaron en otros partes, para sugerir al escribir."""
    anotadas = (Damnificado.objects.exclude(localidad="").values_list("localidad", flat=True)
                .distinct().order_by("localidad")[:300])
    return sorted(set(LOCALIDADES_CERCANAS) | set(anotadas))


@login_required
def buscar_direccion(request):
    """Sugerencias para la ubicación del siniestro: calle normalizada, altura y barrio."""
    texto = request.GET.get("q", "").strip()[:150]
    match = REGEX_DIRECCION.match(texto)
    if not match and REGEX_ESQUINA.match(texto):
        esquina = REGEX_ESQUINA.match(texto)
        return JsonResponse({"candidatos": geocodificacion.buscar_esquina(
            esquina.group("calle"), esquina.group("esquina"))})
    calle, numero = (match.group("calle"), match.group("numero")) if match else (texto, "")
    return JsonResponse({"candidatos": geocodificacion.buscar_direccion(calle, numero)})


@login_required
def menu(request):
    """Inicio: saludo y solo lo que le toca hacer a cada rol (cada cosa vive en su sección)."""
    integrante = getattr(request.user, "integrante", None)
    anio = timezone.localdate().year
    usuario = request.user
    pendientes = []
    if puede_ver_cerrados(usuario):
        demorados = len(ParteSiniestro.demorados())
        if demorados:
            pendientes.append({"texto": f"{demorados} parte{'s' if demorados != 1 else ''} sin cerrar hace más de "
                                        f"{ParteSiniestro.HORAS_PARA_CERRAR} horas: hay que atenderlo.", "alerta": True,
                               "url": reverse("listado_partes") + "?estado=demorados", "boton": "Ver cuáles"})
    if usuario.has_perm("partes.cerrar_partesiniestro"):
        abiertos = ParteSiniestro.objects.exclude(estado_parte__nombre_estado="Cerrado").count()
        if abiertos:
            pendientes.append({"texto": f"Hay {abiertos} parte{'s' if abiertos != 1 else ''} sin cerrar.",
                               "url": reverse("listado_partes") + "?estado=abiertos", "boton": "Ver partes"})
    if integrante:
        sin_completar = (ParteSiniestro.objects.exclude(estado_parte__nombre_estado="Cerrado")
                         .filter(personal__integrante=integrante).exclude(envios__usuario=usuario).count())
        if sin_completar and not usuario.has_perm("partes.cerrar_partesiniestro") and puede_completar(usuario):
            pendientes.append({"texto": f"Figurás en {sin_completar} parte{'s' if sin_completar != 1 else ''} "
                                        "que todavía no completaste.",
                               "url": reverse("listado_partes") + "?estado=abiertos", "boton": "Completar"})
    if usuario.has_perm("partes.gestionar_periodo"):
        periodo = Periodo.del_anio(anio)
        if not periodo.cerrado and not NumeroMerito.objects.filter(anio=anio).exists():
            pendientes.append({"texto": f"Todavía no se asignaron los números de mérito {anio}.",
                               "url": reverse("numeros_tomo", args=[anio]), "boton": "Asignar números"})
        anterior = Periodo.objects.filter(anio=anio - 1, estado=Periodo.Estado.ABIERTO).first()
        if anterior:
            pendientes.append({"texto": f"El período {anterior.anio} sigue abierto.",
                               "url": reverse("tomo", args=[anterior.anio]), "boton": "Ver el tomo"})
    return render(request, "partes/menu.html", {
        "nombre": integrante.nombre if integrante else (usuario.first_name or usuario.username),
        "merito": integrante.numero_merito_en(anio) if integrante else None,
        "aviso_sesion": aviso_sesion(request),
        "pendientes": pendientes,
    })


@login_required
def mi_perfil(request):
    """Mi perfil es el mismo perfil que ven los demás (no se repiten los datos en otra pantalla)."""
    try:
        integrante = request.user.integrante
    except Integrante.DoesNotExist:
        return render(request, "partes/mi_perfil.html")
    return _perfil(request, integrante, propio=True)


def _huella(*datos):
    return hashlib.sha256(repr(datos).encode()).hexdigest()[:16]


def _version_parte(parte):
    """Cambia cuando cambia algo del parte: la pantalla abierta en otro equipo se actualiza sola."""
    return _huella(parte.estado_parte_id, parte.hora_salida, parte.hora_llegada, parte.hora_regreso, parte.km_regreso,
                   parte.vehiculo_id, parte.personal.count(), parte.damnificados.count(), parte.envios.count(),
                   parte.fotos.count(), parte.negativas.count())


def _version_listado():
    return _huella(list(ParteSiniestro.objects.values_list("pk", "estado_parte_id", "tipo_id", "motivo_id")[:200]))


@login_required
def version_partes(request):
    if request.GET.get("parte", "").isdigit():
        return JsonResponse({"version": _version_parte(_parte(int(request.GET["parte"])))})
    return JsonResponse({"version": _version_listado()})


def puede_ver_cerrados(usuario):
    """Los partes cerrados los ven el guardia, Estadística y el jefe (los que ven los tomos).
    El bombero, solo los que están en curso (pedido del tutor, 03/11)."""
    return usuario.has_perm("partes.view_periodo")


@login_required
def listado_partes(request):
    partes = ParteSiniestro.objects.select_related("tipo", "motivo", "estado_parte", "guardia")
    filtro = request.GET.get("estado", "")
    ve_cerrados = puede_ver_cerrados(request.user)
    demorados = ParteSiniestro.demorados() if ve_cerrados else []
    if filtro == "abiertos" or filtro == "por_guardia" or not ve_cerrados:
        partes = partes.exclude(estado_parte__nombre_estado="Cerrado")
    if filtro == "por_guardia":
        partes = partes.order_by("guardia__apellido", "guardia__nombre", "-anio", "-numero_correlativo")
    elif filtro == "demorados":
        partes = partes.filter(pk__in=[p.pk for p in demorados])
    elif filtro == "cerrados":
        partes = partes.filter(estado_parte__nombre_estado="Cerrado")
    q = request.GET.get("q", "").strip()
    # Por fecha (pedido del tutor): de entrada, los partes del día; el árbol lleva a otros días, meses y años.
    fecha = request.GET.get("fecha") or request.GET.get("anio", "")
    if not fecha:
        fecha = "todas" if (filtro or q) else timezone.localdate().isoformat()
    seleccion = _fecha_elegida(fecha)
    arbol = _arbol_por_fecha(partes, seleccion)
    if seleccion:
        anio, mes, dia = seleccion
        partes = partes.filter(fecha__year=anio)
        if mes:
            partes = partes.filter(fecha__month=mes)
        if dia:
            partes = partes.filter(fecha__day=dia)
    if q:
        numero = q.split("/")[0]
        busqueda = (Q(ubicacion_calle__icontains=q) | Q(barrio__icontains=q)
                    | Q(tipo__descripcion__icontains=q) | Q(motivo__descripcion__icontains=q))
        if numero.isdigit():
            busqueda |= Q(numero_correlativo=int(numero))
        partes = partes.filter(busqueda)
    return render(request, "partes/listado_partes.html",
                  {"partes": partes[:200], "filtro": filtro, "q": q, "ve_cerrados": ve_cerrados,
                   "fecha": fecha, "titulo_fecha": _titulo_fecha(seleccion), "arbol": arbol,
                   **_carpetas(arbol, seleccion),
                   "es_hoy": fecha == timezone.localdate().isoformat(),
                   "demorados": len(demorados), "version": _version_listado()})


MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]


def _fecha_elegida(texto):
    """'2026' / '2026-11' / '2026-11-05' -> (anio, mes, dia), con None en lo que no se eligió.
    'todas' o algo que no se entiende -> None (todas las fechas)."""
    partes = texto.split("-")
    if not 1 <= len(partes) <= 3 or not all(p.isdigit() for p in partes):
        return None
    numeros = [int(p) for p in partes] + [None] * (3 - len(partes))
    try:
        date(numeros[0], numeros[1] or 1, numeros[2] or 1)
    except ValueError:
        return None
    return tuple(numeros)


def _titulo_fecha(seleccion):
    if not seleccion:
        return "Todas las fechas"
    anio, mes, dia = seleccion
    if dia:
        texto = f"{dia:02d}/{mes:02d}/{anio}"
        return f"Hoy, {texto}" if date(anio, mes, dia) == timezone.localdate() else texto
    return f"{MESES[mes - 1].capitalize()} {anio}" if mes else str(anio)


def _carpetas(arbol, seleccion):
    """Recuadros para navegar por fecha, como carpetas: todos los años > los meses de un año > los días de
    un mes (con un día elegido se siguen viendo los otros días de ese mes). Arriba, el camino."""
    anio, mes, dia = seleccion or (None, None, None)
    migas = [("Todas las fechas", "todas")]
    carpetas = [{"clave": str(a["anio"]), "titulo": str(a["anio"]), "sub": "", "n": a["n"]} for a in arbol]
    por_anio = {a["anio"]: a for a in arbol}
    if anio in por_anio:
        migas.append((str(anio), str(anio)))
        meses = por_anio[anio]["meses"]
        carpetas = [{"clave": m["clave"], "titulo": m["nombre"], "sub": str(anio), "n": m["n"]} for m in meses]
        elegido = next((m for m in meses if m["clave"] == f"{anio}-{(mes or 0):02d}"), None)
        if elegido:
            migas.append((elegido["nombre"], elegido["clave"]))
            carpetas = [{"clave": d["clave"], "titulo": d["dia"].strftime("%d/%m"),
                         "sub": ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"][d["dia"].weekday()],
                         "n": d["n"]} for d in elegido["dias"]]
    return {"carpetas": carpetas, "migas": migas}


def _arbol_por_fecha(partes, seleccion):
    """Años > meses > días con la cantidad de partes, del más reciente al más viejo. Queda abierto lo
    elegido (o el año y mes de hoy)."""
    hoy = timezone.localdate()
    anio_abierto, mes_abierto = (seleccion[0], seleccion[1]) if seleccion else (hoy.year, hoy.month)
    conteo = partes.order_by().values("fecha").annotate(n=Count("id"))
    por_anio = {}
    for fila in sorted(conteo, key=lambda f: f["fecha"], reverse=True):
        dia = fila["fecha"]
        anio = por_anio.setdefault(dia.year, {"anio": dia.year, "n": 0, "meses": {}})
        mes = anio["meses"].setdefault(dia.month, {"clave": f"{dia.year}-{dia.month:02d}", "nombre": MESES[dia.month - 1].capitalize(),
                                                    "n": 0, "dias": []})
        anio["n"] += fila["n"]
        mes["n"] += fila["n"]
        mes["dias"].append({"clave": dia.isoformat(), "dia": dia, "n": fila["n"]})
    arbol = []
    for anio in por_anio.values():
        anio["abierto"] = anio["anio"] == anio_abierto
        anio["meses"] = list(anio["meses"].values())
        for mes in anio["meses"]:
            mes["abierto"] = anio["abierto"] and mes["clave"] == f"{anio['anio']}-{(mes_abierto or 0):02d}"
        arbol.append(anio)
    return arbol


def _parte(pk):
    return get_object_or_404(
        ParteSiniestro.objects.select_related(
            "tipo", "motivo", "estado_parte", "guardia", "autorizo", "cerro", "vehiculo",
            "personal_e100"),
        pk=pk)


def _contenido_parte(parte):
    """Lo que se muestra de un parte: lo usan el detalle y la hoja para imprimir (PDF)."""
    meritos = dict(NumeroMerito.objects.filter(anio=parte.anio)
                   .values_list("integrante_id", "numero_merito"))
    personal = [(p, meritos.get(p.integrante_id))
                for p in parte.personal.select_related("integrante__rango", "rango")]
    personal.sort(key=lambda par: (par[1] is None, par[1] or 0))
    return {
        "parte": parte,
        "merito_guardia": meritos.get(parte.guardia_id),
        "personal": personal,
        "rodados": parte.vehiculos_siniestrados.prefetch_related("damnificados__hacia"),
        "sin_rodado": parte.damnificados.filter(vehiculo_siniestrado=None).select_related("hacia"),
        # Como en Completar: las secciones del tipo de siniestro y las que ya tienen datos.
        "secciones": set(parte.tipo.lista_secciones()) | set(_secciones_con_datos(parte)),
        "entidades": parte.entidades.select_related("tipo_entidad"),
        "incendio": IncendioDato.objects.filter(parte=parte).first(),
        "negativas": parte.negativas.all(),
        "firma_cierre": Firma.de(parte, Firma.Accion.CIERRE_PARTE).first(),
        "correcciones": [
            {"firma": f, "motivo": f.datos.get("motivo", ""),
             "cambios": [(ParteSiniestro.NOMBRES_EN_FIRMA.get(clave, clave), _texto_firma(antes), _texto_firma(despues))
                         for clave, (antes, despues) in f.datos.get("cambios", {}).items()]}
            for f in Firma.de(parte, Firma.Accion.CORRECCION_PARTE).order_by("pk")],
    }


def _texto_firma(valor):
    if isinstance(valor, list):
        return ", ".join(str(v) if v else "—" for v in valor) or "—"
    return str(valor) if valor not in (None, "") else "—"


@login_required
def detalle_parte(request, pk):
    parte = _parte(pk)
    if parte.esta_cerrado and not puede_ver_cerrados(request.user):
        messages.error(request, CERRADO_NO_SE_VE.format(numero=parte.numero_correlativo, anio=parte.anio))
        return redirect("listado_partes")
    contenido = _contenido_parte(parte)
    personal = contenido["personal"]
    corrige = puede_corregir(request.user)
    completa = puede_completar(request.user)
    salida = None
    if corrige and not parte.esta_cerrado and not parte.salida_resuelta:
        # Antes de marcar la salida, el guardia anota qué unidad sale, quién autorizó y quiénes van.
        salida = {"form": SalidaForm(instance=parte, prefix="salida"),
                  "personal": PersonalConcurrioForm(parte=parte, prefix="personal")}
    return render(request, "partes/detalle_parte.html", {
        **contenido,
        "salida": salida,
        # Desde el detalle se puede ir a cargar rodados y damnificados (quien ya envió, no).
        "url_cargar": (reverse("corregir_parte", args=[pk]) if corrige
                       else None if ya_completo(parte, request.user) or not completa
                       else reverse("completar_parte", args=[pk])),
        "completa": completa,
        "fotos": parte.fotos.order_by("fecha_captura_local"),
        "problemas_cierre": parte.problemas_para_cerrar(),
        "marcas": [("salida", "Salida", parte.hora_salida), ("llegada", "Llegada", parte.hora_llegada),
                   ("regreso", "Regreso", parte.hora_regreso)],
        "descargas": parte.descargas.filter(activa=True).select_related("usuario"),
        "corrige": corrige,
        "ya_completo": ya_completo(parte, request.user),
        "envios_con_avisos": parte.envios.exclude(avisos="").select_related("usuario"),
        "version": _version_parte(parte),
    })


@requiere_permiso("partes.view_periodo", "Tu rol no permite ver los períodos.")
def imprimir_parte(request, pk):
    """Hoja del parte para imprimir o guardar como PDF desde el navegador. Se abre desde el tomo."""
    return render(request, "partes/imprimir_partes.html", {
        "hojas": [_contenido_parte(_parte(pk))], "titulo": None})


def _texto_constancia(parte):
    """El cuerpo de la constancia, como las que hace el cuartel (Ceballos, Soria, Medina): se puede editar antes de imprimir."""
    fecha = f"{parte.fecha.day:02d} de {MESES[parte.fecha.month - 1]} del año {parte.fecha.year}"
    hora = parte.hora_llegada or parte.hora_salida or parte.hora_llamada
    efectivos = parte.personal.count()
    texto = (f"El día {fecha}, siendo las {hora:%H:%M} hs., se concurre con una Unidad de Bomberos"
             + (f" con {efectivos} efectivo{'s' if efectivos != 1 else ''}" if efectivos else "")
             + f" a {parte.ubicacion}" + (f", barrio {parte.barrio}" if parte.barrio else "")
             + f", de nuestra ciudad, por {parte.motivo.descripcion.lower()}")
    asistidos = []
    for d in parte.damnificados.all():
        persona = d.apellido_nombre or "una persona"
        if d.edad:
            persona += f" de {d.edad} años de edad"
        if d.dni:
            persona += f", DNI {d.dni}"
        if d.lesion:
            persona += f", que presentaba {d.lesion.lower()}"
        if d.trasladado and d.destino:
            persona += f", la cual es trasladada hacia {d.destino} para su atención"
        asistidos.append(persona)
    if asistidos:
        texto += ", donde se asiste a " + "; a ".join(asistidos)
    return texto + "."


@requiere_permiso("partes.emitir_constancia", "Tu rol no permite emitir constancias.")
def constancia_intervencion(request, pk):
    """Constancia (o acta) de intervención: lo que se entrega a quien necesita demostrar que bomberos acudió,
    por ejemplo ante un seguro. Se imprime con membrete y la firma el 1° Jefe. Solo de partes cerrados
    (10/11): lo que certifica ya no cambia; la emiten el Jefe y Estadística."""
    parte = _parte(pk)
    if not parte.esta_cerrado:
        messages.error(request, "La constancia de intervención se emite cuando el parte está cerrado.")
        return redirect("detalle_parte", pk=pk)
    hoy = timezone.localdate()
    jefe = Integrante.objects.filter(cargo="1° Jefe").select_related("rango").first()
    return render(request, "partes/constancia_intervencion.html", {
        "parte": parte, "texto": _texto_constancia(parte), "jefe": jefe,
        "fecha_emision": f"{hoy.day:02d} de {MESES[hoy.month - 1]} de {hoy.year}",
    })


@requiere_permiso("partes.view_periodo", "Tu rol no permite ver los períodos.")
def imprimir_partes_del_anio(request, anio):
    """Todos los partes del tomo, uno por hoja, para guardar el año entero en un PDF."""
    partes = (ParteSiniestro.objects.filter(anio=anio)
              .select_related("tipo", "motivo", "guardia", "vehiculo", "estado_parte", "personal_e100",
                              "autorizo", "cerro")
              .order_by("numero_correlativo"))
    return render(request, "partes/imprimir_partes.html", {
        "hojas": [_contenido_parte(p) for p in partes], "titulo": f"Partes de siniestro {anio}", "anio": anio})


def puede_corregir(usuario):
    """Guardia y jefe: corrigen el parte las veces que haga falta mientras esté abierto.
    El resto del personal completa una sola vez. Cerrado, nadie lo modifica: solo se ve."""
    return usuario.has_perm("partes.cerrar_partesiniestro")


def ya_completo(parte, usuario):
    return parte.envios.filter(usuario=usuario).exists()


def _formularios_completar(parte, datos, usuario, corregir=False):
    form = CompletarParteForm(datos, instance=parte,
                              puede_confidencial=usuario.has_perm("partes.ver_confidencial"),
                              es_guardia=puede_corregir(usuario), corregir=corregir)
    incendio = IncendioDato.objects.filter(parte=parte).first() or IncendioDato(parte=parte)
    return form, {
        "rodados": VehiculoSiniestradoFormSet(datos, instance=parte, prefix="rodados"),
        "damnificados": DamnificadosParteFormSet(datos, parte=parte, prefix="damnificados"),
        "incendio": IncendioDatoForm(datos, instance=incendio, prefix="incendio"),
        "entidades": EntidadesForm(datos, parte=parte, prefix="entidades"),
        "personal": PersonalConcurrioForm(datos, parte=parte, prefix="personal",
                                          con_cobro=puede_corregir(usuario) and parte.tipo.se_cobra),
    }


def _guardar_completar(request, parte, corregir=False):
    """Valida y guarda un envío de Completar (desde la pantalla o desde un celular).

    Devuelve (resultado, form, secciones, avisos) con resultado "guardado",
    "repetido", "ya_completo" o "errores". Si el navegador mandó cómo estaba el parte
    al abrirlo (_base), se combina con lo que hay ahora para no pisar lo de otros equipos."""
    envio = request.POST.get("_envio", "")
    if offline.es_uuid(envio) and EnvioCompletar.objects.filter(pk=envio).exists():
        return "repetido", None, None, []
    if not puede_corregir(request.user) and ya_completo(parte, request.user):
        return "ya_completo", None, None, []
    if not offline.es_uuid(envio):
        envio = str(uuid.uuid4())  # cada guardado queda registrado a nombre de quien lo hizo
    # "Corregir datos" (llamado y horas) lo manda la pantalla, también desde el celular sin señal.
    corregir = (corregir or request.POST.get("_modo") == "corregir") and puede_corregir(request.user)

    datos, uuids, avisos = request.POST, {}, []
    base = offline.leer_base(request.POST.get("_base"))
    if base is not None:
        actual, secciones_actuales = _formularios_completar(parte, None, request.user, corregir)
        datos, uuids, avisos = offline.combinar(
            request.POST, base,
            [actual, secciones_actuales["incendio"], secciones_actuales["entidades"],
             secciones_actuales["personal"]],
            [(secciones_actuales["rodados"], lambda f: f"Rodado N° {f.instance.rodado_numero} - "),
             (secciones_actuales["damnificados"],
              lambda f: f"Damnificado {f.instance.apellido_nombre or 'sin nombre'} - ")])
        offline.renumerar_rodados(datos, avisos, maximo=VehiculoSiniestrado.MAXIMO)
        datos["_base"] = request.POST["_base"]  # si hay errores, la pantalla lo conserva

    form, secciones = _formularios_completar(parte, datos, request.user, corregir)
    if not (form.is_valid() and all(s.is_valid() for s in secciones.values())):
        log.warning("Completar del parte %s rechazado: %s", parte.pk,
                    " | ".join(_errores_completar(form, secciones)))
        return "errores", form, secciones, avisos

    # Las filas nuevas se guardan con el UUID que generó el celular.
    for prefijo, nuevos in uuids.items():
        for indice, valor in nuevos.items():
            secciones[prefijo].forms[indice].instance.pk = valor
    with transaction.atomic():
        parte = form.save(commit=False)
        if parte.estado_parte.nombre_estado == "Iniciado":
            parte.estado_parte, _ = EstadoParte.objects.get_or_create(nombre_estado="En curso")
        parte.save()
        # Primero los rodados: los damnificados se enganchan por Rodado N°.
        secciones["rodados"].save()
        secciones["damnificados"].save()
        if secciones["incendio"].tiene_datos():
            secciones["incendio"].save()
        secciones["entidades"].save()
        secciones["personal"].save()
        EnvioCompletar.objects.create(id=envio, parte=parte, usuario=request.user,
                                      avisos="\n".join(avisos))
    return "guardado", form, secciones, avisos


TOMO_CERRADO = "El período {anio} está cerrado: sus partes ya no se modifican."
CERRADO_NO_SE_VE = "El parte {numero}/{anio} está cerrado: solo lo ven el guardia, Estadística y el jefe."
YA_COMPLETO = "Ya enviaste los datos de este parte. Si hay que corregir algo, pedíselo al guardia."


@completa_partes
def completar_parte(request, pk, corregir=False):
    parte = _parte(pk)
    corrige = puede_corregir(request.user)
    if corregir and not corrige:
        messages.error(request, "Solo el guardia puede corregir los datos del parte.")
        return redirect("detalle_parte", pk=pk)
    if Periodo.esta_cerrado(parte.anio):
        messages.error(request, TOMO_CERRADO.format(anio=parte.anio))
        return redirect("detalle_parte", pk=pk)
    corrige_cerrado = parte.esta_cerrado and corregir and request.user.has_perm("partes.corregir_cerrado")
    if parte.esta_cerrado and not corrige_cerrado:
        messages.error(request, "El parte está cerrado: ya no se modifica, solo se puede ver.")
        return redirect("detalle_parte", pk=pk)
    correccion = CorreccionCerradoForm(request.POST or None, usuario=request.user) if corrige_cerrado else None
    if request.method == "GET" and not corrige and ya_completo(parte, request.user):
        messages.info(request, YA_COMPLETO)
        return redirect("detalle_parte", pk=pk)

    if request.method == "POST" and correccion is not None:
        if not correccion.is_valid():
            messages.error(request, "No se guardó: falta el motivo de la corrección o la contraseña no es correcta.")
            form, secciones = _formularios_completar(parte, request.POST, request.user, corregir)
            return _pantalla_completar(request, parte, form, secciones, corregir, correccion)
        with transaction.atomic():
            antes = parte.datos_para_firma()
            resultado, form, secciones, avisos = _guardar_completar(request, parte, corregir)
            if resultado == "guardado":
                parte = _parte(pk)
                cambios = {clave: [valor, despues] for (clave, valor), despues
                           in zip(antes.items(), parte.datos_para_firma().values()) if valor != despues}
                if not cambios:
                    messages.info(request, "No había nada distinto: no se registró ninguna corrección.")
                    return redirect("detalle_parte", pk=pk)
                firma = Firma.sellar(request.user, Firma.Accion.CORRECCION_PARTE, parte, {
                    "motivo": correccion.cleaned_data["motivo_correccion"], "cambios": cambios})
                messages.success(request, f"Corrección del parte cerrado registrada. Sello {firma.codigo}.")
                return redirect("detalle_parte", pk=pk)
        if resultado != "errores":
            return redirect("detalle_parte", pk=pk)
        messages.error(request, "No se guardó. Revisá: " + "; ".join(_errores_completar(form, secciones)[:3]) + ".")
        return _pantalla_completar(request, parte, form, secciones, corregir, correccion)

    if request.method == "POST":
        resultado, form, secciones, avisos = _guardar_completar(request, parte, corregir)
        if resultado == "ya_completo":
            messages.error(request, YA_COMPLETO)
            return redirect("detalle_parte", pk=pk)
        if resultado != "errores":
            if resultado == "repetido":
                messages.info(request, "Esos datos ya se habían guardado.")
            else:
                messages.success(request, "Datos del parte guardados.")
            for aviso in avisos:
                messages.warning(request, aviso)
            # Guardar: al detalle, para ver cómo quedó. Guardar y volver: a la lista de partes.
            destino = (reverse("listado_partes") if "seguir" in request.POST
                       else reverse("detalle_parte", args=[pk]))
            envio = request.POST.get("_envio", "")
            # ?guardado= le avisa al navegador que puede borrar su copia de este guardado.
            return redirect(f"{destino}?guardado={envio}" if offline.es_uuid(envio) else destino)
        errores = _errores_completar(form, secciones)
        messages.error(request, "No se guardó. Revisá: " + "; ".join(errores[:3])
                       + (f" (y {len(errores) - 3} más)." if len(errores) > 3 else "."))
    else:
        form, secciones = _formularios_completar(parte, None, request.user, corregir)
    return _pantalla_completar(request, parte, form, secciones, corregir, correccion)


def _pantalla_completar(request, parte, form, secciones, corregir, correccion=None):
    corrige = puede_corregir(request.user)
    # El km de regreso tiene que superar al último anotado para el móvil antes de este parte.
    km_moviles = {str(v.pk): v.km_anterior(parte.fecha, excluir=parte)
                  for v in Vehiculo.objects.filter(dado_de_baja=False)}
    return render(request, "partes/completar_parte.html", {
        "parte": parte, "form": form, "km_moviles": km_moviles,
        "calles": geocodificacion.calles(),
        "barrios": Barrio.objects.values_list("nombre", flat=True),
        "base_original": request.POST.get("_base", ""),
        "envio_rechazado": request.POST.get("_envio", ""),
        "pagina": uuid.uuid4().hex,
        "corrige": corrige, "corregir": corregir, "correccion": correccion,
        "tipos": datos_por_tipo(),
        "con_datos": _secciones_con_datos(parte),
        "maximo_rodados": VehiculoSiniestrado.MAXIMO,
        **_datos_damnificado(),
        **secciones,
    })


def datos_por_tipo():
    """Para la pantalla: qué secciones y qué motivos corresponden a cada tipo de siniestro, y el
    texto de ayuda (Motivo.detalle) de cada motivo, que se muestra abajo del desplegable.

    Los motivos apagados (los "Reserva" de la planilla) no se ofrecen, pero sí van sus detalles:
    un parte viejo puede tener uno guardado y su ayuda se sigue mostrando."""
    generales = list(Motivo.objects.filter(tipos=None, activo=True).values_list("pk", flat=True))
    tipos = {}
    for tipo in TipoSiniestro.objects.prefetch_related("motivos"):
        propios = [m.pk for m in tipo.motivos.all()]
        activos = [m.pk for m in tipo.motivos.all() if m.activo]
        tipos[str(tipo.pk)] = {
            "secciones": tipo.lista_secciones(),
            # Un tipo sin motivos propios ofrece todos; si los tiene, solo los que estén activos.
            "motivos": activos + generales if propios else None,
        }
    detalles = {str(pk): detalle
                for pk, detalle in Motivo.objects.exclude(detalle="").values_list("pk", "detalle")}
    return {"tipos": tipos, "detalles": detalles}


def _secciones_con_datos(parte):
    """Las secciones que ya tienen algo cargado se muestran aunque el tipo no las pida."""
    con_datos = []
    if parte.vehiculos_siniestrados.exists():
        con_datos.append("rodados")
    if parte.damnificados.exists():
        con_datos.append("damnificados")
    if IncendioDato.objects.filter(parte=parte).exists():
        con_datos.append("incendio")
    return con_datos


def _errores_completar(form, secciones):
    """Los errores de validación en texto, para mostrarlos en el celular."""
    errores = []
    for formulario in [form, secciones["incendio"], secciones["entidades"], secciones["personal"]]:
        for nombre, lista in formulario.errors.items():
            campo = formulario.fields.get(nombre)
            errores += [f"{campo.label}: {e}" if campo else e for e in lista]
    for prefijo, titulo in (("rodados", "Rodado"), ("damnificados", "Damnificado")):
        formset = secciones[prefijo]
        errores += list(formset.non_form_errors())
        for numero, (fila, errores_fila) in enumerate(zip(formset.forms, formset.errors), start=1):
            for nombre, lista in errores_fila.items():
                campo = fila.fields.get(nombre)
                errores += [f"{titulo} {numero} - {campo.label}: {e}" if campo else e for e in lista]
    return errores


def _json_sin_sesion(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Iniciá sesión para enviar lo guardado en el celular."},
                            status=401)
    if not puede_completar(request.user):
        return JsonResponse({"error": NO_COMPLETA}, status=403)
    return None


@require_POST
def sincronizar_parte(request, pk):
    """Recibe un guardado de Completar hecho sin señal. Responde en JSON."""
    sin_sesion = _json_sin_sesion(request)
    if sin_sesion:
        return sin_sesion
    parte = _parte(pk)
    if Periodo.esta_cerrado(parte.anio):
        return JsonResponse({"error": TOMO_CERRADO.format(anio=parte.anio)}, status=409)
    if parte.esta_cerrado:
        return JsonResponse({"error": f"El parte {parte.numero_correlativo}/{parte.anio} ya está "
                                      "cerrado: lo guardado en el celular no se puede agregar."},
                            status=409)
    resultado, form, secciones, avisos = _guardar_completar(request, parte)
    if resultado == "ya_completo":
        return JsonResponse({"error": YA_COMPLETO}, status=409)
    if resultado == "errores":
        return JsonResponse({"error": "El servidor rechazó los datos guardados en el celular.",
                             "errores": _errores_completar(form, secciones)}, status=400)
    DescargaOffline.objects.filter(parte=parte, usuario=request.user).update(
        ultima_sincronizacion=timezone.now())
    return JsonResponse({"ok": True, "avisos": avisos})


@require_POST
def sincronizar_foto(request, pk):
    """Recibe una foto sacada sin señal. El id lo genera el celular: reenviar no la duplica."""
    sin_sesion = _json_sin_sesion(request)
    if sin_sesion:
        return sin_sesion
    parte = _parte(pk)
    foto_id = request.POST.get("id", "")
    if not offline.es_uuid(foto_id):
        return JsonResponse({"error": "Foto sin identificador."}, status=400)
    if ParteFoto.objects.filter(pk=foto_id).exists():
        return JsonResponse({"ok": True})
    if parte.esta_cerrado:
        return JsonResponse({"error": f"El parte {parte.numero_correlativo}/{parte.anio} ya está "
                                      "cerrado: la foto no se puede agregar."}, status=409)
    form = FotosForm(request.POST, request.FILES)
    if not form.is_valid():
        return JsonResponse({"error": " ".join(e for lista in form.errors.values() for e in lista)},
                            status=400)
    fecha = parse_datetime(request.POST.get("fecha_captura_local", "")) or timezone.now()
    if timezone.is_naive(fecha):
        fecha = timezone.make_aware(fecha)
    ParteFoto.objects.create(id=foto_id, parte=parte, archivo=form.cleaned_data["archivos"][0],
                             descripcion=form.cleaned_data["descripcion"],
                             fecha_captura_local=fecha, sincronizado=True)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def descargar_parte(request, pk):
    """Anota que el parte se lleva al celular (o que se quitó) y dice qué pantallas guardar."""
    parte = _parte(pk)
    if not puede_completar(request.user):
        return JsonResponse({"error": NO_COMPLETA}, status=403)
    if request.POST.get("quitar"):
        DescargaOffline.objects.filter(parte=parte, usuario=request.user).update(activa=False)
        return JsonResponse({"ok": True})
    if parte.esta_cerrado:
        return JsonResponse({"error": "El parte está cerrado."}, status=409)
    DescargaOffline.objects.update_or_create(
        parte=parte, usuario=request.user,
        defaults={"activa": True, "fecha_descarga": timezone.now()})
    urls = [reverse("detalle_parte", args=[pk]), reverse("completar_parte", args=[pk])]
    if puede_corregir(request.user):
        urls.append(reverse("corregir_parte", args=[pk]))
    return JsonResponse({"ok": True, "urls": urls})


def sin_conexion(request):
    """Pantalla que muestra el celular cuando no hay señal y la página no está descargada."""
    return render(request, "partes/sin_conexion.html")


def service_worker(request):
    respuesta = render(request, "partes/sw.js", content_type="application/javascript")
    respuesta["Cache-Control"] = "no-cache"
    return respuesta


@completa_partes
def damnificados(request, pk, rodado_id):
    parte = _parte(pk)
    rodado = get_object_or_404(VehiculoSiniestrado, pk=rodado_id, parte=parte)
    if parte.esta_cerrado:
        messages.error(request, "El parte está cerrado: ya no se puede modificar.")
        return redirect("detalle_parte", pk=pk)

    formset = DamnificadoFormSet(request.POST or None, instance=rodado, prefix="damnificados")
    if request.method == "POST":
        if formset.is_valid():
            formset.save()
            messages.success(request, f"Damnificados del rodado {rodado.rodado_numero} guardados.")
            if "seguir" in request.POST:
                return redirect("damnificados", pk=pk, rodado_id=rodado_id)
            return redirect("detalle_parte", pk=pk)
        messages.error(request, "Hay errores en el formulario: revisá los campos marcados.")

    return render(request, "partes/damnificados.html",
                  {"parte": parte, "rodado": rodado, "formset": formset, **_datos_damnificado()})


@requiere_permiso("partes.cerrar_partesiniestro", "Tu rol no permite cerrar partes.")
@require_POST
def cerrar_parte(request, pk):
    parte = _parte(pk)
    try:
        integrante = request.user.integrante
    except Integrante.DoesNotExist:
        messages.error(request, "Tu usuario no está vinculado a ningún integrante.")
        return redirect("detalle_parte", pk=pk)
    firma = FirmarForm(request.POST, usuario=request.user)
    if not firma.is_valid():
        messages.error(request, " ".join(firma.errors.get("clave", ["Falta la contraseña para firmar."])))
        return redirect("detalle_parte", pk=pk)
    try:
        parte.cerrar(integrante, usuario=request.user)
    except ValidationError as error:
        for problema in error.messages:
            messages.error(request, problema)
    else:
        sello = Firma.de(parte, Firma.Accion.CIERRE_PARTE).first()
        messages.success(request, f"Parte {parte.numero_correlativo}/{parte.anio} cerrado y firmado. "
                                  f"Sello {sello.codigo}.")
    return redirect("detalle_parte", pk=pk)


HORAS_DE_SALIDA = {"salida": "hora_salida", "llegada": "hora_llegada", "regreso": "hora_regreso"}


@requiere_permiso("partes.cerrar_partesiniestro", "Los horarios de la salida los marca el guardia.")
@require_POST
def marcar_hora(request, pk, momento):
    """Botones "Salida", "Llegada" y "Regreso" del detalle: un toque anota la hora actual.
    No pisa una hora ya anotada (para corregirla está Corregir datos).

    La salida lleva antes la unidad, quién la autorizó y el personal que va (pedido del tutor: la
    unidad no sale sin autorización y el guardia sabe cuál sale). Con "guardar" se anotan esos
    datos sin marcar todavía la hora."""
    parte, cerrado = _parte_editable(request, pk)
    if cerrado:
        return cerrado
    campo = HORAS_DE_SALIDA.get(momento)
    if campo is None:
        return redirect("detalle_parte", pk=pk)
    if momento != "salida" and not parte.hora_salida:
        messages.error(request, "Primero hay que marcar la salida de la unidad.")
        return redirect("detalle_parte", pk=pk)
    if getattr(parte, campo):
        messages.info(request, f"La hora de {momento} ya estaba anotada.")
        return redirect("detalle_parte", pk=pk)
    solo_guardar = "guardar" in request.POST
    cambios = [campo, "unidad_salio", "estado_parte"]
    with transaction.atomic():
        if momento == "salida" and "salida-vehiculo" in request.POST:
            form = SalidaForm(request.POST, instance=parte, prefix="salida")
            personal = PersonalConcurrioForm(request.POST, parte=parte, prefix="personal")
            if solo_guardar:
                for nombre in form.fields.values():
                    nombre.required = False
            if not (form.is_valid() and personal.is_valid()):
                for errores in form.errors.values():
                    for error in errores:
                        messages.error(request, error)
                return redirect("detalle_parte", pk=pk)
            parte.vehiculo, parte.autorizo = form.cleaned_data["vehiculo"], form.cleaned_data["autorizo"]
            personal.save()
            cambios += ["vehiculo", "autorizo"]
        elif momento == "salida" and not (parte.vehiculo_id and parte.autorizo_id):
            messages.error(request, "Antes de marcar la salida, elegí la unidad y quién la autorizó.")
            return redirect("detalle_parte", pk=pk)
        if not solo_guardar:
            setattr(parte, campo, timezone.localtime().time().replace(second=0, microsecond=0))
            parte.unidad_salio = True
        if parte.estado_parte.nombre_estado == "Iniciado":
            parte.estado_parte, _ = EstadoParte.objects.get_or_create(nombre_estado="En curso")
        parte.save(update_fields=cambios)
    if solo_guardar:
        messages.success(request, "Unidad, autorización y personal guardados. Falta marcar la salida.")
    else:
        messages.success(request, f"Hora de {momento}: {getattr(parte, campo):%H:%M}.")
    return redirect("detalle_parte", pk=pk)


def _parte_editable(request, pk):
    """Devuelve (parte, None) o (None, redirect) si el parte está cerrado."""
    parte = _parte(pk)
    if parte.esta_cerrado or Periodo.esta_cerrado(parte.anio):
        messages.error(request, "El parte está cerrado: ya no se puede modificar.")
        return None, redirect("detalle_parte", pk=pk)
    return parte, None


@completa_partes
def incendio(request, pk):
    parte, cerrado = _parte_editable(request, pk)
    if cerrado:
        return cerrado
    instancia = IncendioDato.objects.filter(parte=parte).first() or IncendioDato(parte=parte)
    form = IncendioDatoForm(request.POST or None, instance=instancia)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Datos del incendio guardados.")
        return redirect("detalle_parte", pk=pk)
    return render(request, "partes/form_simple.html", {
        "parte": parte, "form": form, "titulo": "Datos del incendio"})


@completa_partes
def nueva_negativa(request, pk):
    parte, cerrado = _parte_editable(request, pk)
    if cerrado:
        return cerrado
    inicial = {}
    integrante = getattr(request.user, "integrante", None)
    if integrante:
        inicial = {"atendio": integrante, "matricula_atendio": integrante.matricula_profesional}
    try:
        damnificado = Damnificado.objects.filter(
            pk=request.GET["damnificado"], parte=parte).first()
    except (KeyError, ValidationError):
        damnificado = None
    if damnificado:
        inicial.update({"damnificado": damnificado, "nombre_firmante": damnificado.apellido_nombre,
                        "dni_firmante": damnificado.dni, "edad_firmante": damnificado.edad,
                        "domicilio_firmante": damnificado.domicilio})
    form = NegativaAtencionForm(request.POST or None, initial=inicial, parte=parte)
    if request.method == "POST" and form.is_valid():
        negativa = form.save(commit=False)
        negativa.parte = parte
        negativa.save()
        messages.success(request, "Negativa registrada. Imprimila para que la firmen.")
        return redirect("constancia_negativa", negativa_id=negativa.pk)
    return render(request, "partes/form_simple.html", {
        "parte": parte, "form": form, "titulo": "Negativa a la atención / traslado"})


@login_required
def constancia_negativa(request, negativa_id):
    negativa = get_object_or_404(
        NegativaAtencion.objects.select_related("parte", "atendio", "damnificado"), pk=negativa_id)
    if negativa.parte and negativa.parte.esta_cerrado and not puede_ver_cerrados(request.user):
        messages.error(request, CERRADO_NO_SE_VE.format(numero=negativa.parte.numero_correlativo,
                                                        anio=negativa.parte.anio))
        return redirect("listado_partes")
    return render(request, "partes/constancia_negativa.html", {"n": negativa})


@completa_partes
def fotos(request, pk):
    parte, cerrado = _parte_editable(request, pk)
    if cerrado:
        return cerrado
    form = FotosForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        for archivo in form.cleaned_data["archivos"]:
            ParteFoto.objects.create(parte=parte, archivo=archivo,
                                     descripcion=form.cleaned_data["descripcion"], sincronizado=True)
        cantidad = len(form.cleaned_data["archivos"])
        messages.success(request, f"{cantidad} foto{'s' if cantidad != 1 else ''} subida{'s' if cantidad != 1 else ''}.")
        return redirect("detalle_parte", pk=pk)
    return render(request, "partes/form_simple.html", {
        "parte": parte, "form": form, "titulo": "Subir fotos", "multipart": True})


# ---------------------------------------------------------------------------
# Bomberos: listado, ficha (como RUBA) con métricas, alta y edición
# ---------------------------------------------------------------------------

@requiere_permiso("partes.view_integrante", "Tu rol no permite ver la ficha de los integrantes.")
def bomberos(request):
    anio = timezone.localdate().year
    integrantes = (Integrante.objects.select_related("rango", "estado")
                   .annotate(salidas_anio=Count("participaciones", filter=Q(participaciones__parte__anio=anio))))
    q = request.GET.get("q", "").strip()
    if q:
        integrantes = integrantes.filter(Q(apellido__icontains=q) | Q(nombre__icontains=q)
                                         | Q(legajo__icontains=q) | Q(dni__icontains=q))
    escalafon = request.GET.get("escalafon", "")
    if escalafon:
        integrantes = integrantes.filter(rango__escalafon=escalafon)
    estado = request.GET.get("estado", "")
    if estado:
        integrantes = integrantes.filter(estado_id=estado)
    grupo = request.GET.get("grupo", "")
    if grupo:
        integrantes = integrantes.filter(grupo_guardia=grupo)
    meritos = dict(NumeroMerito.objects.filter(anio=anio).values_list("integrante_id", "numero_merito"))
    filas = [(i, meritos.get(i.pk)) for i in integrantes.order_by("rango__orden_jerarquico", "apellido")]
    return render(request, "partes/bomberos.html", {
        "filas": filas, "anio": anio, "q": q, "escalafon": escalafon, "estado": estado, "grupo": grupo,
        "escalafones": Rango.Escalafon.choices, "estados": EstadoIntegrante.objects.all(),
        "grupos": Integrante.GrupoGuardia.choices,
    })


@login_required
def ficha_bombero(request, pk):
    integrante = get_object_or_404(Integrante.objects.select_related("rango", "estado", "usuario"), pk=pk)
    propio = integrante.usuario_id == request.user.pk
    if not (propio or request.user.has_perm("partes.view_integrante")):
        messages.error(request, "Tu rol no permite ver la ficha de los integrantes.")
        return redirect("menu")
    return _perfil(request, integrante, propio)


PESTANAS_PERFIL = [("actividad", "Actividad"), ("datos", "Datos"), ("familia", "Familia"), ("trayectoria", "Trayectoria")]


def _perfil(request, integrante, propio):
    """El perfil de un bombero, organizado como una red social: portada con la foto, sus cifras
    del año y pestañas (actividad como muro, datos, familia, trayectoria). Es la misma pantalla
    para "Mi perfil" y para la ficha que ve Estadística: cada dato está en un solo lugar."""
    anios = sorted(set(Periodo.objects.values_list("anio", flat=True)) | {timezone.localdate().year}, reverse=True)
    anio = int(request.GET["anio"]) if request.GET.get("anio", "").isdigit() else anios[0]
    pestana = request.GET.get("pestana", "actividad")
    if pestana not in dict(PESTANAS_PERFIL):
        pestana = "actividad"

    salidas = list(PartePersonal.objects.filter(integrante=integrante, parte__anio=anio)
                   .select_related("parte", "parte__tipo", "parte__motivo"))
    guardias = list(integrante.partes_como_guardia.filter(anio=anio).select_related("tipo"))
    capacitaciones = _capacitaciones_de(integrante, anio)
    mantenimientos = list(integrante.mantenimientos.filter(anio=anio).select_related("vehiculo"))

    contexto = {
        "i": integrante, "propio": propio, "anio": anio, "anios": anios,
        "pestana": pestana, "pestanas": PESTANAS_PERFIL,
        "periodo": Periodo.objects.filter(anio=anio).first(),
        "merito": integrante.numero_merito_en(anio),
        "resumen": {
            "salidas": len(salidas), "guardias": len(guardias),
            "capacitaciones": len(capacitaciones["capacitaciones"]),
            "horas_capacitacion": capacitaciones["horas_capacitacion"],
            "mantenimientos": len(mantenimientos),
            "horas_mantenimiento": sum(m.horas or 0 for m in mantenimientos),
        },
        "firmas_baja": Firma.de(integrante, Firma.Accion.BAJA_INTEGRANTE),
        "dado_de_baja": integrante.estado.nombre_estado == "Baja",
    }
    if pestana == "actividad":
        # El muro del año: lo más reciente primero.
        muro = [{"fecha": p.parte.fecha, "tipo": "Siniestro", "titulo": f"{p.parte.tipo} · {p.parte.motivo}",
                 "detalle": f"Parte {p.parte.numero_correlativo}/{p.parte.anio} · {p.parte.ubicacion_calle} "
                            f"{p.parte.ubicacion_numero}".strip(),
                 "url": reverse("detalle_parte", args=[p.parte.pk])} for p in salidas]
        muro += [{"fecha": g.fecha, "tipo": "Guardia", "titulo": f"Atendió la llamada: {g.tipo}",
                  "detalle": f"Parte {g.numero_correlativo}/{g.anio}", "url": reverse("detalle_parte", args=[g.pk])}
                 for g in guardias]
        muro += [{"fecha": c.fecha, "tipo": "Capacitación", "titulo": c.tema,
                  "detalle": f"Capacitación {c.numero_correlativo}/{c.anio}" + (f" · {c.horas} h" if c.horas else ""),
                  "url": reverse("detalle_capacitacion", args=[c.pk])} for c in capacitaciones["capacitaciones"]]
        muro += [{"fecha": c.fecha, "tipo": "Instructor", "titulo": f"Ayudante instructor: {c.tema}",
                  "detalle": f"Capacitación {c.numero_correlativo}/{c.anio}",
                  "url": reverse("detalle_capacitacion", args=[c.pk])} for c in capacitaciones["como_ayudante"]]
        muro += [{"fecha": m.fecha, "tipo": "Mantenimiento",
                  "titulo": f"{m.que} · {m.get_tipo_display()}" + (f" · {m.motivo}" if m.motivo else ""),
                  "detalle": f"Mantenimiento {m.numero_correlativo}/{m.anio}" + (f" · {m.horas} h" if m.horas else ""),
                  "url": reverse("detalle_mantenimiento", args=[m.pk])} for m in mantenimientos]
        contexto["muro"] = sorted(muro, key=lambda item: item["fecha"], reverse=True)
        contexto["por_tipo"] = sorted(
            ((tipo, sum(1 for p in salidas if p.parte.tipo.descripcion == tipo))
             for tipo in {p.parte.tipo.descripcion for p in salidas}), key=lambda par: -par[1])
    elif pestana == "datos":
        contexto["secciones"] = _secciones_ficha(integrante)
    elif pestana == "familia":
        contexto["familiares"] = integrante.familiares.all()
    else:
        contexto["historial"] = integrante.historial.select_related("estado")
        contexto["historial_merito"] = integrante.numeros_merito.select_related("rango")
    return render(request, "partes/ficha_bombero.html", contexto)


@require_POST
@requiere_permiso("partes.change_integrante", "Las bajas las da Estadística.", volver="ficha_bombero")
def dar_de_baja(request, pk):
    """Baja de un bombero: se confirma con la contraseña y queda firmada por quien la dio."""
    integrante = get_object_or_404(Integrante.objects.select_related("estado", "rango"), pk=pk)
    if integrante.estado.nombre_estado == "Baja":
        messages.info(request, "Ya estaba dado de baja.")
        return redirect("ficha_bombero", pk=pk)
    form = BajaForm(request.POST, usuario=request.user)
    if not form.is_valid():
        for errores in form.errors.values():
            for error in errores:
                messages.error(request, error)
        return redirect("ficha_bombero", pk=pk)
    anio = timezone.localdate().year
    baja, _ = EstadoIntegrante.objects.get_or_create(nombre_estado="Baja")
    with transaction.atomic():
        Firma.sellar(request.user, Firma.Accion.BAJA_INTEGRANTE, integrante, {
            "integrante": str(integrante), "legajo": integrante.legajo, "dni": integrante.dni,
            "rango": str(integrante.rango), "estado_anterior": str(integrante.estado),
            "numero_merito": integrante.numero_merito_en(anio), "motivo": form.cleaned_data["motivo"],
        })
        integrante.estado = baja
        integrante.save(update_fields=["estado"])
        HistorialIntegrante.objects.create(integrante=integrante, fecha_desde=timezone.localdate(),
                                           estado=baja, motivo_baja=form.cleaned_data["motivo"])
        # Deja de tener número este año: queda libre para otro.
        integrante.numeros_merito.filter(anio=anio).delete()
    messages.success(request, f"{integrante} quedó dado de baja. La baja quedó firmada a tu nombre.")
    return redirect("ficha_bombero", pk=pk)


def _capacitaciones_de(integrante, anio):
    """Capacitaciones del año: a las que asistió y en las que fue ayudante instructor."""
    asistio = list(integrante.capacitaciones.filter(anio=anio))
    return {
        "capacitaciones": asistio,
        "horas_capacitacion": sum(c.horas or 0 for c in asistio),
        "como_ayudante": integrante.capacitaciones_ayudante.filter(anio=anio),
    }


def _secciones_ficha(integrante):
    """[(título, [(etiqueta, valor legible, url si es archivo)])] en el orden de RUBA."""
    from datetime import date
    etiquetas = IntegranteForm().fields
    secciones = []
    for titulo, campos in SECCIONES_FICHA:
        filas = []
        for nombre in campos:
            campo = Integrante._meta.get_field(nombre)
            valor = getattr(integrante, nombre)
            url = None
            if campo.choices:
                valor = getattr(integrante, f"get_{nombre}_display")()
            elif isinstance(valor, bool):
                valor = "Sí" if valor else "No"
            elif isinstance(valor, date):
                valor = valor.strftime("%d/%m/%Y")
            elif isinstance(campo, models.FileField):
                url = valor.url if valor else None
                valor = "Ver archivo" if valor else ""
            elif nombre == "rango" and valor:
                valor = f"{valor} ({valor.get_escalafon_display()})"
            filas.append((etiquetas[nombre].label, valor if valor not in (None, "") else "—", url))
        secciones.append((titulo, filas))
    return secciones


def _editar_bombero(request, integrante):
    datos = request.POST if request.method == "POST" else None
    archivos = request.FILES if request.method == "POST" else None
    form = IntegranteForm(datos, archivos, instance=integrante)
    familiares = FamiliarFormSet(datos, instance=integrante, prefix="familiares")
    historial = HistorialFormSet(datos, instance=integrante, prefix="historial")
    if datos is not None:
        if form.is_valid() and familiares.is_valid() and historial.is_valid():
            with transaction.atomic():
                integrante = form.save()
                familiares.instance = historial.instance = integrante
                familiares.save()
                historial.save()
            messages.success(request, f"Ficha de {integrante.apellido}, {integrante.nombre} guardada.")
            return redirect("ficha_bombero", pk=integrante.pk)
        messages.error(request, "Hay errores en el formulario: revisá los campos marcados.")
    return render(request, "partes/bombero_form.html", {
        "form": form, "familiares": familiares, "historial": historial, "integrante": integrante})


@requiere_permiso("partes.add_integrante", "Tu rol no permite dar de alta integrantes.")
def nuevo_bombero(request):
    return _editar_bombero(request, Integrante())


@requiere_permiso("partes.change_integrante", "Tu rol no permite modificar integrantes.")
def editar_bombero(request, pk):
    return _editar_bombero(request, get_object_or_404(Integrante, pk=pk))


# ---------------------------------------------------------------------------
# Capacitaciones
# ---------------------------------------------------------------------------

SIN_PERMISO_CAPACITACION = "Las capacitaciones las carga y corrige Estadística."


@requiere_permiso("partes.view_capacitacion", "Tu rol no permite ver las capacitaciones.")
def capacitaciones(request):
    anios = sorted(set(Capacitacion.objects.values_list("anio", flat=True)) | {timezone.localdate().year},
                   reverse=True)
    try:
        anio = int(request.GET.get("anio", anios[0]))
    except ValueError:
        anio = anios[0]
    lista = Capacitacion.objects.filter(anio=anio).annotate(cantidad=Count("asistencias"))
    q = request.GET.get("q", "").strip()
    if q:
        lista = lista.filter(Q(datos__icontains=q) | Q(tema__icontains=q) | Q(lugar__icontains=q) | Q(a_cargo__icontains=q)
                             | Q(a_cargo_integrante__apellido__icontains=q))
    return render(request, "partes/capacitaciones.html", {
        "capacitaciones": lista, "anio": anio, "anios": anios, "q": q,
    })


@requiere_permiso("partes.add_capacitacion", SIN_PERMISO_CAPACITACION)
def nueva_capacitacion(request):
    capacitacion = Capacitacion(cargado_por=request.user)
    return _formulario_capacitacion(request, capacitacion)


@requiere_permiso("partes.change_capacitacion", SIN_PERMISO_CAPACITACION, volver="detalle_capacitacion")
def editar_capacitacion(request, pk):
    return _formulario_capacitacion(request, get_object_or_404(Capacitacion, pk=pk))


def _formulario_capacitacion(request, capacitacion):
    form = CapacitacionForm(request.POST or None, instance=capacitacion)
    if request.method == "POST":
        if form.is_valid():
            with transaction.atomic():
                capacitacion = form.save()
                _actualizar_km(capacitacion.vehiculo, capacitacion.km_regreso)
            messages.success(request, f"Capacitación {capacitacion.numero_correlativo}/{capacitacion.anio} guardada.")
            return redirect("detalle_capacitacion", pk=capacitacion.pk)
        log.warning("Capacitación rechazada: %s", "; ".join(
            f"{campo}: {' '.join(errores)}" for campo, errores in form.errors.items()))
        messages.error(request, "Hay errores en el formulario: revisá los campos marcados.")
    km_moviles = {str(pk): km for pk, km in Vehiculo.objects.values_list("pk", "kilometraje_actual")}
    return render(request, "partes/capacitacion_form.html", {
        "puede_moviles": request.user.has_perm("partes.add_vehiculo"),
        "form": form, "capacitacion": capacitacion, "km_moviles": km_moviles,
    })


def _actualizar_km(movil, km):
    """El kilometraje del móvil solo avanza: un km menor (dato viejo o error) no lo baja."""
    if movil and km and km > movil.kilometraje_actual:
        movil.kilometraje_actual = km
        movil.save(update_fields=["kilometraje_actual"])


def _con_merito(integrantes, anio):
    """[(integrante, N° de mérito del año)] ordenado por número, como en las planillas."""
    meritos = dict(NumeroMerito.objects.filter(anio=anio).values_list("integrante_id", "numero_merito"))
    return sorted(((i, meritos.get(i.pk)) for i in integrantes),
                  key=lambda par: (par[1] is None, par[1] or 0, par[0].apellido))


@requiere_permiso("partes.view_capacitacion", "Tu rol no permite ver las capacitaciones.", volver=None)
def detalle_capacitacion(request, pk):
    capacitacion = get_object_or_404(
        Capacitacion.objects.select_related("vehiculo", "guardia", "cargado_por"), pk=pk)
    asistentes = _con_merito(capacitacion.asistentes.select_related("rango"), capacitacion.anio)
    return render(request, "partes/capacitacion.html", {
        "c": capacitacion, "asistentes": asistentes, "ayudantes": capacitacion.ayudantes.all(),
        "cobraron": set(capacitacion.asistencias.filter(cobro=True).values_list("integrante", flat=True)),
    })


# ---------------------------------------------------------------------------
# Períodos anuales ("tomos")
# ---------------------------------------------------------------------------

SIN_PERMISO_PERIODO = "Lo hace el encargado de Estadística: tu rol no permite gestionar los períodos."


@requiere_permiso("partes.view_periodo", "Tu rol no permite ver los períodos.")
def tomos(request):
    Periodo.del_anio(timezone.localdate().year)
    lista = list(Periodo.objects.select_related("cerro"))
    partes = dict(ParteSiniestro.objects.values("anio").annotate(n=Count("pk")).values_list("anio", "n"))
    capacitaciones = dict(Capacitacion.objects.values("anio").annotate(n=Count("pk")).values_list("anio", "n"))
    bomberos = dict(NumeroMerito.objects.values("anio").annotate(n=Count("pk")).values_list("anio", "n"))
    mantenimientos = dict(Mantenimiento.objects.values("anio").annotate(n=Count("pk")).values_list("anio", "n"))
    for periodo in lista:
        periodo.mantenimientos = mantenimientos.get(periodo.anio, 0)
        periodo.partes = partes.get(periodo.anio, 0)
        periodo.capacitaciones = capacitaciones.get(periodo.anio, 0)
        periodo.bomberos = bomberos.get(periodo.anio, 0)
    ultimo = lista[0] if lista else None
    return render(request, "partes/tomos.html", {
        "tomos": lista, "puede_abrir": ultimo is not None and ultimo.cerrado,
        "siguiente": ultimo.anio + 1 if ultimo else None,
    })


VISTAS_TOMO = [("bomberos", "Bomberos"), ("partes", "Partes de siniestro"), ("capacitaciones", "Capacitaciones"),
               ("mantenimientos", "Mantenimientos")]


@requiere_permiso("partes.view_periodo", "Tu rol no permite ver los períodos.", volver=None)
def tomo(request, anio):
    if anio == timezone.localdate().year:
        Periodo.del_anio(anio)
    periodo = get_object_or_404(Periodo.objects.select_related("cerro", "abrio"), anio=anio)
    ver = request.GET.get("ver", "bomberos")
    if ver not in dict(VISTAS_TOMO):
        ver = "bomberos"
    # Cerrado: lo que quedó guardado al cerrar. Abierto: las cuentas de hoy.
    filas = (list(periodo.resumenes.select_related("integrante")) if periodo.cerrado
             else periodos.resumen_bomberos(anio))
    if request.GET.get("formato") == "csv":
        return _resumen_csv(periodo, filas)
    campos = ["salidas", "guardias", "capacitaciones", "horas_capacitacion", "como_ayudante",
              "mantenimientos", "horas_mantenimiento"]
    totales = {c: sum((f[c] if isinstance(f, dict) else getattr(f, c)) for f in filas) for c in campos}
    contexto = {"periodo": periodo, "ver": ver, "vistas": VISTAS_TOMO, "filas": filas, "totales": totales,
                "problemas": periodo.problemas_para_cerrar() if not periodo.cerrado else []}
    if ver == "partes":
        contexto["partes"] = (ParteSiniestro.objects.filter(anio=anio)
                              .select_related("tipo", "motivo", "estado_parte")
                              .order_by("numero_correlativo"))
    elif ver == "capacitaciones":
        contexto["capacitaciones"] = (Capacitacion.objects.filter(anio=anio)
                                      .annotate(cantidad=Count("asistencias")).order_by("numero_correlativo"))
    elif ver == "mantenimientos":
        contexto["mantenimientos"] = (Mantenimiento.objects.filter(anio=anio).select_related("vehiculo")
                                      .annotate(cantidad=Count("participaciones")).order_by("numero_correlativo"))
    contexto["firma_cierre"] = Firma.de(periodo, Firma.Accion.CIERRE_PERIODO).first()
    if ver == "partes":
        contexto["tiempos_salida"] = _tiempos_salida(contexto["partes"])
    return render(request, "partes/tomo.html", contexto)


def _tiempos_salida(partes):
    """Métrica clave: minutos entre que se inició el parte (llamada) y la salida del cuartel."""
    from statistics import median
    minutos = [p.minutos_despacho for p in partes if p.unidad_salio and p.minutos_despacho is not None]
    if not minutos:
        return None
    return {"cantidad": len(minutos), "promedio": round(sum(minutos) / len(minutos), 1),
            "mediana": median(minutos), "maximo": max(minutos), "minimo": min(minutos)}


def _resumen_csv(periodo, filas):
    """Planilla para Estadística (se abre con Excel): una fila por bombero."""
    import csv
    from django.http import HttpResponse
    respuesta = HttpResponse(content_type="text/csv; charset=utf-8")
    respuesta["Content-Disposition"] = f'attachment; filename="periodo_{periodo.anio}.csv"'
    respuesta.write("\ufeff")  # para que Excel reconozca las tildes
    escritor = csv.writer(respuesta, delimiter=";")
    escritor.writerow(["N°", "Apellido", "Nombre", "Legajo", "Rango", "Estado", "Siniestros asistidos",
                       "Partes como guardia", "Capacitaciones", "Horas de capacitación", "Como ayudante instructor",
                       "Mantenimientos", "Horas de mantenimiento"])
    for f in filas:
        f = f if isinstance(f, dict) else vars(f) | {"integrante": f.integrante}
        i = f["integrante"]
        escritor.writerow([f"{f['numero_merito']:03d}" if f["numero_merito"] else "", i.apellido, i.nombre, i.legajo,
                           f["rango"], f["estado"], f["salidas"], f["guardias"], f["capacitaciones"],
                           str(f["horas_capacitacion"]).replace(".", ","), f["como_ayudante"],
                           f["mantenimientos"], str(f["horas_mantenimiento"]).replace(".", ",")])
    return respuesta


@require_POST
@requiere_permiso("partes.gestionar_periodo", SIN_PERMISO_PERIODO, volver=None)
def cerrar_tomo(request, anio):
    periodo = get_object_or_404(Periodo, anio=anio)
    firma = FirmarForm(request.POST, usuario=request.user)
    if not firma.is_valid():
        messages.error(request, " ".join(firma.errors.get("clave", ["Falta la contraseña para firmar."])))
        return redirect("tomo", anio=anio)
    try:
        periodo.cerrar(request.user)
    except ValidationError as error:
        for problema in error.messages:
            messages.error(request, f"No se puede cerrar el período: {problema}")
    else:
        sello = Firma.de(periodo, Firma.Accion.CIERRE_PERIODO).first()
        messages.success(request, f"Período {anio} cerrado y firmado (sello {sello.codigo}). "
                                  "Queda guardado como tomo y ya no se modifica.")
    return redirect("tomo", anio=anio)


@require_POST
@requiere_permiso("partes.gestionar_periodo", SIN_PERMISO_PERIODO, volver=None)
def abrir_tomo(request):
    ultimo = Periodo.objects.first()
    if ultimo is not None and not ultimo.cerrado:
        messages.error(request, f"Primero hay que cerrar el período {ultimo.anio}.")
        return redirect("tomos")
    anio = ultimo.anio + 1 if ultimo else timezone.localdate().year
    Periodo.objects.create(anio=anio, abrio=request.user)
    messages.success(request, f"Período {anio} abierto. Asigná los números de mérito y marcá las bajas.")
    return redirect("numeros_tomo", anio=anio)


@requiere_permiso("partes.gestionar_periodo", SIN_PERMISO_PERIODO, volver=None)
def numeros_tomo(request, anio):
    periodo = get_object_or_404(Periodo, anio=anio)
    if periodo.cerrado:
        messages.error(request, f"El período {anio} está cerrado: sus números ya no se cambian.")
        return redirect("tomo", anio=anio)
    form = NumerosPeriodoForm(request.POST or None, periodo=periodo)
    if request.method == "POST":
        if form.is_valid():
            with transaction.atomic():
                asignados = form.save()
            messages.success(request, f"Números {anio} guardados: {asignados} asignado{'s' if asignados != 1 else ''}.")
            return redirect("tomo", anio=anio)
        messages.error(request, "Hay números repetidos o inválidos: revisá los marcados.")
    return render(request, "partes/tomo_numeros.html", {"periodo": periodo, "form": form})


# ---------------------------------------------------------------------------
# Mantenimiento de los móviles
# ---------------------------------------------------------------------------

SIN_PERMISO_MANTENIMIENTO = "Los mantenimientos los carga y corrige Estadística."


@requiere_permiso("partes.view_mantenimiento", "Tu rol no permite ver los mantenimientos.")
def mantenimientos(request):
    anios = sorted(set(Mantenimiento.objects.values_list("anio", flat=True)) | {timezone.localdate().year},
                   reverse=True)
    try:
        anio = int(request.GET.get("anio", anios[0]))
    except ValueError:
        anio = anios[0]
    lista = (Mantenimiento.objects.filter(anio=anio).select_related("vehiculo", "a_cargo")
             .annotate(cantidad=Count("participaciones")))
    movil = request.GET.get("movil", "")
    if movil.isdigit():
        lista = lista.filter(vehiculo_id=int(movil))
    elif movil == "otros":
        lista = lista.filter(vehiculo=None)
    q = request.GET.get("q", "").strip()
    if q:
        lista = lista.filter(Q(motivo__icontains=q) | Q(descripcion__icontains=q) | Q(taller__icontains=q)
                             | Q(elemento__icontains=q))
    lista = list(lista)
    return render(request, "partes/mantenimientos.html", {
        "mantenimientos": lista, "anio": anio, "anios": anios, "q": q, "movil": movil,
        "moviles": Vehiculo.objects.all(),
        "costo_anio": sum(m.costo_total or 0 for m in lista),
    })


@requiere_permiso("partes.add_mantenimiento", SIN_PERMISO_MANTENIMIENTO)
def nuevo_mantenimiento(request):
    mantenimiento = Mantenimiento(cargado_por=request.user)
    movil = request.GET.get("movil", "")
    if movil.isdigit():
        mantenimiento.vehiculo_id = int(movil)
    return _formulario_mantenimiento(request, mantenimiento)


@requiere_permiso("partes.change_mantenimiento", SIN_PERMISO_MANTENIMIENTO, volver="detalle_mantenimiento")
def editar_mantenimiento(request, pk):
    return _formulario_mantenimiento(request, get_object_or_404(Mantenimiento, pk=pk))


def _formulario_mantenimiento(request, mantenimiento):
    form = MantenimientoForm(request.POST or None, instance=mantenimiento)
    if request.method == "POST":
        if form.is_valid():
            with transaction.atomic():
                mantenimiento = form.save()
                _actualizar_km(mantenimiento.vehiculo, mantenimiento.kilometraje)
            messages.success(request, f"Mantenimiento {mantenimiento.numero_correlativo}/{mantenimiento.anio} guardado.")
            return redirect("detalle_mantenimiento", pk=mantenimiento.pk)
        log.warning("Mantenimiento rechazado: %s", "; ".join(
            f"{campo}: {' '.join(errores)}" for campo, errores in form.errors.items()))
        messages.error(request, "Hay errores en el formulario: revisá los campos marcados.")
    km_moviles = {str(pk): km for pk, km in Vehiculo.objects.values_list("pk", "kilometraje_actual")}
    return render(request, "partes/mantenimiento_form.html", {
        "puede_moviles": request.user.has_perm("partes.add_vehiculo"),
        "form": form, "mantenimiento": mantenimiento, "km_moviles": km_moviles,
    })


@requiere_permiso("partes.view_mantenimiento", "Tu rol no permite ver los mantenimientos.", volver=None)
def detalle_mantenimiento(request, pk):
    mantenimiento = get_object_or_404(
        Mantenimiento.objects.select_related("vehiculo", "autorizo", "a_cargo", "controlo", "guardia"), pk=pk)
    return render(request, "partes/mantenimiento.html", {
        "m": mantenimiento,
        "participantes": _con_merito(mantenimiento.participantes.select_related("rango"), mantenimiento.anio),
        "anteriores": (mantenimiento.vehiculo.mantenimientos.exclude(pk=mantenimiento.pk)
                       .filter(fecha__lte=mantenimiento.fecha)[:5] if mantenimiento.vehiculo_id else []),
    })


# ---------------------------------------------------------------------------
# Móviles del cuartel (la lista de unidades de los partes, capacitaciones y mantenimientos)
# ---------------------------------------------------------------------------

SIN_PERMISO_MOVILES = "Los móviles los carga Estadística."


@requiere_permiso("partes.view_vehiculo", "Tu rol no permite ver los móviles.")
def moviles(request):
    return render(request, "partes/moviles.html", {
        "categorias": agrupar_moviles(Vehiculo.objects.select_related("tipo_vehiculo", "combustible")),
    })


@requiere_permiso("partes.view_vehiculo", "Tu rol no permite ver los móviles.", volver=None)
def detalle_movil(request, pk):
    movil = get_object_or_404(Vehiculo.objects.select_related("tipo_vehiculo", "combustible"), pk=pk)
    return render(request, "partes/movil.html", {
        "movil": movil,
        "partes": movil.partes.select_related("tipo", "motivo", "estado_parte")[:10],
        "cantidad_partes": movil.partes.count(),
        "capacitaciones": movil.capacitaciones.all()[:10],
        "cantidad_capacitaciones": movil.capacitaciones.count(),
        "mantenimientos": movil.mantenimientos.all()[:10],
        "cantidad_mantenimientos": movil.mantenimientos.count(),
    })


@requiere_permiso("partes.add_vehiculo", SIN_PERMISO_MOVILES, volver=None)
def nuevo_movil(request):
    return _formulario_movil(request, Vehiculo())


@requiere_permiso("partes.change_vehiculo", SIN_PERMISO_MOVILES, volver=None)
def editar_movil(request, pk):
    return _formulario_movil(request, get_object_or_404(Vehiculo, pk=pk))


def _formulario_movil(request, movil):
    form = VehiculoForm(request.POST or None, instance=movil)
    if request.method == "POST" and form.is_valid():
        movil = form.save()
        messages.success(request, f"{movil} guardado.")
        return redirect("detalle_movil", pk=movil.pk)
    return render(request, "partes/movil_form.html", {"form": form, "movil": movil})


@require_POST
@requiere_permiso("partes.change_vehiculo", SIN_PERMISO_MOVILES, volver=None)
def baja_movil(request, pk):
    """De baja deja de ofrecerse en las listas, pero conserva su historial. Se puede reactivar."""
    movil = get_object_or_404(Vehiculo, pk=pk)
    movil.dado_de_baja = not movil.dado_de_baja
    movil.save(update_fields=["dado_de_baja"])
    messages.success(request, f"{movil} {'dado de baja' if movil.dado_de_baja else 'reactivado'}.")
    return redirect("detalle_movil", pk=pk)


@require_POST
@requiere_permiso("partes.delete_vehiculo", SIN_PERMISO_MOVILES, volver=None)
def quitar_movil(request, pk):
    """Solo se borra un móvil sin historial (por ejemplo, cargado por error)."""
    movil = get_object_or_404(Vehiculo, pk=pk)
    if movil.tiene_historial:
        messages.error(request, f"{movil} ya figura en partes, capacitaciones o mantenimientos: "
                                "no se puede quitar, pero sí dar de baja.")
        return redirect("detalle_movil", pk=pk)
    nombre = str(movil)
    movil.delete()
    messages.success(request, f"{nombre} quitado.")
    return redirect("moviles")


# ---------------------------------------------------------------------------
# Calles que faltan en el listado
# ---------------------------------------------------------------------------

@requiere_permiso("partes.view_anotacionlibroguardia", "Tu rol no permite ver el libro de guardia.", volver=None)
def libro_guardia(request):
    """El libro de guardia del día: se agregan anotaciones, nunca se editan ni se borran."""
    hoy = timezone.localdate()
    fecha = _fecha_elegida(request.GET.get("fecha", "")) or (hoy.year, hoy.month, hoy.day)
    dia = date(fecha[0], fecha[1] or 1, fecha[2] or 1)
    puede_anotar = request.user.has_perm("partes.add_anotacionlibroguardia") and dia == hoy
    form = AnotacionForm(request.POST or None, initial={"hora": timezone.localtime().strftime("%H:%M")})
    if request.method == "POST":
        if not puede_anotar:
            messages.error(request, "En el libro de guardia solo se anota en el día de hoy.")
        elif form.is_valid():
            anotacion = AnotacionLibroGuardia.anotar(request.user, form.cleaned_data["texto"], form.cleaned_data["hora"])
            messages.success(request, f"Anotado en el libro (folio {anotacion.numero}/{anotacion.anio}).")
            return redirect("libro_guardia")
    anotaciones = (AnotacionLibroGuardia.objects.filter(fecha=dia)
                   .select_related("corrige").prefetch_related("correcciones"))
    puede_corregir_libro = request.user.has_perm("partes.corregir_libro")
    return render(request, "partes/libro_guardia.html", {
        "form": form, "anotaciones": anotaciones, "dia": dia, "es_hoy": dia == hoy, "puede_anotar": puede_anotar,
        "anterior": (dia - timedelta(days=1)).isoformat(), "siguiente": (dia + timedelta(days=1)).isoformat(),
        "puede_corregir_libro": puede_corregir_libro,
        "alteradas": AnotacionLibroGuardia.verificar_cadena() if puede_corregir_libro else None,
    })


SEXO_PRENSA = {"M": "MASCULINO", "F": "FEMENINA", "X": "PERSONA"}


def _persona_prensa(d):
    """'MASCULINO 28 AÑOS': sin nombre ni DNI (a la prensa no van datos personales)."""
    texto = SEXO_PRENSA.get(d.sexo, "PERSONA")
    if d.rol == Damnificado.Rol.ACOMPANANTE:
        texto = f"ACOMP/ {texto}"
    return f"{texto} {d.edad} AÑOS" if d.edad else texto


def resumen_prensa_del_dia(dia):
    """El parte de prensa como lo mandan al grupo de periodistas (notas/PRENSA.txt): hora y lugar, motivo,
    vehículos y personas (sexo y edad), qué se hizo y la dotación. Sin nombres, DNI ni patentes."""
    partes = (ParteSiniestro.objects.filter(fecha=dia).select_related("motivo", "vehiculo__tipo_vehiculo")
              .prefetch_related("damnificados__hacia", "vehiculos_siniestrados__damnificados").order_by("hora_llamada"))
    lineas = ["BOMBEROS VOLUNTARIOS VILLA MARIA", "", f"PARTE DE PRENSA - {dia:%d/%m/%Y}"]
    for p in partes:
        lineas += ["", f"- HS {p.hora_llamada:%H:%M} - {p.ubicacion.upper()}", f"- {p.motivo.descripcion.upper()}"]
        for r in p.vehiculos_siniestrados.all():
            vehiculo = f"{r.marca} {r.modelo}".strip().upper() or "VEHÍCULO"
            personas = " Y ".join(_persona_prensa(d) for d in r.damnificados.all())
            lineas.append(f"- {vehiculo}" + (f" - {personas}" if personas else ""))
        sueltos = [d for d in p.damnificados.all() if not d.vehiculo_siniestrado_id]
        if sueltos:
            lineas.append("- " + " Y ".join(_persona_prensa(d) for d in sueltos))
        notas = []
        for d in p.damnificados.all():
            quien = _persona_prensa(d).split(" AÑOS")[0].rsplit(" ", 1)[0] if d.edad else _persona_prensa(d)
            lesion = f" CON {d.lesion.upper()}" if d.lesion else ""
            if d.trasladado and d.destino:
                notas.append(f"SE TRASLADA A {d.destino.upper()} {quien}{lesion}")
            elif d.herido:
                notas.append(f"{quien}{lesion}, NO SE REALIZA TRASLADO")
        if notas:
            lineas.append("- NOTA: " + ". ".join(notas))
        unidad = p.vehiculo.tipo_vehiculo.descripcion.upper() if p.vehiculo_id and p.vehiculo.tipo_vehiculo_id else "UNIDAD"
        efectivos = p.personal.count()
        lineas.append(f"- {unidad}" + (f" CON {efectivos} EFECTIVOS" if efectivos else ""))
    if not partes:
        lineas += ["", "Sin salidas registradas ese día."]
    return "\n".join(lineas)


@requiere_permiso("partes.view_periodo", "Tu rol no permite armar el resumen de prensa.", volver=None)
def resumen_prensa(request):
    hoy = timezone.localdate()
    fecha = _fecha_elegida(request.GET.get("fecha", "")) or (hoy.year, hoy.month, hoy.day)
    dia = date(fecha[0], fecha[1] or 1, fecha[2] or 1)
    return render(request, "partes/prensa.html", {"texto": resumen_prensa_del_dia(dia), "dia": dia,
                                                  "anterior": (dia - timedelta(days=1)).isoformat(),
                                                  "siguiente": (dia + timedelta(days=1)).isoformat(), "es_hoy": dia == hoy})


@requiere_permiso("partes.view_ordenentrega", "Tu rol no permite ver las órdenes de entrega.", volver=None)
def ordenes_entrega(request):
    ordenes = OrdenEntrega.objects.select_related("entregado_por").prefetch_related("elementos")
    estado = request.GET.get("estado", "")
    q = request.GET.get("q", "").strip()
    if q:
        ordenes = ordenes.filter(Q(retira_nombre__icontains=q) | Q(destino__icontains=q)
                                 | Q(elementos__descripcion__icontains=q)).distinct()
    ordenes = [o for o in ordenes[:300] if not estado or o.estado == estado]
    return render(request, "partes/ordenes.html", {"ordenes": ordenes, "estado": estado, "q": q,
                                                   "estados": ["Pendiente de devolución", "Entregado", "Recibido", "Devuelto"]})


@requiere_permiso("partes.add_ordenentrega", "Tu rol no permite cargar órdenes de entrega.", volver=None)
def nueva_orden(request):
    return _formulario_orden(request, OrdenEntrega(cargado_por=request.user))


@requiere_permiso("partes.change_ordenentrega", "Tu rol no permite corregir órdenes de entrega.", volver=None)
def editar_orden(request, pk):
    return _formulario_orden(request, get_object_or_404(OrdenEntrega, pk=pk))


def _formulario_orden(request, orden):
    form = OrdenEntregaForm(request.POST or None, instance=orden)
    elementos = ElementosFormSet(request.POST or None, instance=orden, prefix="elementos")
    if request.method == "POST":
        if form.is_valid() and elementos.is_valid():
            with transaction.atomic():
                orden = form.save()
                elementos.instance = orden
                elementos.save()
            messages.success(request, f"{orden} guardada.")
            return redirect("detalle_orden", pk=orden.pk)
        messages.error(request, "No se guardó: revisá los campos marcados.")
    return render(request, "partes/orden_form.html", {"form": form, "elementos": elementos, "orden": orden})


@requiere_permiso("partes.view_ordenentrega", "Tu rol no permite ver las órdenes de entrega.", volver=None)
def detalle_orden(request, pk):
    orden = get_object_or_404(OrdenEntrega.objects.select_related("entregado_por", "autoriza"), pk=pk)
    return render(request, "partes/orden.html", {"o": orden})


@require_POST
@requiere_permiso("partes.corregir_libro", "Solo el jefe corrige el libro de guardia.", volver=None)
def corregir_anotacion(request, pk):
    original = get_object_or_404(AnotacionLibroGuardia, pk=pk)
    form = CorreccionAnotacionForm(request.POST)
    if form.is_valid():
        AnotacionLibroGuardia.anotar(request.user, form.cleaned_data["texto"], original.hora, fecha=original.fecha,
                                     corrige=original, motivo=form.cleaned_data["motivo"])
        messages.success(request, f"Corrección agregada al folio {original.numero}/{original.anio}.")
    else:
        messages.error(request, "No se corrigió: hace falta el texto y un motivo (mínimo 10 letras).")
    return redirect(f"{reverse('libro_guardia')}?fecha={original.fecha.isoformat()}")


@requiere_permiso("partes.add_personale100", "El personal E-100 lo carga Estadística.", volver=None)
def personal_e100(request):
    """Quienes atienden el 100 (los pone la municipalidad): alta, edición y baja (inactivo)."""
    editado = PersonalE100.objects.filter(pk=request.GET.get("editar") or None).first()
    form = PersonalE100Form(request.POST or None, instance=editado)
    if request.method == "POST" and form.is_valid():
        persona = form.save()
        messages.success(request, f"Personal E-100 «{persona}» {'actualizado' if editado else 'agregado'}.")
        return redirect("personal_e100")
    return render(request, "partes/e100.html", {"form": form, "editado": editado,
                                                 "personas": PersonalE100.objects.all()})


@requiere_permiso("partes.add_calle", "Las calles las agrega Estadística.", volver=None)
def calles_agregadas(request):
    form = CalleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        calle = form.save()
        messages.success(request, f"Calle «{calle}» agregada: ya aparece al cargar un parte.")
        return redirect("calles")
    return render(request, "partes/calles.html", {
        "form": form, "calles": Calle.objects.all(), "cantidad_listado": len(geocodificacion.calles_del_listado()),
    })


@require_POST
@requiere_permiso("partes.delete_calle", "Las calles las agrega Estadística.", volver=None)
def quitar_calle(request, pk):
    get_object_or_404(Calle, pk=pk).delete()
    messages.success(request, "Calle quitada del listado.")
    return redirect("calles")
