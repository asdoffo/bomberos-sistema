"""Controles de los datos que se tipean en los formularios (DNI, edad, teléfono, patente, nombres).

Están en los modelos, así valen en las pantallas, en la sincronización del celular y en el admin.
Los formularios además limpian lo que la gente suele escribir distinto (puntos del DNI,
patente en minúsculas o con espacios) y le dan al celular el teclado que corresponde.
"""
import re

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, RegexValidator

EDAD_MAXIMA = 120

dni = RegexValidator(r"^\d{7,8}$", "El DNI tiene que tener 7 u 8 números, sin puntos ni letras.")

edad = MaxValueValidator(EDAD_MAXIMA, f"La edad tiene que estar entre 0 y {EDAD_MAXIMA} años.")

nombre = RegexValidator(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ' .,-]+$",
                        "Solo letras, espacios y la coma entre apellido y nombre.")

# Patentes argentinas: AAA123 (hasta 2016), AA123AA (Mercosur), 123AAA y A123AAA (motos).
patente = RegexValidator(r"^([A-Z]{3}\d{3}|[A-Z]{2}\d{3}[A-Z]{2}|\d{3}[A-Z]{3}|[A-Z]\d{3}[A-Z]{3})$",
                         "Patente inválida. Ejemplos: AB123CD o ABC123.")


def telefono(valor):
    if not re.fullmatch(r"\d+( \d+)?", valor) or not 6 <= len(re.sub(r"\D", "", valor)) <= 13:
        raise ValidationError("Teléfono inválido: solo números.")


# Teléfonos argentinos: característica sin el 0 + número sin el 15 = 10 dígitos.
CARACTERISTICA_LOCAL = "353"  # Villa María
DIGITOS_TELEFONO = 10


def separar_telefono(guardado):
    """'353 4123456' → ('353', '4123456'). Lo cargado antes sin espacio queda todo en el número."""
    if " " in (guardado or ""):
        caracteristica, numero = guardado.split(" ", 1)
        return caracteristica, numero
    return CARACTERISTICA_LOCAL, guardado or ""


# Qué control y qué teclado corresponde a cada campo, por nombre (se repiten en varios modelos).
CAMPOS_DNI = {"dni", "dni_firmante", "testigo_dni", "retira_dni"}
CAMPOS_EDAD = {"edad", "edad_firmante"}
CAMPOS_TELEFONO = {"telefono", "telefono_origen"}
CAMPOS_NOMBRE = {"apellido_nombre", "damnificado_nombre", "nombre_firmante", "testigo_nombre"}
CAMPOS_PATENTE = {"dominio"}


def limpiar(nombre_campo, valor):
    """Lo que la gente suele escribir distinto: '30.123.456' → '30123456', 'ab 123 cd' → 'AB123CD'."""
    if not isinstance(valor, str):
        return valor
    if nombre_campo in CAMPOS_DNI:
        return re.sub(r"[.\s-]", "", valor)
    if nombre_campo in CAMPOS_PATENTE:
        return re.sub(r"[\s-]", "", valor).upper()
    if nombre_campo in CAMPOS_TELEFONO:
        return re.sub(r"[\s()-]", "", valor)
    if nombre_campo in CAMPOS_NOMBRE:
        return " ".join(valor.split())
    return valor.strip()


def atributos_html(nombre_campo):
    """Teclado y límites para el navegador (en el celular abre el teclado numérico, etc.)."""
    if nombre_campo in CAMPOS_DNI:
        return {"inputmode": "numeric", "pattern": r"[0-9]{7,8}", "maxlength": "8",
                "title": "7 u 8 números"}
    if nombre_campo in CAMPOS_EDAD:
        return {"min": "0", "max": str(EDAD_MAXIMA), "inputmode": "numeric"}
    if nombre_campo in CAMPOS_TELEFONO:
        return {"type": "tel", "inputmode": "numeric", "pattern": r"[0-9]{6,13}",
                "title": "Solo números"}
    if nombre_campo in CAMPOS_NOMBRE:
        return {"autocapitalize": "words"}
    if nombre_campo in CAMPOS_PATENTE:
        return {"autocapitalize": "characters", "maxlength": "9", "placeholder": "AB123CD"}
    return {}
