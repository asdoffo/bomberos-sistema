"""Cuándo vence la sesión, según el rol (decisión del usuario, 10/11).

    Personal de guardia ..... a las 06:00 siguientes al login (cambio de guardia), haya actividad o no
    Jefe, Estadística,
    Graduado, Aspirante ..... 12 h después del login
    Bombero ................. tras 12 h sin actividad
    Invitado y sin rol ...... 10 min después del login

Si alguien tiene varios roles, manda el primero de roles.PRIORIDAD (el guardia que además es bombero
se rige por el cambio de guardia).

No alcanza con SESSION_COOKIE_AGE: Django renueva el vencimiento cada vez que la sesión se modifica.
Por eso guardamos en la sesión la hora del login, la regla que le tocó y (para la de inactividad) la
última actividad, y lo controlamos en cada request. La regla se fija al iniciar sesión: si después le
cambian el rol, rige desde el próximo login.

Para probar sin esperar (ver README): DJANGO_SESION_MINUTOS reemplaza las 12 h y los 10 min por esa
cantidad de minutos, y DJANGO_HORA_CAMBIO_GUARDIA (ej. "20:45") cambia la hora del cambio de guardia.
"""
from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.shortcuts import redirect
from django.utils import timezone

from . import roles

CLAVE_INICIO = "sesion_inicio"
CLAVE_REGLA = "sesion_regla"
CLAVE_ACTIVIDAD = "sesion_actividad"
# La última actividad se anota como mucho una vez por minuto, para no escribir la sesión en cada request.
ANOTAR_ACTIVIDAD_CADA = timedelta(minutes=1)

CAMBIO_GUARDIA = "cambio_guardia"  # vence a la hora del cambio de guardia
FIJA = "fija"                      # vence un tiempo después del login
INACTIVIDAD = "inactividad"        # vence tras un tiempo sin actividad

REGLAS = {
    roles.GUARDIA: (CAMBIO_GUARDIA, None),
    roles.JEFE: (FIJA, "SESION_HORAS_FIJA"),
    roles.ESTADISTICA: (FIJA, "SESION_HORAS_FIJA"),
    roles.GRADUADO: (FIJA, "SESION_HORAS_FIJA"),
    roles.BOMBERO: (INACTIVIDAD, "SESION_HORAS_INACTIVIDAD"),
    roles.ASPIRANTE: (FIJA, "SESION_HORAS_FIJA"),
    roles.INVITADO: (FIJA, "SESION_MINUTOS_CORTA"),
    None: (FIJA, "SESION_MINUTOS_CORTA"),  # sin rol, ej. la cuenta admin
}


def _duracion(clave):
    """La duración de una regla, desde settings (o DJANGO_SESION_MINUTOS para probar)."""
    prueba = getattr(settings, "SESION_MINUTOS_PRUEBA", None)
    if prueba:
        return timedelta(minutes=prueba)
    valor = getattr(settings, clave)
    return timedelta(minutes=valor) if clave.startswith("SESION_MINUTOS") else timedelta(hours=valor)


def hora_cambio_guardia():
    horas, minutos = getattr(settings, "SESION_HORA_CAMBIO_GUARDIA", "06:00").split(":")
    return time(int(horas), int(minutos))


def proximo_cambio_guardia(desde):
    """El próximo cambio de guardia después de `desde`: si entra a las 05:50, es a las 06:00 de ese día;
    si entra a las 06:01, a las 06:00 del día siguiente."""
    local = timezone.localtime(desde)
    corte = local.replace(hour=hora_cambio_guardia().hour, minute=hora_cambio_guardia().minute,
                          second=0, microsecond=0)
    if local >= corte:
        corte += timedelta(days=1)
    return corte


def regla_de(user):
    tipo, clave = REGLAS[roles.rol_principal(user)]
    return tipo, (_duracion(clave) if clave else None)


def iniciar(request, user, ahora=None):
    """Anota en la sesión el login y la regla que le toca. La cookie dura un día más que la sesión:
    si venciera junto con ella, el navegador la borraría antes y el usuario llegaría al login sin el
    aviso de por qué se cerró."""
    ahora = ahora or timezone.now()
    tipo, duracion = regla_de(user)
    request.session[CLAVE_INICIO] = ahora.isoformat()
    request.session[CLAVE_REGLA] = [tipo, duracion.total_seconds() if duracion else None]
    request.session[CLAVE_ACTIVIDAD] = ahora.isoformat()
    tope = (proximo_cambio_guardia(ahora) - ahora) if tipo == CAMBIO_GUARDIA else duracion
    request.session.set_expiry(int((tope + timedelta(days=1)).total_seconds()))


@receiver(user_logged_in)
def registrar_inicio(sender, request, user, **kwargs):
    if request is not None and hasattr(request, "session"):
        iniciar(request, user)


def _regla(request):
    tipo, segundos = request.session.get(CLAVE_REGLA) or [FIJA, None]
    return tipo, (timedelta(seconds=segundos) if segundos else None)


def vencimiento_sesion(request):
    """Cuándo vence la sesión de este request (con la actividad anotada hasta ahora)."""
    inicio = request.session.get(CLAVE_INICIO)
    if not inicio:
        return None
    tipo, duracion = _regla(request)
    if tipo == CAMBIO_GUARDIA:
        return proximo_cambio_guardia(datetime.fromisoformat(inicio))
    if tipo == INACTIVIDAD:
        return datetime.fromisoformat(request.session.get(CLAVE_ACTIVIDAD, inicio)) + duracion
    return datetime.fromisoformat(inicio) + duracion


def _texto_duracion(duracion):
    minutos = int(duracion.total_seconds() // 60)
    if minutos % 60:
        return f"{minutos} min"
    return f"{minutos // 60} h"


def aviso_sesion(request):
    """El aviso del inicio: hasta cuándo dura la sesión, o cuánto aguanta sin actividad."""
    vence = vencimiento_sesion(request)
    if vence is None:
        return None
    tipo, duracion = _regla(request)
    if tipo == INACTIVIDAD:
        return {"texto": f"Tu sesión se cierra tras {_texto_duracion(duracion)} sin actividad",
                "detalle": "Cada vez que usás el sistema se vuelve a contar."}
    local = timezone.localtime(vence)
    dia = "" if local.date() == timezone.localdate() else f" del {local:%d/%m}"
    return {"texto": f"Tu sesión dura hasta las {local:%H:%M}{dia}",
            "detalle": "Por cambio de guardia" if tipo == CAMBIO_GUARDIA else "Después hay que volver a ingresar"}


class ExpiracionTurnoMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            ahora = timezone.now()
            if CLAVE_REGLA not in request.session:
                # Sesión iniciada antes de este control: empieza a contar ahora.
                iniciar(request, request.user, ahora)
            vence = vencimiento_sesion(request)
            if vence is not None and ahora >= vence:
                tipo, _ = _regla(request)
                logout(request)
                messages.info(request, "Tu sesión venció por cambio de guardia. Volvé a ingresar."
                              if tipo == CAMBIO_GUARDIA else "Tu sesión venció. Volvé a ingresar.")
                return redirect(settings.LOGIN_URL)
            tipo, _ = _regla(request)
            if tipo == INACTIVIDAD:
                ultima = datetime.fromisoformat(request.session.get(CLAVE_ACTIVIDAD, ahora.isoformat()))
                if ahora - ultima >= ANOTAR_ACTIVIDAD_CADA:
                    request.session[CLAVE_ACTIVIDAD] = ahora.isoformat()
        return self.get_response(request)
