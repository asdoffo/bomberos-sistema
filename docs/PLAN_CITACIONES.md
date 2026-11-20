# Plan: módulo de Citaciones

Planificado el 14/11/2026 y actualizado con las respuestas del usuario. Todavía no está implementado.
Mock: `docs/mock_citacion.html` (abrir en el navegador). El selector de arriba muestra cada estado; la casilla «Ver como confidencial» muestra el aviso de reserva. Se ven la vista del bombero en el celular y la hoja imprimible.

Objetivo: reemplazar la citación en papel. El Jefe o un Graduado cita a un integrante para una tarea; la citación es la notificación escrita. El citado se notifica y puede avisar que no puede asistir. Cada citación queda vinculada al integrante, para ver después quiénes fueron citados y si cumplieron.

---

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Quién cita | Jefe y Graduado. El Personal de guardia **no** cita ni ve las citaciones de otros. |
| Anticipación | Se fija en cada citación, con un **mínimo de 24 h** (24 por defecto). |
| Aviso fuera de plazo | Se deja responder y queda **marcado «fuera de plazo»**. |
| Reemplazo | No lo aprueba la jefatura: alcanza con que el **reemplazante acepte su propia citación**. |
| No asistir sin justificar | **Queda registrado** en la citación y en la ficha del bombero (destacado), y cuenta en Estadísticas. No hay sanción automática. |
| Confidenciales | Existen (descargos, disciplinarias): solo las ven el **Jefe** y el citado. |
| Corrección de una cerrada | Se permite, con motivo y firma. |

---

## Qué tomamos del modelo en papel y qué se moderniza

| Papel | Sistema |
|---|---|
| «Sr.: …» | El citado se elige de la lista: legajo, rango, N° de mérito y grupo de guardia salen de su ficha. |
| «Sírvase presentarse en el cuartel el día … a las … horas para …» | Fecha y hora, lugar (el cuartel por defecto), **tipo de tarea** (catálogo) y detalle. Se agregan duración estimada, qué llevar y si es obligatoria. |
| «Comunicarlo con la debida anticipación y buscar el reemplazo según su cargo y grado» | Anticipación en horas (mínimo 24). El citado avisa desde el celular con motivo y **propone un reemplazo**, con la lista ordenada por el mismo rango primero. |
| «Señores de Jefatura: comunícole que no puedo asistir por …» | La respuesta «No puedo asistir», con su motivo y su firma. |
| Talón (motivo, nombre, N°, lugar, fecha) | Lo reemplaza el registro en el sistema. Para el circuito en papel queda un **talón de notificación recortable**, que el citado firma y queda en el cuartel. |
| (no existía) | **Cumplimiento**: después de la fecha se registra si asistió. Sirve para la ficha y para Estadísticas. |

---

## Circuito

```
EMITIDA ──(se notifica)──▶ NOTIFICADA ──▶ CONFIRMADA ──────────────────────┐
   │                            │                                           ├──(se registra asistencia)──▶ CERRADA
   │                            └──▶ NO PUEDE ASISTIR ──(reemplazante acepta)──▶ REEMPLAZADA (cerrada)
   └──────── ANULADA (Jefe o quien citó, con motivo, antes de cerrar)
```

| Paso | Quién | Qué queda registrado |
|---|---|---|
| Emitir | Jefe o Graduado. Las confidenciales, solo el Jefe. | Citó: nombre, legajo, fecha y hora, firma |
| Notificarse | El citado desde Mi perfil → «Me notifico» con su contraseña. **O** quien citó registra la entrega en papel, y el talón firmado queda en el cuartel. | Notificado: fecha y hora, y el medio (sistema o papel) |
| Responder | El citado: «Voy a asistir», o «No puedo asistir» con motivo y reemplazo propuesto (opcional). Puede cambiar la respuesta hasta la hora de la citación. | Respuesta, motivo, fecha y hora, horas de anticipación y marca **fuera de plazo** si avisó con menos de lo pedido |
| Reemplazo | Al proponer un reemplazante, el sistema le **genera su propia citación** (mismo día, hora y tarea, «en reemplazo de N° 42»). Si la **acepta**, la original queda **reemplazada** y se cierra sola. Si la **rechaza**, se avisa al citado original y a quien citó, y la original sigue en «no puede asistir». | Vínculo entre las dos citaciones, y quién aceptó o rechazó y cuándo |
| Registrar el cumplimiento | Jefe o Graduado (las confidenciales, solo el Jefe), después de la fecha | Resultado, quién lo registró y firma. Queda **cerrada**. |
| Corregir una cerrada | Jefe o Graduado (las confidenciales, solo el Jefe), con motivo | Firma `CITACION_CORRECCION` con los datos antes y después |
| Anular | Jefe o quien citó, con motivo, antes de cerrar | Quién anuló, cuándo y por qué |

**Resultados del cumplimiento:**
- Asistió.
- **No asistió, sin justificar.**
- No asistió, justificado (la jefatura lo justificó, con observación).
- Reemplazado (lo pone el sistema cuando el reemplazante acepta).

Acciones nuevas en `Firma.Accion`: `CITACION_EMITIDA`, `CITACION_NOTIFICADA`, `CITACION_RESPUESTA`, `CITACION_CUMPLIMIENTO`, `CITACION_CORRECCION` y `CITACION_ANULADA`.

### Citar a varios a la vez
El formulario permite elegir varios integrantes, o un grupo de guardia completo. Se crea **una citación por persona**, con su propio número, y todas quedan vinculadas a una misma **convocatoria**.

### Reemplazante sin usuario
Si el reemplazante no tiene usuario, no puede aceptar desde el sistema. En ese caso, quien citó registra la aceptación (con el talón en papel firmado).

---

## Datos

### `TipoCitacion` (catálogo editable desde el admin)
Valores iniciales:
- Guardia
- Capacitación
- Mantenimiento
- Reunión
- Acto / desfile
- Servicio especial
- Descargo / disciplinaria (`confidencial_por_defecto = True`)
- Otro

### `Convocatoria` (opcional, agrupa citaciones de la misma tarea)
- `fecha_hora`, `lugar`, `tipo`, `detalle`, `duracion_estimada`, `llevar`, `obligatoria`, `horas_anticipacion` y `cito`.

### `Citacion` (usa `CorrelativoAnual`: N° 42/2026)

**Citación**
- `integrante` (FK, `related_name="citaciones"`): el **vínculo con la ficha**.
- `convocatoria` (FK opcional).
- `fecha_hora`, `lugar` (por defecto «Cuartel Central — General Paz 358»), `tipo` (FK) y `detalle`.
- `duracion_estimada`, `llevar` y `obligatoria`.
- `horas_anticipacion`: entero, **mínimo 24** (validador), 24 por defecto.
- `confidencial`: booleano; solo el Jefe puede marcarlo.
- Vínculo opcional con la actividad: `capacitacion` o `mantenimiento` (FK).

**Estado**
- `estado`: emitida, notificada, confirmada, no puede asistir, reemplazada, cerrada o anulada.

**Sellos**
- `cito` y `fecha_emision`.
- `fecha_notificacion`, `medio_notificacion` (sistema o papel) y `registro_entrega`.

**Respuesta**
- `respuesta` (asiste o no puede), `motivo_no_asistencia` y `fecha_respuesta`.
- `fuera_de_plazo`: calculado y guardado, verdadero si `fecha_respuesta > fecha_hora − horas_anticipacion`.
- `reemplazo_propuesto` (FK a `Integrante`) y `citacion_reemplazo` (FK a `Citacion`, la que se generó).
- `en_reemplazo_de` (FK a `Citacion`, en la del reemplazante).

**Cumplimiento**
- `resultado`, `registro_resultado`, `fecha_resultado` y `observaciones`.

**Anulación**
- `anulo`, `fecha_anulacion` y `motivo_anulacion`.

---

## Permisos y visibilidad

Permisos nuevos:
- `add_citacion`
- `ver_todas_citacion`
- `registrar_cumplimiento_citacion`
- `corregir_citacion`
- `anular_citacion`
- `citacion_confidencial`

| | Jefe | Graduado | Estadística | Personal de guardia | Bombero / Aspirante / Invitado |
|---|---|---|---|---|---|
| Citar | ✔ | ✔ | | ✘ | ✘ |
| Ver todas (no confidenciales) | ✔ | ✔ | ✔ | ✘ | ✘ |
| Crear y ver confidenciales | ✔ | ✘ | ✘ | ✘ | ✘ |
| Ver y responder **las propias** (incluidas las confidenciales) | ✔ | ✔ | ✔ | ✔ | ✔ |
| Registrar cumplimiento | ✔ | ✔ | | | |
| Corregir una cerrada | ✔ | ✔ | | | |
| Anular | ✔ | quien citó | | | |

**Cuidado con los permisos:**
- `PERMISOS_GRADUADO` se arma a partir de `PERMISOS_GUARDIA`, pero aquí el Graduado tiene permisos que la guardia **no** tiene. Hay que agregárselos al Graduado aparte, sin sumarlos a `PERMISOS_GUARDIA`.
- Un integrante que es Bombero y además Personal de guardia (como el caso de Leyba) tampoco cita.

**Confidenciales:**
- Se excluyen en la consulta para quien no tiene `citacion_confidencial`, salvo el propio citado.
- No aparecen en listados, en la ficha vista por otros ni en exportaciones.
- En Estadísticas cuentan solo en los totales, sin detalle.

---

## Pantallas
1. **Nueva citación**:
   - integrante, o varios, o un grupo de guardia completo;
   - fecha y hora, lugar, tipo, detalle, duración y qué llevar;
   - obligatoria, anticipación (mínimo 24 h) y confidencial (solo el Jefe);
   - vínculo opcional a una capacitación o mantenimiento.
2. **Listado** (Jefe, Graduado, Estadística):
   - filtros por estado, tipo, integrante y fechas;
   - pestañas: «Sin notificar», «Avisaron que no pueden», «Reemplazo rechazado», «Por registrar asistencia» (ya vencidas) y «Cerradas»;
   - las respuestas fuera de plazo llevan su marca.
3. **Detalle e impresión** (ver el mock):
   - membrete, N°, estado y aviso de confidencial;
   - citado, «sírvase presentarse», el texto de anticipación y reemplazo;
   - respuesta (con la marca de fuera de plazo) y cumplimiento;
   - sellos;
   - **talón de notificación recortable**.
4. **Mis citaciones** (Mi perfil, pensado para el celular):
   - tarjeta con día, hora, lugar y tarea;
   - botones «Me notifico», «Voy a asistir» y «No puedo asistir» (con motivo y reemplazo, ordenando la lista por el mismo rango primero);
   - las citaciones de reemplazo dicen «en reemplazo de …» y tienen «Acepto» o «No puedo».
5. **Aviso en el menú**:
   - para el citado: citaciones sin notificar o sin responder, y reemplazos pedidos;
   - para quien citó: avisos de que no pueden y reemplazos rechazados.
6. **Ficha del bombero**: pestaña «Citaciones», con el historial y un resumen (citado, asistió, justificó, reemplazado, **no asistió sin justificar** destacado y respuestas fuera de plazo).
7. **Compartir por WhatsApp** (opcional): un enlace `wa.me` con un texto y el enlace a la citación. No se usa en las confidenciales.

---

## Relación con Estadísticas (`docs/PLAN_ESTADISTICAS.md`)
**Por bombero**
- Citaciones recibidas.
- Porcentaje de cumplimiento.
- Inasistencias sin justificar.
- Avisos fuera de plazo.
- Anticipación promedio.
- Veces que fue reemplazante.

**Institucional**
- Citaciones por tipo y por mes.
- Porcentaje notificadas.
- Cumplimiento por tipo y por grupo de guardia.
- Porcentaje de reemplazos aceptados.

---

## Preguntas abiertas
Ninguna por ahora. Al implementar, se puede ajustar:
- la lista inicial de tipos de citación;
- el texto fijo de la hoja.

---

## Tests a incluir
- Solo Jefe y Graduado citan. La guardia no cita ni ve las de otros. Cada uno ve las suyas.
- Las confidenciales: solo las crea y las ve el Jefe (y el citado), no salen en listados, exportaciones ni la ficha vista por otros, y el Graduado no las ve aunque tenga `ver_todas_citacion`.
- `horas_anticipacion` menor a 24 no se puede guardar.
- La respuesta tardía se guarda con `fuera_de_plazo = True`.
- Al proponer un reemplazo se genera la citación del reemplazante. Si la acepta, la original queda reemplazada y cerrada. Si la rechaza, la original sigue abierta y se avisa.
- No se registra el cumplimiento antes de la fecha. «No asistió sin justificar» aparece en la ficha.
- La corrección de una cerrada exige motivo y deja la firma con el antes y el después.
- Una citación grupal crea una citación por persona, con su propio número.
