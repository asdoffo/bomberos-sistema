from django.db import migrations, models

# Catálogo provisorio -> "Códigos de intervención" del cuartel (lista del tutor, 03/11).
# "Falsa alarma" y "Otro" no tienen equivalente: se borran si nadie los usó (si los usó un parte, quedan).
TIPOS = {
    "Incendio": ("01", "Incendio", "incendio"),
    "Accidente de tránsito": ("02", "Accidentes", "rodados,damnificados"),
    "Emergencia médica / auxilio": ("03", "Salvataje", "damnificados"),
    "Rescate": ("04", "Rescate", "damnificados"),
    "Materiales peligrosos": ("04", "Rescate", "damnificados"),
    "Servicio especial": ("05", "Mantenimiento / tareas generales", ""),
}
MOTIVOS = {
    "Vivienda": ("101", "Fuego en vivienda"),
    "Vehículo": ("103", "Fuego en rodados"),
    "Comercio / industria": ("104", "Fuego en comercio"),
    "Pastizal / campo": ("108", "Quema de pastizales, sembrados y fardos"),
    "Colisión": ("111", "Colisión generalizada, vuelco"),
    "Vuelco": ("111", "Colisión generalizada, vuelco"),
    "Colisión auto-colectivo": ("111", "Colisión generalizada, vuelco"),
    "Colisión moto-bicicleta": ("111", "Colisión generalizada, vuelco"),
    "Colisión contra objeto fijo": ("111", "Colisión generalizada, vuelco"),
    "Colisión con animal": ("111", "Colisión generalizada, vuelco"),
    "Despiste / salida de ruta": ("111", "Colisión generalizada, vuelco"),
    "Atropello a peatón": ("112", "Colisión auto - peatón"),
    "Colisión auto-bicicleta": ("113", "Colisión auto - bicicleta"),
    "Colisión auto-moto": ("114", "Colisión auto - moto / camión - moto / moto - moto"),
    "Colisión moto-moto": ("114", "Colisión auto - moto / camión - moto / moto - moto"),
    "Colisión moto-camión": ("114", "Colisión auto - moto / camión - moto / moto - moto"),
    "Colisión auto-camión": ("115", "Colisión auto - camión"),
    "Colisión auto-auto": ("118", "Colisión auto - auto"),
    "Caída de moto": ("119", "Caída de moto"),
    "Persona atrapada": ("130", "Rescate de persona"),
    "Animal": ("134", "Rescate de animales"),
    "Escape de gas": ("135", "Incidentes con materiales peligrosos (escape de gas, derrame, líquido combustible)"),
    "Derrame de combustible": ("135", "Incidentes con materiales peligrosos (escape de gas, derrame, líquido combustible)"),
    "Árbol caído": ("140", "Tareas varias (carga de combustible, compras varias, reparaciones, etc.)"),
}


def _viejos_sin_codigo(modelo, descripciones):
    """Primero se les saca código y nombre: los provisorios usaban los mismos números que los oficiales."""
    viejos = []
    for fila in modelo.objects.filter(descripcion__in=descripciones):
        viejos.append((fila, fila.descripcion))
        fila.codigo, fila.descripcion = "", f"(provisorio {fila.pk})"
        fila.save(update_fields=["codigo", "descripcion"])
    return viejos


def convertir(apps, schema_editor):
    Tipo, Motivo, Parte = (apps.get_model("partes", m) for m in ("TipoSiniestro", "Motivo", "ParteSiniestro"))
    for fila, descripcion in _viejos_sin_codigo(Tipo, TIPOS):
        codigo, nueva, secciones = TIPOS[descripcion]
        oficial, _ = Tipo.objects.get_or_create(codigo=codigo, defaults={"descripcion": nueva, "secciones": secciones})
        if fila.se_cobra and not oficial.se_cobra:
            oficial.se_cobra = True
            oficial.save(update_fields=["se_cobra"])
        Parte.objects.filter(tipo=fila).update(tipo=oficial)
        fila.delete()
    for fila, descripcion in _viejos_sin_codigo(Motivo, MOTIVOS):
        codigo, nueva = MOTIVOS[descripcion]
        oficial, _ = Motivo.objects.get_or_create(codigo=codigo, defaults={"descripcion": nueva})
        Parte.objects.filter(motivo=fila).update(motivo=oficial)
        fila.delete()
    Tipo.objects.filter(descripcion="Falsa alarma", partesiniestro__isnull=True).delete()
    Motivo.objects.filter(descripcion="Otro", partesiniestro__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('partes', '0018_planillas_medicos_calles'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='firmamedico',
            name='una_firma_por_medico_y_fecha',
        ),
        migrations.AlterField(
            model_name='tiposiniestro',
            name='codigo',
            field=models.CharField(blank=True, help_text='Código de la planilla (ej. 02). Se muestra adelante: 02 - Accidentes.', max_length=5, verbose_name='código'),
        ),
        migrations.DeleteModel(
            name='FirmaMedico',
        ),
        migrations.RunPython(convertir, migrations.RunPython.noop),
    ]
