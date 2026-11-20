"""Categorías de las unidades, para ordenar la lista de Móviles y el selector de la salida (10/11).

Van en el código (no en la base) por decisión del usuario: una categoría nueva se agrega acá. Un tipo
de vehículo que no esté en ninguna cae en "Otras".
"""
OTRAS = "Otras"
CATEGORIAS = [
    ("Ambulancias", ["Ambulancia", "Unidad sanitaria"]),
    ("Rescate", ["Escalante/Hidroelevador", "Unidad de Rescate"]),
    ("Autobombas livianas", ["Autobomba Liviana <= a 1500 lts", "Ataque rápido"]),
    ("Autobombas pesadas", ["Autobomba Pesada > a 3000 hasta 10000 lts", "Autobomba Cisterna > a 10000 lts",
                            "Cisterna", "Autobomba"]),
    ("Transporte", ["Unidad de Transporte Carga/Personal"]),
    (OTRAS, ["Embarcaciones", "Camioneta de apoyo", "Otros"]),
]
_CATEGORIA_DE = {tipo.casefold(): nombre for nombre, tipos in CATEGORIAS for tipo in tipos}
_ORDEN = [nombre for nombre, _ in CATEGORIAS]


def categoria(vehiculo):
    tipo = vehiculo.tipo_vehiculo.descripcion if vehiculo.tipo_vehiculo_id else ""
    return _CATEGORIA_DE.get(tipo.strip().casefold(), OTRAS)


def orden_legajo(vehiculo):
    """Primero los N° (3, 4, 5... 62) y después los que tienen texto ("4-B2011", "CUO")."""
    legajo = vehiculo.legajo.strip()
    return (0, int(legajo), "") if legajo.isdigit() else (1, 0, legajo.casefold())


def agrupar(vehiculos):
    """[(categoría, [vehículos ordenados])], en el orden de CATEGORIAS y sin categorías vacías."""
    grupos = {nombre: [] for nombre in _ORDEN}
    for vehiculo in vehiculos:
        grupos[categoria(vehiculo)].append(vehiculo)
    return [(nombre, sorted(grupos[nombre], key=orden_legajo)) for nombre in _ORDEN if grupos[nombre]]


def opciones_agrupadas(vehiculos, vacio):
    """Choices con <optgroup> por categoría para un ModelChoiceField de vehículos."""
    return [("", vacio)] + [(nombre, [(v.pk, str(v)) for v in lista]) for nombre, lista in agrupar(vehiculos)]
