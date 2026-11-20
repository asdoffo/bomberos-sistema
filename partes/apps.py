from django.apps import AppConfig


class PartesConfig(AppConfig):
    name = 'partes'
    verbose_name = 'Partes de siniestro'

    def ready(self):
        from . import sesion  # noqa: F401  registra la señal de inicio de turno
