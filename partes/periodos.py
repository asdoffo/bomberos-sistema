"""Cuentas de un período (tomo): qué hizo cada bombero en el año.

Lo usa la pantalla del tomo abierto (en vivo) y Periodo.cerrar() para congelarlo."""
from collections import Counter, defaultdict

from django.db.models import Q

from .models import (
    AsistenciaCapacitacion, Capacitacion, Integrante, NumeroMerito, ParteSiniestro, PartePersonal,
    ParticipacionMantenimiento,
)

ESTADOS_EN_ACTIVIDAD = ["Activo", "Aspirante", "Auxiliar"]


def resumen_bomberos(anio):
    """Una fila por bombero que tuvo número o actividad ese año, ordenada por N° de mérito.
    Las claves son los campos de ResumenPeriodo (sin periodo)."""
    numeros = {n.integrante_id: n for n in NumeroMerito.objects.filter(anio=anio).select_related("rango")}
    salidas = Counter(PartePersonal.objects.filter(parte__anio=anio).values_list("integrante", flat=True))
    guardias = Counter(ParteSiniestro.objects.filter(anio=anio).exclude(guardia=None)
                       .values_list("guardia", flat=True))
    capacitaciones, horas = Counter(), defaultdict(float)
    for asistencia in AsistenciaCapacitacion.objects.filter(capacitacion__anio=anio).select_related("capacitacion"):
        capacitaciones[asistencia.integrante_id] += 1
        horas[asistencia.integrante_id] += asistencia.capacitacion.horas or 0
    ayudante = Counter(Capacitacion.ayudantes.through.objects.filter(capacitacion__anio=anio)
                       .values_list("integrante", flat=True))
    mantenimientos, horas_mant = Counter(), defaultdict(float)
    for participacion in (ParticipacionMantenimiento.objects.filter(mantenimiento__anio=anio)
                          .select_related("mantenimiento")):
        mantenimientos[participacion.integrante_id] += 1
        horas_mant[participacion.integrante_id] += participacion.mantenimiento.horas or 0

    con_actividad = (set(numeros) | set(salidas) | set(guardias) | set(capacitaciones) | set(ayudante)
                     | set(mantenimientos))
    filas = []
    for integrante in Integrante.objects.filter(pk__in=con_actividad).select_related("rango", "estado"):
        numero = numeros.get(integrante.pk)
        filas.append({
            "integrante": integrante,
            "numero_merito": numero.numero_merito if numero else None,
            "rango": str(numero.rango if numero else integrante.rango),
            "estado": str(integrante.estado),
            "salidas": salidas[integrante.pk],
            "guardias": guardias[integrante.pk],
            "capacitaciones": capacitaciones[integrante.pk],
            "horas_capacitacion": round(horas[integrante.pk], 1),
            "como_ayudante": ayudante[integrante.pk],
            "mantenimientos": mantenimientos[integrante.pk],
            "horas_mantenimiento": round(horas_mant[integrante.pk], 1),
        })
    filas.sort(key=lambda f: (f["numero_merito"] is None, f["numero_merito"] or 0, f["integrante"].apellido))
    return filas


def candidatos_numeros(anio):
    """A quiénes se les asigna número en el período: los que están en actividad y los que
    tuvieron número ese año o el anterior (Reserva, por ejemplo). Los dados de baja, no."""
    con_numero = NumeroMerito.objects.filter(anio__in=[anio, anio - 1]).values("integrante")
    return (Integrante.objects
            .filter(Q(estado__nombre_estado__in=ESTADOS_EN_ACTIVIDAD) | Q(pk__in=con_numero))
            .exclude(estado__nombre_estado="Baja")
            .select_related("rango", "estado").distinct()
            .order_by("rango__orden_jerarquico", "apellido", "nombre"))
