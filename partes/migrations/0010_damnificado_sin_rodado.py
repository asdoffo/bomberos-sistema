import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


def parte_desde_el_rodado(apps, schema_editor):
    Damnificado = apps.get_model("partes", "Damnificado")
    for damnificado in Damnificado.objects.select_related("vehiculo_siniestrado"):
        damnificado.parte_id = damnificado.vehiculo_siniestrado.parte_id
        damnificado.save(update_fields=["parte"])


# Con damnificados sin rodado, estos tipos también los piden. Solo se cambia si
# las secciones siguen como las dejó cargar_catalogos (no se pisa lo editado en el admin).
SECCIONES = {
    "Rescate": ("", "damnificados"),
    "Emergencia médica / auxilio": ("", "damnificados"),
    "Materiales peligrosos": ("rodados", "rodados,damnificados"),
}


def secciones_con_damnificados(apps, schema_editor):
    TipoSiniestro = apps.get_model("partes", "TipoSiniestro")
    for descripcion, (antes, despues) in SECCIONES.items():
        TipoSiniestro.objects.filter(descripcion=descripcion, secciones=antes).update(secciones=despues)


class Migration(migrations.Migration):

    dependencies = [
        ("partes", "0009_tipo_y_llamado"),
    ]

    operations = [
        migrations.AddField(
            model_name="damnificado",
            name="parte",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name="damnificados", to="partes.partesiniestro"),
        ),
        migrations.RunPython(parte_desde_el_rodado, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="damnificado",
            name="parte",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                    related_name="damnificados", to="partes.partesiniestro"),
        ),
        migrations.AlterField(
            model_name="damnificado",
            name="vehiculo_siniestrado",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name="damnificados", to="partes.vehiculosiniestrado",
                                    verbose_name="rodado"),
        ),
        migrations.AlterField(
            model_name="damnificado",
            name="rol",
            field=models.CharField(blank=True, choices=[
                ("propietario", "Propietario"), ("conductor", "Conductor"),
                ("acompanante", "Acompañante"), ("peaton", "Peatón")], max_length=15),
        ),
        migrations.AlterField(
            model_name="vehiculosiniestrado",
            name="rodado_numero",
            field=models.PositiveSmallIntegerField(validators=[
                django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(10)]),
        ),
        migrations.RunPython(secciones_con_damnificados, migrations.RunPython.noop),
    ]
