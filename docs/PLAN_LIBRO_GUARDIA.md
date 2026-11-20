# Plan: Libro de guardia mejorado

Planificado el 15/11/2026. Todavía no está implementado.
Mock: `docs/mock_libro_guardia.html` (abrir en el navegador). Tiene 4 pantallas:
1. libro de la guardia en curso;
2. inicio de guardia (06:00);
3. visado del Jefe;
4. buscar e imprimir.

Objetivo: que el libro digital reemplace por completo al libro en papel. Todos los eventos del cuartel tienen que quedar registrados, y los que ya se cargan en el sistema se anotan solos.

---

## Lo que ya existe y se mantiene
- `AnotacionLibroGuardia`: folio correlativo por año, hora del hecho, cuándo se cargó (`registrada`), autor congelado y sello SHA-256 encadenado con la anotación anterior (`verificar_cadena`).
- **No se edita ni se borra.** El Jefe agrega correcciones con motivo, que remiten a la original (`corrige`).
- Anotan el Personal de guardia y el Jefe (`add_anotacionlibroguardia`). El Graduado solo ve. **Sigue igual.**
- La sesión del Personal de guardia vence a las 06:00 (`sesion.py`, `SESION_HORA_CAMBIO_GUARDIA`), y así el entrante tiene que iniciar su propia sesión.

Hoy el libro es solo una hora y un texto libre, con navegación por día calendario. En la base, casi todo lo anotado son ingresos de personal y salidas de unidades, que ya están en el parte: se escribe dos veces.

---

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Qué se anota | **Todos los eventos.** Los que el sistema ya registra se insertan solos. |
| Anotaciones automáticas | Parte (inicio, salida, regreso y cierre), órdenes de entrega, tareas pendientes y mantenimientos. |
| Guardia | De **06:00 a 06:00**. |
| Relevo | Las novedades se pasan **de palabra**: no hay lista de control. El relevo queda registrado solo: a las 06:00 se cierra la guardia saliente y, cuando el entrante inicia sesión, se anota «Recibe la guardia…». |
| «De guardia ahora» | Lo anota **solo el guardia**, y **solo para quienes están de guardia activa**: no para quien pasa un momento por el cuartel. Es solo informativo y **no** marca nada en el parte, por ahora. |
| Otras automáticas | Capacitaciones, órdenes de compra y citaciones: **no**, por ahora. |
| Visado del Jefe | Sí, como la firma en el libro de papel: **una vez por guardia**. |
| «Anotada tarde» | **Sí.** Si entre la hora del hecho («Hs.», la escribe el guardia) y la hora de carga (cuando se tocó «Anotar», la pone el sistema) pasan **más de 30 min**, se muestra «anotada HH:MM» al lado. Solo informativo: no bloquea nada. |
| Quién anota | Personal de guardia y Jefe. El Graduado solo ve. |

---

## Tipos de anotación

Se agrega `tipo` a `AnotacionLibroGuardia`:

| Tipo | Cómo se carga | Texto que se arma |
|---|---|---|
| Ingresa a la guardia | Manual: se elige el integrante; solo personal de guardia activa | «Ingresa a la guardia el Bv. Rodríguez P. (087)» + texto opcional |
| Se retira de la guardia | Manual: se elige entre los que están de guardia | «Se retira de la guardia el …» |
| Llamada sin salida | Manual | texto libre (quién llama, qué pasa, qué se indicó) |
| Visita | Manual | texto libre (quién, motivo) |
| Materiales | Manual (si no hay orden de entrega) | texto libre |
| Novedad de equipos o unidades | Manual: unidad o equipo opcional | texto libre |
| Novedad general | Manual | texto libre |
| Otra | Manual | texto libre |
| Parte: inicio, salida, regreso, cierre | **Automática** | «Sale U°12 por un 02-114 (Belgrano 450), a cargo del Bv. Luna E., con 6 integrantes.» |
| Orden de entrega: alta y devolución | **Automática** | «Se entregan en préstamo 3 elementos a … (Orden 00000041)» |
| Tarea pendiente: reportada y resuelta | **Automática** | «Se reporta tarea T-21/2026: …» |
| Mantenimiento: cargado | **Automática** | «Mantenimiento 12/2026 del Móvil 62: …» |
| Guardia: cierre y recepción | **Automática** | «Cierra la guardia de …» y «Recibe la guardia el …» |
| Visado del Jefe | Al visar | «Visada por el Jefe …» |

### Campos nuevos en `AnotacionLibroGuardia`
- `tipo`: lista de opciones (las de la tabla).
- `automatica`: booleano.
- `integrante`: FK opcional, en «Ingresa a la guardia» y «Se retira de la guardia».
- `vehiculo`: FK opcional.
- Origen, para el enlace: `objeto_tipo` y `objeto_id`, como en `Firma` (parte, orden, tarea, mantenimiento).
- `importante`: booleano; le avisa al Jefe.
- `guardia`: FK a `Guardia`.
- **Entran en el sello** (`_contenido`) todos los campos nuevos. Las anotaciones viejas se migran como tipo «Otra», sin recalcular su sello. Para eso, `_contenido` incluye los campos nuevos solo cuando tienen valor, y así los sellos viejos siguen verificando.

### Anotaciones automáticas
- Se crean en la **misma transacción** que el evento (por ejemplo, en `marcar_hora` o en `ParteSiniestro.cerrar`), con `AnotacionLibroGuardia.anotar(...)`. Así entran en la cadena de sellos con su folio.
- El autor es el usuario que hizo la acción. Si no hay usuario (el cierre de guardia de las 06:00), el autor es «Sistema».
- No se escriben a mano. Si el dato estaba mal, se corrige en su origen (parte, orden…), y el Jefe puede agregar una corrección en el libro, como hoy.
- No se anota dos veces el mismo evento. Por ejemplo, si se marca la salida dos veces, queda una sola anotación.

---

## Guardia (06:00 a 06:00)

### `Guardia` (tabla nueva)
- `inicio` (06:00 del día) y `fin` (06:00 del día siguiente).
- `a_cargo`: el primer usuario del Personal de guardia que inicia sesión en esa guardia.
- `hora_recepcion`: cuándo inició sesión.
- `novedades`: el texto opcional que deja el saliente para el entrante.
- Visado: `visada_por`, `fecha_visado` y `observacion_visado`, con firma.

### Cómo funciona el relevo
1. A las **06:00** termina la guardia. La primera vez que alguien usa el sistema después de esa hora se anota «Cierra la guardia de … (N partes, M anotaciones)», con fecha y hora 06:00 y autor «Sistema». No hace falta una tarea programada.
2. Cuando el **guardia entrante inicia sesión**, se crea la `Guardia` del día (si no existe) y se anota «Recibe la guardia el …», con las novedades escritas por el saliente si las dejó.
3. Antes de las 06:00, el saliente puede usar «**Dejar novedades para la próxima guardia**» (opcional). Las novedades se siguen pasando de palabra; esto es una ayuda.
4. Al entrar, el guardia ve la pantalla **«Recibís la guardia»**, con:
   - las novedades escritas por el saliente;
   - partes sin cerrar;
   - anotaciones importantes de la guardia anterior;
   - unidades fuera de servicio;
   - tareas que afectan la operatividad;
   - órdenes de entrega pendientes de devolución.

### Regla de «solo se anota hoy»
Pasa a ser «**solo se anota en la guardia en curso**». Una anotación de las 03:00 pertenece a la guardia que empezó el día anterior a las 06:00.

---

## Visado del Jefe
- Una firma por guardia terminada. Es el reemplazo de la firma del Jefe en el libro de papel.
- El Jefe la firma con su contraseña, con una observación opcional.
- Queda como una anotación del libro, con sello. No se puede deshacer.
- Pantalla con las guardias sin visar, y las anotaciones importantes destacadas.
- Aviso en el menú del Jefe: «N guardias sin visar».
- Si después del visado aparece un error, el Jefe agrega una corrección, como hoy.

---

## Pantallas

1. **Libro de la guardia en curso**:
   - encabezado con la guardia (06:00 a 06:00), quién está a cargo y desde qué hora, y el botón «Dejar novedades para la próxima guardia»;
   - **nueva anotación**: botones por tipo. En ingreso y retiro de la guardia se elige al integrante y se muestra cómo quedará el texto. Casilla «importante»;
   - **línea de tiempo** con ícono por tipo, marca de automática con enlace a su origen, marca de anotada tarde (si se cargó mucho después de la hora del hecho) y correcciones como hoy;
   - filtros: todo, personal, unidades, novedades, importantes;
   - lateral: «De guardia ahora» (sale de los ingresos y retiros de la guardia), estado de las unidades y estado del visado;
   - navegación entre guardias (anterior y siguiente).
2. **Recibís la guardia**: la pantalla que ve el entrante al iniciar sesión.
3. **Visado** (Jefe): guardias sin visar y el formulario para visar.
4. **Buscar e imprimir**: por texto, tipo, integrante, unidad, autor y fechas. Imprimir o exportar un período con folios y sellos, para archivo.

---

## «De guardia ahora»
- Se calcula con las anotaciones de la guardia en curso: quienes tienen «Ingresa a la guardia» y no tienen después «Se retira de la guardia», más el guardia a cargo.
- Al cerrarse la guardia a las 06:00, la lista empieza vacía en la guardia siguiente.
- «Se retira de la guardia» ofrece solo a los que figuran de guardia en ese momento.

## «Anotada tarde»
- Se calcula al mostrar, sin campos nuevos: la diferencia entre `fecha` + `hora` (el hecho) y `registrada` (la carga), contemplando que la guardia cruza la medianoche.
- Más de **30 min** (constante `MINUTOS_ANOTADA_TARDE = 30`) → «anotada 20:30».
- Las anotaciones automáticas no se marcan, porque se crean en el momento del evento.
- También sirve como filtro en la búsqueda.

## Preguntas abiertas
Ninguna.

---

## Tests a incluir
- Cada evento automático genera una sola anotación, con su tipo, su enlace y su folio, dentro de la cadena de sellos (`verificar_cadena` sigue dando íntegro).
- Las anotaciones viejas siguen verificando después de agregar los campos nuevos.
- El texto de ingreso y retiro de la guardia se arma bien a partir del integrante. «De guardia ahora» muestra a quienes ingresaron y no se retiraron en la guardia en curso, y empieza vacía en la guardia siguiente.
- Después de las 06:00 se anota el cierre de la guardia una sola vez. El primer inicio de sesión del guardia anota «Recibe la guardia», con las novedades si las hay.
- Solo se anota en la guardia en curso: una anotación de las 03:00 pertenece a la guardia del día anterior.
- Solo el Jefe visa. El visado queda sellado y no se repite para la misma guardia.
- El Graduado ve y no anota. El Bombero no ve el libro.
- «Anotada tarde» aparece solo con más de 30 min entre el hecho y la carga, también cruzando la medianoche (hecho 23:50 y carga 00:30 son 40 min), y nunca en las automáticas.
- La búsqueda filtra por texto, tipo, integrante, unidad y fechas. La impresión incluye folios y sellos.
