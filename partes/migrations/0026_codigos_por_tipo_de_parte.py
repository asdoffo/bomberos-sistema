"""Catálogo de códigos de intervención de la planilla del 18/11/2026.

Agrega:
- TipoSiniestro.parte: en qué planilla se ofrece el tipo (SINIESTRO, GENERAL, MANTENIMIENTO,
  CAPACITACION, ACTIVIDAD COMERCIAL: la columna PARTE de la planilla).
- Motivo.detalle: el texto de ayuda de la columna Detalle.
- Motivo.activo: los códigos "Reserva" se cargan apagados, así no aparecen en los desplegables.

Y rehace el catálogo de cero (decisión del usuario, 18/11): la planilla nueva le corre el número
a todo el grupo Rescate y le cambia el significado a varios códigos, así que emparejar lo viejo
con lo nuevo a mano dejaría mezcla. Un tipo o un motivo que algún parte esté usando no se puede
borrar (los FK son PROTECT): esos se reapuntan al equivalente nuevo buscándolo por código y, si
el código no está, por descripción. Si no hay equivalente, el viejo queda tal cual, apagado.
"""
from django.db import migrations, models

from partes.codigos_intervencion import CODIGOS_INTERVENCION


def _apartar(modelo):
    """Libera los valores unique de lo viejo: la planilla nueva reusa los mismos códigos y
    descripciones, así que no se puede crear el catálogo nuevo mientras lo viejo esté en la tabla.
    Devuelve {pk: (código, descripción)} para después poder emparejar."""
    anteriores = {}
    for fila in modelo.objects.all():
        anteriores[fila.pk] = (fila.codigo, fila.descripcion)
        fila.codigo, fila.descripcion = "", f"(planilla anterior {fila.pk})"
        fila.save(update_fields=["codigo", "descripcion"])
    return anteriores


def _equivalente(nuevos, codigo, descripcion):
    """El reemplazo de una fila vieja: primero por código, si no por descripción."""
    return nuevos["codigo"].get(codigo) or nuevos["descripcion"].get(descripcion.strip().lower())


def recargar(apps, schema_editor):
    Tipo = apps.get_model("partes", "TipoSiniestro")
    Motivo = apps.get_model("partes", "Motivo")
    ParteSiniestro = apps.get_model("partes", "ParteSiniestro")

    if not Tipo.objects.exists() and not Motivo.objects.exists():
        # Base nueva (o la de los tests): no hay nada que reconciliar y sembrar los catálogos no
        # es tarea de las migraciones, sino de `manage.py cargar_catalogos`.
        return

    tipos_viejos = _apartar(Tipo)
    motivos_viejos = _apartar(Motivo)

    tipos_nuevos = {"codigo": {}, "descripcion": {}}
    motivos_nuevos = {"codigo": {}, "descripcion": {}}
    for codigo, descripcion, parte, secciones, se_cobra, motivos in CODIGOS_INTERVENCION:
        tipo = Tipo.objects.create(codigo=codigo, descripcion=descripcion, parte=parte,
                                   secciones=secciones, se_cobra=se_cobra)
        tipos_nuevos["codigo"][codigo] = tipo
        tipos_nuevos["descripcion"][descripcion.lower()] = tipo
        for codigo_motivo, descripcion_motivo, detalle, activo in motivos:
            motivo = Motivo.objects.create(codigo=codigo_motivo, descripcion=descripcion_motivo,
                                           detalle=detalle, activo=activo)
            motivo.tipos.add(tipo)
            motivos_nuevos["codigo"][codigo_motivo] = motivo
            motivos_nuevos["descripcion"][descripcion_motivo.lower()] = motivo

    # Los partes que apuntaban a una fila vieja pasan a la nueva equivalente.
    for pk, (codigo, descripcion) in tipos_viejos.items():
        nuevo = _equivalente(tipos_nuevos, codigo, descripcion)
        if nuevo:
            ParteSiniestro.objects.filter(tipo_id=pk).update(tipo=nuevo)
    for pk, (codigo, descripcion) in motivos_viejos.items():
        nuevo = _equivalente(motivos_nuevos, codigo, descripcion)
        if nuevo:
            ParteSiniestro.objects.filter(motivo_id=pk).update(motivo=nuevo)

    # Lo viejo que ya no usa nadie se va; lo que algún parte todavía usa (porque no tenía
    # equivalente en la planilla nueva) se queda con su nombre original y apagado.
    Tipo.objects.filter(pk__in=tipos_viejos, partesiniestro__isnull=True).delete()
    Motivo.objects.filter(pk__in=motivos_viejos, partesiniestro__isnull=True).delete()
    for fila in Tipo.objects.filter(pk__in=tipos_viejos):
        fila.codigo, fila.descripcion = tipos_viejos[fila.pk]
        fila.save(update_fields=["codigo", "descripcion"])
    for fila in Motivo.objects.filter(pk__in=motivos_viejos):
        fila.codigo, fila.descripcion = motivos_viejos[fila.pk]
        fila.activo = False
        fila.save(update_fields=["codigo", "descripcion", "activo"])


class Migration(migrations.Migration):

    dependencies = [
        ("partes", "0025_cambios_roles_y_departamentos"),
    ]

    operations = [
        migrations.AddField(
            model_name="tiposiniestro",
            name="parte",
            field=models.CharField(
                choices=[("siniestro", "Parte de siniestro"), ("general", "Parte general"),
                         ("mantenimiento", "Mantenimiento"), ("capacitacion", "Capacitación"),
                         ("actividad_comercial", "Actividad comercial")],
                default="siniestro",
                help_text="En qué planilla se ofrece este tipo (columna PARTE de los códigos de intervención).",
                max_length=20, verbose_name="tipo de parte"),
        ),
        migrations.AddField(
            model_name="motivo",
            name="detalle",
            field=models.CharField(
                blank=True,
                help_text="Texto de ayuda: ejemplos o aclaraciones que se muestran al elegir este motivo.",
                max_length=200),
        ),
        migrations.AddField(
            model_name="motivo",
            name="activo",
            field=models.BooleanField(
                default=True,
                help_text="Desmarcado, el motivo no se ofrece al cargar un parte "
                          "(los partes viejos lo siguen mostrando)."),
        ),
        migrations.RunPython(recargar, migrations.RunPython.noop),
    ]
