"""Carga sin señal: combina lo que manda un celular con lo que ya está en el servidor.

Completar manda dos fotos del formulario: cómo estaba al abrirlo (campo oculto
``_base``, que arma el navegador) y cómo quedó al guardar (los datos de siempre).
Solo se aplica lo que cambió en ese equipo, así no se pisa lo que otro celular
sincronizó mientras tanto:

- Listas (personal que concurrió, lo dañado): se suman los agregados y se sacan
  los quitados.
- Filas de rodados y damnificados: se identifican por su UUID. Las nuevas traen
  un UUID generado en el celular (``<prefijo>-<n>-uuid_local``), así reenviar
  el mismo guardado no las duplica.
- Datos sueltos (horarios, km, observaciones...): gana el último que guarda, y
  se avisa si otro equipo lo había cambiado.
"""
import json
import uuid

from django import forms
from django.http import QueryDict


def leer_base(texto):
    """``_base`` es una lista JSON de pares [nombre, valor]. None si no vino o está roto."""
    if not texto:
        return None
    try:
        pares = json.loads(texto)
        base = QueryDict(mutable=True)
        for nombre, valor in pares:
            base.appendlist(str(nombre), str(valor))
        return base
    except (ValueError, TypeError):
        return None


def es_uuid(texto):
    try:
        uuid.UUID(texto)
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _sin_vacios(valores):
    return [v for v in valores if v != ""]


def valores_actuales(campo):
    """Lo que mandaría hoy el navegador para este campo de un form sin datos."""
    widget = campo.field.widget
    valor = campo.value()
    if isinstance(widget, forms.CheckboxInput):
        return ["on"] if widget.check_test(valor) else []
    if isinstance(widget, forms.FileInput):
        return []
    texto = widget.format_value(valor)
    if texto is None:
        return []
    if isinstance(texto, (list, tuple)):
        return _sin_vacios([str(v) for v in texto])
    return _sin_vacios([str(texto)])


def _es_lista(campo):
    return isinstance(campo.field, (forms.MultipleChoiceField, forms.ModelMultipleChoiceField))


def _iguales(campo, x, y):
    """Compara por valor y no por texto ("10:00" y "10:00:00" son la misma hora)."""
    if x == y:
        return True
    if _es_lista(campo):
        return set(x) == set(y)
    try:
        return campo.field.to_python(x[0] if x else "") == campo.field.to_python(y[0] if y else "")
    except forms.ValidationError:
        return False


def _combinar_valor(campo, actual, base, enviado, avisos, contexto):
    if enviado == base:
        return actual  # este equipo no lo tocó: queda lo del servidor
    if _es_lista(campo):
        quitados = set(base) - set(enviado)
        return ([v for v in actual if v not in quitados]
                + [v for v in enviado if v not in base and v not in actual])
    if not _iguales(campo, actual, base) and not _iguales(campo, actual, enviado):
        avisos.append(f"{contexto}{campo.label}: lo había cambiado otro equipo y quedó lo de este guardado.")
    return enviado


def _filas(datos, prefijo):
    """(cantidad de filas existentes, [ {campo: [valores]} por fila ]) de un formset enviado."""
    try:
        total = min(int(datos.get(f"{prefijo}-TOTAL_FORMS", 0)), 1000)
        iniciales = int(datos.get(f"{prefijo}-INITIAL_FORMS", 0))
    except ValueError:
        return 0, []
    filas = []
    for i in range(total):
        inicio = f"{prefijo}-{i}-"
        filas.append({clave[len(inicio):]: _sin_vacios(datos.getlist(clave))
                      for clave in datos if clave.startswith(inicio)})
    return iniciales, filas


def _primero(fila, campo):
    return (fila.get(campo) or [""])[0]


def _combinar_formset(formset, datos, base, avisos, etiqueta):
    """Devuelve (filas combinadas, cuántas son existentes, {índice: uuid} de las nuevas)."""
    base_iniciales, base_filas = _filas(base, formset.prefix)
    iniciales, filas = _filas(datos, formset.prefix)
    en_base = {_primero(f, "id"): f for f in base_filas[:base_iniciales] if _primero(f, "id")}
    actuales = {str(form.instance.pk): form for form in formset.initial_forms}

    enviadas, nuevas = {}, []
    for i, fila in enumerate(filas):
        if i < iniciales:
            if _primero(fila, "id"):
                enviadas[_primero(fila, "id")] = fila
        elif _primero(fila, "uuid_local") in actuales:
            enviadas[_primero(fila, "uuid_local")] = fila  # ya había llegado en un envío anterior
        elif not fila.get("DELETE"):
            nuevas.append(fila)

    resultado = []
    for pk, form in actuales.items():
        campos = [c for c in form if c.name not in ("id", "DELETE")]
        fila = {"id": [pk], **{c.name: valores_actuales(c) for c in campos}}
        enviada = enviadas.get(pk)
        if enviada is not None:
            if enviada.get("DELETE"):
                fila["DELETE"] = ["on"]
            else:
                original = en_base.get(pk)
                for c in campos:
                    actual = fila[c.name]
                    fila[c.name] = _combinar_valor(
                        c, actual, original.get(c.name, []) if original is not None else actual,
                        enviada.get(c.name, []), avisos, etiqueta(form))
        resultado.append(fila)

    for pk, enviada in enviadas.items():
        if pk not in actuales and not enviada.get("DELETE") and enviada != en_base.get(pk):
            avisos.append(f"Un {formset.model._meta.verbose_name} que se modificó acá lo había "
                          "quitado otro equipo: esos cambios no se guardaron.")

    uuids = {}
    for fila in nuevas:
        local = _primero(fila, "uuid_local")
        if es_uuid(local):
            uuids[len(resultado)] = local
        resultado.append({k: v for k, v in fila.items() if k not in ("id", "DELETE", "uuid_local")})
    return resultado, len(actuales), uuids


def combinar(datos, base, formularios, formsets):
    """Arma el POST que hubiera mandado el celular si hubiera tenido los datos al día.

    ``formularios`` y ``formsets`` se construyen SIN datos (muestran el estado
    actual del servidor). ``formsets`` es una lista de (formset, etiqueta) donde
    etiqueta(form) describe la fila en los avisos.
    Devuelve (QueryDict combinado, {prefijo: {índice: uuid}}, avisos).
    """
    combinado = QueryDict(mutable=True)
    avisos = []
    for form in formularios:
        for campo in form:
            combinado.setlist(campo.html_name, _combinar_valor(
                campo, valores_actuales(campo), _sin_vacios(base.getlist(campo.html_name)),
                _sin_vacios(datos.getlist(campo.html_name)), avisos, ""))

    uuids = {}
    for formset, etiqueta in formsets:
        filas, iniciales, nuevos = _combinar_formset(formset, datos, base, avisos, etiqueta)
        p = formset.prefix
        combinado[f"{p}-TOTAL_FORMS"] = str(len(filas))
        combinado[f"{p}-INITIAL_FORMS"] = str(iniciales)
        combinado[f"{p}-MIN_NUM_FORMS"] = "0"
        combinado[f"{p}-MAX_NUM_FORMS"] = "1000"
        for i, fila in enumerate(filas):
            for campo, valores in fila.items():
                combinado.setlist(f"{p}-{i}-{campo}", valores)
        uuids[p] = nuevos
    return combinado, uuids, avisos


def renumerar_rodados(datos, avisos, rodados="rodados", damnificados="damnificados", maximo=4):
    """Si un rodado nuevo de este celular choca con el número de uno que ya cargó
    otro equipo, pasa al primer número libre (y sus damnificados nuevos con él)."""
    iniciales, filas = _filas(datos, rodados)
    usados = {_primero(f, "rodado_numero") for f in filas[:iniciales] if not f.get("DELETE")}
    cambios = {}
    for i in range(iniciales, len(filas)):
        numero = _primero(filas[i], "rodado_numero")
        if not numero:
            continue
        if numero in usados:
            libres = [str(n) for n in range(1, maximo + 1) if str(n) not in usados]
            if not libres:
                continue  # lo frena la validación del formulario
            cambios[numero] = libres[0]
            datos[f"{rodados}-{i}-rodado_numero"] = libres[0]
            avisos.append(f"El rodado N° {numero} cargado en este equipo quedó como N° {libres[0]} "
                          f"porque otro equipo ya había cargado un N° {numero}.")
            numero = libres[0]
        usados.add(numero)
    if cambios:
        iniciales_d, filas_d = _filas(datos, damnificados)
        for i in range(iniciales_d, len(filas_d)):
            numero = _primero(filas_d[i], "rodado_numero")
            if numero in cambios:
                datos[f"{damnificados}-{i}-rodado_numero"] = cambios[numero]
