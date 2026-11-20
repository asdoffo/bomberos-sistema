# Plan: numeración propia por tipo de documento

Planificado el 17/11/2026. Todavía no está implementado.

**Problema:** hoy cuatro modelos heredan de `CorrelativoAnual` (`partes/models.py:458`) y cada uno
cuenta desde 1 cada año. El mismo número existe cuatro veces: el `10/2026` puede ser un parte de
siniestro, una capacitación, un mantenimiento o una orden de entrega. Con los ocho módulos
planificados serían nueve numeraciones pisándose.

**Objetivo:** que el número identifique solo, sin tener que saber de qué pantalla salió.

---

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Formato | **Prefijo de dos letras + correlativo de 4 dígitos + año**: `PS-1425/2026`. |
| Prefijo de dos letras | Para que no choquen (con una sola letra, `C` sirve para Capacitación y Citación, y `M` para Mantenimiento). |
| Contador | **Sigue igual**: por tipo y por año. Los tomos del cuartel son anuales y por tipo, el correlativo tiene que corresponder con el papel. |
| Dónde se guarda el prefijo | **En ningún lado.** Es un atributo de clase; el número se arma al mostrar. Sin migraciones de datos ni renumeración. |
| Tipos nuevos | Los agregamos programando, cada uno con su tabla. **No** se pueden crear tipos de documento desde la aplicación (decisión del 17/11). |
| Sellos | **No se tocan.** Ver «Lo que no se toca». |

---

## Tabla de códigos

| Código | Documento | Modelo | Estado |
|---|---|---|---|
| `PS` | Parte de siniestro | `ParteSiniestro` | existe |
| `CA` | Capacitación | `Capacitacion` | existe |
| `MA` | Mantenimiento | `Mantenimiento` | existe |
| `OE` | Orden de entrega | `OrdenEntrega` | existe |
| `OC` | Orden de compra | — | planificado (`PLAN_ORDEN_COMPRA.md`) |
| `CI` | Citación | — | planificado (`PLAN_CITACIONES.md`) |
| `TP` | Tarea pendiente | — | planificado (`PLAN_TAREAS_PENDIENTES.md`) |
| `RE` | Recibo de pago | — | planificado (`PLAN_PAGOS.md`) |
| `LG` | Folio del libro de guardia | `AnotacionLibroGuardia` | existe (hoy folio suelto) |

Reservados para no reusarlos: `PA` (apresto), `LI` (licencia), `IT` (itinerario de viaje, Fase 2 del SRS).

El combustible **no lleva código**: se carga dentro del parte y se identifica con el `PS` de ese parte.

---

## Cómo se implementa

### `partes/numeracion.py` (archivo nuevo)
- `CODIGOS = {"PS": "Parte de siniestro", "CA": "Capacitación", ...}`: la lista completa en un solo
  lugar, para que no se repita un código por descuido.
- `DIGITOS = 4` (los partes llegan a ~1.400 por año; con 4 dígitos alcanza y las columnas se alinean).
- `formatear(codigo, numero, anio)` → `"PS-1425/2026"`.
- `parsear(texto)` → `(codigo_o_None, numero_o_None, anio_o_None)`, para el buscador. Acepta
  `1425`, `1425/2026`, `PS-1425/2026`, `ps 1425`, `PS1425/26` y con espacios de más.

### En los modelos
- `CorrelativoAnual` gana `CODIGO = None` y la propiedad:
  ```python
  @property
  def numero(self):
      return numeracion.formatear(self.CODIGO, self.numero_correlativo, self.anio)
  ```
- Cada modelo declara el suyo: `CODIGO = "PS"`, `CODIGO = "CA"`, etc.
- `AnotacionLibroGuardia` no hereda de `CorrelativoAnual` (su folio funciona distinto): se le agrega
  la misma propiedad `numero` con `CODIGO = "LG"`.
- Test que verifica que **ningún código se repite** entre modelos y que todos están en `CODIGOS`.

### En las pantallas
- 34 apariciones de `{{ x.numero_correlativo }}/{{ x.anio }}` en 18 plantillas → `{{ x.numero }}`.
- ~20 textos en `views.py` (`f"Parte {parte.numero_correlativo}/{parte.anio}"`) → `parte.numero`.
- `admin.py:241` (`numero_con_anio`) → usa la propiedad.
- Los `order_by("numero_correlativo")` y `Q(numero_correlativo=...)` **no cambian**: siguen
  trabajando con el entero.

### Buscador (importante)
Hoy `listado_partes` hace `q.split("/")[0]` y `numero.isdigit()` (`views.py:279-283`).
Pasa a usar `numeracion.parsear(q)`:
- **Todos los papeles del cuartel dicen `1425/2026`**, así que la forma vieja tiene que seguir
  funcionando siempre;
- si el texto trae un código de otro tipo (`CA-0034` buscado en el listado de partes), el listado
  avisa «Ese número es de una capacitación» con el enlace, en lugar de no encontrar nada;
- el mismo `parsear` se usa en los listados de capacitaciones, mantenimientos y órdenes.

---

## Lo que NO se toca

1. **Los sellos.** `ParteSiniestro.datos_para_firma()` (`models.py:711`) guarda
   `"parte": f"{self.numero_correlativo}/{self.anio}"`. Si ese texto cambiara, **todas las firmas
   existentes dejarían de verificar**. El sello es un dato interno, no lo que se muestra: queda
   exactamente igual. Lo mismo con el `_contenido` del libro de guardia.
2. **Los contadores y las columnas de la base.** `numero_correlativo` y `anio` siguen como están;
   no hay migración de datos ni riesgo de renumerar.
3. **El casillero de 8 dígitos de la orden de entrega impresa** (`orden.html:35`,
   `numero_correlativo|stringformat:"08d"` → `00000041`): el formulario de papel ya está impreso, así
   que ahí se deja. El `OE-0041/2026` va en las pantallas y listados.
4. **El CSV del período**: no incluye números de documento, no lo afecta.

---

## Detalles menores

- **Formularios impresos**: el encabezado pasa a «Control de salida N° PS-1425/2026». Conviene
  mirar una impresión antes de dar por cerrado el cambio, porque es lo que se archiva en el tomo.
- **Partes guardados en un celular sin señal**: el título que se guardó en IndexedDB
  (`completar_parte.html:18`, `data-titulo`) va a seguir mostrando el formato viejo hasta que ese
  parte se envíe. Es solo el título de la lista; no afecta los datos.
- **Transición**: durante un tiempo van a convivir papeles con `1425/2026` y pantallas con
  `PS-1425/2026`. Por eso el buscador acepta las dos formas desde el primer día.

---

## Preguntas abiertas
Ninguna.

---

## Tests a incluir
- `formatear` arma `PS-1425/2026` y rellena con ceros (`PS-0034/2026`).
- `parsear` entiende `1425`, `1425/2026`, `PS-1425/2026`, `ps 1425`, `PS1425/26`, y devuelve vacío
  con texto que no es un número.
- Ningún código repetido entre modelos; todos los modelos numerados tienen `CODIGO` y está en `CODIGOS`.
- Buscar `1425` (forma vieja) y `PS-1425/2026` encuentra el mismo parte.
- Buscar `CA-0034` en el listado de partes avisa que es de otro tipo y no devuelve resultados erróneos.
- **Las firmas de los partes cerrados antes del cambio siguen verificando** (`verificar_cadena` y la
  comparación al corregir). Es el test que importa.
- Un parte, una capacitación y un mantenimiento creados el mismo año pueden tener el mismo
  `numero_correlativo` y sus `numero` son distintos.
