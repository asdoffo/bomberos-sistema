# Plan: Tareas pendientes de mantenimiento

Planificado el 14/11/2026. Todavía no está implementado.
Mock: `docs/mock_tareas_pendientes.html` (abrir en el navegador). Tiene 4 pantallas: tablero, detalle, reportar desde el celular y la vinculación al cargar un mantenimiento.

Objetivo: llevar la lista de lo que falta hacer en los móviles y en el cuartel. Cada tarea tiene quién la reportó, un encargado, un estado y una descripción. Hoy lo pendiente queda como texto libre en «qué quedó pendiente» del mantenimiento, y así no se puede asignar, seguir ni cerrar.

---

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Quién reporta | **Desde la guardia para arriba**: Personal de guardia, Graduado y Jefe. Bombero, Aspirante e Invitado no. |
| Quién asigna el encargado | **Jefe o Graduado**. |
| Encargados | Uno o **varios integrantes** (no un taller externo). Cualquiera de ellos puede trabajarla y resolverla. |
| Cierre | Alcanza con que **un encargado** la marque resuelta. No hay verificación aparte. |
| Resolución desde un mantenimiento | Si al cargar un mantenimiento se tilda la tarea, queda **resuelta directamente**, sin importar quién lo cargó. |
| Estadística | **Ve** todas las tareas (solo lectura). |
| Móvil fuera de servicio | **Se maneja aparte** (`Vehiculo.en_servicio`). La tarea solo marca «afecta la operatividad» como dato y para destacarla. |
| Tareas periódicas automáticas | **Segunda etapa**. |

---

## Relación con lo que ya existe

```
Se reporta un problema ─▶ TAREA PENDIENTE ─▶ Jefe/Graduado asigna encargado ─▶ se trabaja
(salida, guardia, revisión)       │                                               │
                                  ├── necesita repuestos ─▶ ORDEN DE COMPRA (plan aparte)
                                  └──────────── se resuelve con ─▶ MANTENIMIENTO (ya existe)
```

- **Mantenimiento** es el registro de un trabajo hecho, con horas, participantes y costos. **Tarea** es lo que falta hacer.
- Relación de muchos a muchos: un mantenimiento puede resolver varias tareas, y una tarea puede llevar varios mantenimientos.

---

## Estados

```
REPORTADA ─(Jefe/Graduado asigna encargados)─▶ ASIGNADA ─▶ EN CURSO ─▶ RESUELTA
                                                  │   ▲
                                                  ▼   │
                                               EN ESPERA (repuesto / taller / presupuesto / otro)
       └───────── DESCARTADA (Jefe o Graduado, con motivo: duplicada, no corresponde…)
```

| Transición | Quién |
|---|---|
| Reportar | Guardia, Graduado, Jefe |
| Asignar, agregar o quitar encargados; cambiar prioridad o fecha límite | Jefe, Graduado |
| En curso ↔ En espera | Cualquiera de los encargados, Jefe, Graduado |
| Resolver | Cualquiera de los **encargados**, Jefe, Graduado. También se resuelve sola al tildarla en un mantenimiento, sin importar quién lo cargue. |
| Descartar | Jefe, Graduado (con motivo) |
| Reabrir una resuelta | Jefe, Graduado (con motivo; vuelve a Asignada) |
| Comentar y agregar fotos | Quien puede ver la tarea |

- **Al resolver**: se elige el mantenimiento que la resolvió, o se anota qué se hizo si fue algo menor que no amerita cargar un mantenimiento.
- **En espera**: se indica el motivo (repuesto, taller, presupuesto u otro) y, si hay una, la orden de compra vinculada.

---

## Datos

### `TareaPendiente` (usa `CorrelativoAnual`: T-15/2026)

**Qué**
- `fecha` del reporte.
- `vehiculo` (FK opcional) **o** `elemento` (texto: edificio, bomba del cuartel, equipo ERA N° 4…). Uno de los dos es obligatorio, igual que en `Mantenimiento`.
- `categoria`: FK a `CategoriaTarea`, un catálogo editable con mecánica, eléctrica, carrocería, bomba / hidráulica, equipos ERA, comunicaciones, edificio, herramientas y otra.
- `titulo` (corto, para listados) y `descripcion`.

**Urgencia**
- `prioridad`: baja, media, alta o crítica.
- `afecta_operatividad`: booleano. Solo destaca la tarea; **no** cambia `Vehiculo.en_servicio`.
- `fecha_limite`, opcional. Pasada esa fecha, la tarea aparece como vencida.

**Personas**
- `reporto`: FK a `Integrante`, el usuario logueado, no se puede editar.
- `encargados`: muchos a muchos con `Integrante` (`related_name="tareas_a_cargo"`), vacío hasta que se asigna.
- `asigno` y `fecha_asignacion` (la primera asignación). Cada alta o baja de encargado posterior queda en el historial.

**Estado**
- `estado`, más `motivo_espera` (repuesto, taller, presupuesto u otro).
- Resolución: `resolvio`, `fecha_resolucion` y `nota_resolucion`.
- Descarte: `motivo_descarte`.

**Vínculos**
- `mantenimientos`: muchos a muchos con `Mantenimiento` (`related_name="tareas_resueltas"`).
- `ordenes_compra`: muchos a muchos con `OrdenCompra`, cuando exista ese módulo.
- `origen_parte` (FK opcional a `ParteSiniestro`), `origen_mantenimiento` (FK opcional, para lo que «quedó pendiente» de un trabajo anterior) y `origen_libro` (FK opcional a `AnotacionLibroGuardia`).

### `FotoTarea`
- `tarea`, `archivo`, `fecha` y `subio`.

### `EventoTarea` (historial)
- `tarea`, `fecha`, `usuario` y `tipo`: reporte, asignación, cambio de estado, cambio de prioridad, comentario o foto.
- `estado_anterior`, `estado_nuevo` y `texto`.
- **Solo se agregan**: no se editan ni se borran. No llevan firma con contraseña: alcanza con el historial.

---

## Permisos y visibilidad

Permisos:
- `add_tareapendiente` y `view_tareapendiente` (el CRUD normal de Django).
- `asignar_tareapendiente`: asignar, cambiar prioridad y fecha límite, descartar y reabrir.

| | Jefe | Graduado | Personal de guardia | Estadística | Bombero / Aspirante / Invitado |
|---|---|---|---|---|---|
| Ver todas | ✔ | ✔ | ✔ | ✔ (solo lectura) | ✘ |
| Reportar | ✔ | ✔ | ✔ | | ✘ |
| Asignar, cambiar prioridad, descartar, reabrir | ✔ | ✔ | ✘ | | ✘ |
| Cambiar estado y resolver | ✔ | ✔ | si es uno de los encargados | | si es uno de los encargados |
| Resolver al tildarla en un mantenimiento | quien pueda cargar el mantenimiento (hoy: Jefe, Graduado, guardia y Estadística) | | | | |

**Encargados sin permisos de tareas.** Si uno de los encargados es, por ejemplo, un Bombero, igual tiene que poder ver y resolver **sus** tareas. Se resuelve en la vista: «Mis tareas» y el detalle se habilitan para quien figura entre los encargados, aunque no tenga `view_tareapendiente`. Lo mismo pasa con la orden de compra (ejecutor) y con las citaciones (el citado).

**Cuidado con los permisos:** el Graduado hereda los permisos de la guardia (`PERMISOS_GRADUADO` se arma a partir de `PERMISOS_GUARDIA`), así que `asignar_tareapendiente` se agrega al Graduado y al Jefe, **no** a la guardia.

---

## Pantallas

1. **Tablero**:
   - columnas por estado: Reportada, Asignada, En curso, En espera y Resuelta (las últimas, más un enlace a todas);
   - tarjeta con número, móvil o elemento, título, prioridad, «afecta operatividad», vencida, encargados y antigüedad;
   - filtros por móvil o elemento, encargado (incluye «sin asignar»; muestra las tareas donde figura entre los encargados), categoría, prioridad y texto;
   - resumen arriba: abiertas, que afectan la operatividad, vencidas y antigüedad mediana;
   - en el celular, las columnas se apilan.
2. **Detalle**:
   - datos, fotos e historial en forma de conversación;
   - comentarios y fotos;
   - acciones según quién mira;
   - vínculos (parte de origen, orden de compra, mantenimientos);
   - otras tareas del mismo móvil.
3. **Reportar** (pensado para el celular):
   - móvil o elemento, título, categoría, prioridad, «afecta la operatividad», descripción, fotos y origen opcional;
   - **aviso de posible duplicado**: si el móvil ya tiene tareas abiertas, las muestra antes de guardar.
4. **Mis tareas**: las tareas donde el usuario logueado figura entre los encargados, con aviso en el menú («Tenés 2 tareas»).
5. **Al cargar un mantenimiento** de un móvil (o elemento):
   - el formulario actual muestra las tareas abiertas de ese móvil, para tildar las que resuelve;
   - las tildadas pasan **directamente** a Resuelta, vinculadas al mantenimiento, sin importar quién lo cargó;
   - en el historial queda «Resuelta con el mantenimiento N° … (cargó …)».
6. **Ficha del móvil**: sus tareas abiertas y el historial.
7. **Al elegir el móvil en un parte**: si tiene tareas que afectan la operatividad, se muestra un aviso (solo informativo, no bloquea).
8. **Imprimir** las pendientes de un móvil, para llevar al taller.
9. **Aviso en el menú** para Jefe y Graduado: tareas sin asignar y vencidas.

---

## Relación con Estadísticas (`docs/PLAN_ESTADISTICAS.md`)
- Tareas reportadas y resueltas por mes.
- Tiempo de resolución (mediana y P90) por categoría y prioridad.
- Tareas abiertas por móvil.
- Tiempo en espera por motivo.
- Por integrante: tareas reportadas, tareas en las que fue encargado (cuenta para cada uno de los encargados) y tiempo medio de resolución.

---

## Segunda etapa: tareas periódicas automáticas
- `TareaPeriodica`: móvil o elemento, título, cada N días o cada N km, anticipación, encargado por defecto y la última vez que se generó.
- Un comando diario (o una revisión al abrir el tablero) crea la tarea cuando corresponde.
- Ejemplos: cambio de aceite cada 10.000 km, verificación técnica (`Vehiculo.fecha_vto_verificacion`), control mensual de matafuegos y prueba hidráulica de los ERA.

---

## Preguntas abiertas
Ninguna por ahora. La lista de categorías se puede ajustar desde el admin.

---

## Tests a incluir
- Reportan la guardia, el Graduado y el Jefe; el Bombero no puede, salvo ver y resolver lo que tiene asignado.
- Solo el Jefe y el Graduado asignan, cambian la prioridad, descartan y reabren. La guardia no.
- Cualquiera de los encargados puede resolver aunque su rol no tenga permisos de tareas, y solo las tareas donde figura.
- Estadística ve todas las tareas, pero no reporta, asigna ni cambia estados.
- Una tarea con varios encargados aparece en «Mis tareas» de cada uno.
- No se puede pasar de Reportada a En curso sin al menos un encargado.
- Descartar y reabrir exigen motivo.
- Cada cambio deja un evento en el historial, y los eventos no se editan ni se borran.
- Al guardar un mantenimiento con tareas tildadas, quedan resueltas y vinculadas, aunque lo cargue alguien que no es encargado.
- «Afecta la operatividad» no cambia `Vehiculo.en_servicio`.
- La tarea exige móvil o elemento.
