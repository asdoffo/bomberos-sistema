"""Nombres de los roles (grupos de Django) y cuál manda cuando alguien tiene varios.

Los permisos de cada rol están en management/commands/cargar_catalogos.py (ROLES). Acá solo
lo que necesita el resto del código: la duración de la sesión (sesion.py) y quién completa partes.
"""

JEFE = "Jefe"
ESTADISTICA = "Estadística"
GUARDIA = "Personal de guardia"
GRADUADO = "Graduado"
BOMBERO = "Bombero"
ASPIRANTE = "Aspirante"
INVITADO = "Invitado"

# Si un usuario tiene varios roles, vale el primero de esta lista (decisión del usuario, 10/11):
# ej. Leyba es Bombero y Personal de guardia, y su sesión se rige por la de guardia.
PRIORIDAD = [GUARDIA, JEFE, ESTADISTICA, GRADUADO, BOMBERO, ASPIRANTE, INVITADO]


def rol_principal(user):
    """El rol que manda para este usuario, o None si no tiene ninguno de la lista (ej. admin)."""
    if not getattr(user, "is_authenticated", False):
        return None
    nombres = set(user.groups.values_list("name", flat=True))
    return next((rol for rol in PRIORIDAD if rol in nombres), None)


def puede_completar(user):
    """Completar un parte (datos del lugar, damnificados, negativas, fotos) lo puede hacer cualquier
    integrante logueado, menos el Invitado, que por ahora solo ve."""
    return rol_principal(user) != INVITADO
