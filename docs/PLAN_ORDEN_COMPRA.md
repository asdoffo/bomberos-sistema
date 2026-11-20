# Plan: módulo de Órdenes de compra

Planificado el 14/11/2026 y actualizado con las respuestas del usuario. Todavía no está implementado.
Mock de la hoja: `docs/mock_orden_compra.html` (abrir en el navegador; el selector de arriba muestra cada estado).

Objetivo: una orden de compra digital, que también se pueda imprimir, como la orden de entrega. Debe quedar sellado quién la pidió, quién la autorizó, qué guardia la gestionó y quién la ejecutó.

---

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Circuito | Jefe o Graduado **pide** → el Jefe **autoriza** → la guardia **gestiona** → un integrante con permiso **ejecuta**. |
| Comercio | Se puede pedir sin comercio. La guardia lo define (y consigue el presupuesto) al gestionar. |
| Un comercio por orden | No puede haber en una misma orden ítems de comercios distintos. |
| Compra parcial | Es muy común: se registra por ítem cuánto se compró y a qué precio. |
| Ejecución | Al sellar la ejecución, la orden queda **cerrada** (solo lectura). |
| Autorización por monto | No hay tope. El Jefe autoriza todas. |
| Quién ejecuta | Un integrante con un permiso especial (por ejemplo, encargado de compras). |
| Pedido del Jefe | Si la pide el Jefe, queda **autorizada sola**: los sellos «Pidió» y «Autorizó» salen a su nombre. |
| Gestión | La hacen la guardia y también el Jefe. |
| Visibilidad | Jefe, Graduado y Estadística ven **todas**. La guardia ve solo las órdenes **ya autorizadas**. Bombero, Aspirante e Invitado no ven órdenes de compra. |
| Orden de faltantes | Empieza como **pedida**: el Jefe la vuelve a autorizar. |
| Corrección de una ejecutada | Se permite, con motivo, a la guardia, al Jefe y al Graduado. Queda registrada. |
| Cierre del período | Se puede cerrar con órdenes sin ejecutar. |

---

## Circuito

```
PEDIDA ──(Jefe autoriza)──▶ AUTORIZADA ──(guardia gestiona)──▶ GESTIONADA ──(se compra)──▶ EJECUTADA (cerrada)
  │  └─(Jefe rechaza)─┐          │                                  │
  │                   ▼          ▼                                  ▼
  └──────────────▶ ANULADA ◀─────┴──────────────────────────────────┘
```

| Paso | Quién | Qué hace | Qué queda sellado |
|---|---|---|---|
| Pedir | Jefe o Graduado | Carga los ítems, cantidades, costo estimado, destino y motivo. El comercio es opcional. Si la pide el Jefe, la misma firma sella también la autorización y la orden pasa directo a autorizada. | Pidió: nombre, legajo, fecha y hora, firma |
| Autorizar o rechazar | Jefe | Autoriza la orden, o la rechaza con motivo (queda anulada). | Autorizó: ídem |
| Gestionar | Personal de guardia o Jefe | Define o confirma el comercio, el presupuesto y la forma de pago, y ajusta precios estimados. Si los ítems van a comercios distintos, **divide la orden**. | Gestionó: ídem |
| Ejecutar | Integrante con permiso de compras | Por cada ítem, cantidad comprada y precio real. Además: fecha, factura o remito, foto del comprobante y novedades. La orden se **cierra**. | Ejecutó: ídem |
| Anular | Jefe (en cualquier estado antes de ejecutar). Quien pidió, solo mientras está pedida. | Con motivo obligatorio. | Anuló: ídem y el motivo |

- Cada paso se firma confirmando la contraseña, con `Firma.sellar()`.
- Se agregan las acciones nuevas a `Firma.Accion`: `PEDIDO_COMPRA`, `AUTORIZACION_COMPRA`, `GESTION_COMPRA`, `EJECUCION_COMPRA`, `ANULACION_COMPRA` y `CORRECCION_COMPRA`.
- **Qué se puede editar en cada estado:**
  - Pedida: todo (quien pidió o el Jefe).
  - Autorizada: comercio, presupuesto, forma de pago y precios estimados (la guardia, al gestionar). No se agregan ítems nuevos, porque el Jefe no los autorizó.
  - Gestionada: solo la carga de la compra.
  - Ejecutada: solo con «Corregir con motivo» (ver abajo).
  - Anulada: nada.

### Corregir una orden ejecutada
- Pueden hacerlo la guardia, el Jefe y el Graduado (permiso `corregir_ordencompra`).
- Se corrigen los datos de la gestión y de la compra: comercio, presupuesto, forma de pago, cantidades compradas, precios reales, comprobante y novedades.
- No se agregan ni se quitan ítems, porque eso no fue autorizado.
- El motivo es obligatorio y se firma con la contraseña (`CORRECCION_COMPRA`). La firma guarda cómo estaban los datos antes y después, igual que la corrección de un parte cerrado (`datos_para_firma`).
- La orden sigue **ejecutada (cerrada)**. En el detalle y en la impresión aparece «Corregida el … por … (motivo)».

### Dividir por comercio (al gestionar)
- La guardia elige qué ítems van a otro comercio, y el sistema crea una orden nueva con esos ítems.
- La orden nueva queda **autorizada**, con referencia «dividida de la OC 17/2026». Así hereda la autorización del Jefe sobre esos mismos ítems y no hay que volver a autorizarla.
- La orden original pierde esos ítems. Las dos quedan vinculadas y la operación queda en la firma de la gestión.

### Lo que faltó comprar (después de ejecutar)
- En cada ítem queda la cantidad pedida y la comprada. Si la comprada es menor, el ítem queda marcado como «no comprado» o «compra parcial».
- El botón **«Nueva orden con lo que faltó»** crea una orden pedida con las cantidades pendientes, vinculada a la original. Queda sin comercio, para que la guardia lo defina.

---

## Datos

### `Comercio` (catálogo nuevo)
- `razon_social`, `cuit` (con validador), `direccion`, `telefono`, `email`, `rubro` y `activo`.
- La guardia lo da de alta desde la misma orden si no existe.

### `OrdenCompra` (usa `CorrelativoAnual`: N° 17/2026)

**Encabezado**
- `fecha` y `estado`: pedida, autorizada, gestionada, ejecutada o anulada.
- `comercio`: FK, **opcional** mientras está pedida o autorizada, **obligatorio** para pasar a gestionada.
- Gestión: `presupuesto_numero`, `presupuesto_vence` y `forma_pago` (contado, cuenta corriente o transferencia).

**Destino y pedido**
- `vehiculo`, `departamento` (FK opcionales) o `destino_otro` (texto).
- `motivo`, `prioridad` (normal o urgente) y `observaciones`.
- Vínculo opcional a un `ParteSiniestro` o a un `Mantenimiento` que originó la compra.

**Vínculos entre órdenes**
- `dividida_de` y `faltantes_de`: FK a `OrdenCompra`.

**Circuito**
- `pidio` y `fecha_pedido`.
- `autorizo` y `fecha_autorizacion`.
- `gestiono` y `fecha_gestion`.
- `ejecuto` y `fecha_ejecucion`.
- `anulo`, `fecha_anulacion` y `motivo_anulacion`.

Todos son FK a `Integrante`, se completan con el usuario logueado y no se pueden editar.

**Ejecución**
- `fecha_compra`, `comprobante_numero`, `comprobante` (archivo: foto o PDF) y `novedades`.

**Calculados**
- `total_estimado`, `total_real` y `completa` (si se compró todo).

### `ItemOrdenCompra`
- `orden` (FK), `descripcion`, `cantidad` (decimal: 2,5 m o 1,5 kg), `unidad` y `precio_unitario_estimado`.
- Se cargan al ejecutar: `cantidad_comprada` (decimal, 0 si no se compró) y `precio_unitario_real`.
- Opcional: `nota` (por ejemplo, «sin stock»).
- Calculados: `subtotal_estimado`, `subtotal_real` y `pendiente` (cantidad − comprada).
- Validaciones: la cantidad comprada no puede superar la pedida, y la orden necesita al menos un ítem.

---

## Permisos y visibilidad

Permisos nuevos en `OrdenCompra.Meta.permissions`:
- `autorizar_ordencompra`
- `gestionar_ordencompra`
- `ejecutar_ordencompra`
- `corregir_ordencompra`
- `ver_todas_ordencompra`

| Permiso | Jefe | Graduado | Estadística | Personal de guardia | Compras (permiso extra) | Bombero / Aspirante / Invitado |
|---|---|---|---|---|---|---|
| `view_ordencompra` | ✔ | ✔ | ✔ | ✔ | ✔ | ✘ |
| `ver_todas_ordencompra` | ✔ | ✔ | ✔ | | | ✘ |
| `add_ordencompra` (pedir) | ✔ | ✔ | | | | ✘ |
| `autorizar_ordencompra` | ✔ | | | | | ✘ |
| `gestionar_ordencompra` | ✔ | | | ✔ | | ✘ |
| `ejecutar_ordencompra` | | | | | ✔ | ✘ |
| `corregir_ordencompra` | ✔ | ✔ | | ✔ | | ✘ |

**Qué órdenes ve cada uno** (se filtra en la consulta, no solo en el menú):
- **Jefe, Graduado y Estadística**: todas (`ver_todas_ordencompra`).
- **Guardia**: las autorizadas en adelante.
- **Compras**: las gestionadas en adelante.

Si alguien tiene varios roles, ve la unión de lo que le corresponde a cada uno.

Si alguien abre la dirección de una orden que no le corresponde, recibe un **404**.

**Permiso de compras**: un grupo nuevo «Compras», que se suma al rol del integrante (por ejemplo, Bombero + Compras). No cambia la duración de la sesión: `roles.PRIORIDAD` sigue igual.

**Cuidado:** en `cargar_catalogos.py`, `PERMISOS_GRADUADO` se arma a partir de `PERMISOS_GUARDIA`. `gestionar_ordencompra` tiene que quedar excluido del Graduado de forma explícita, como ya pasa con el libro de guardia. `corregir_ordencompra` sí lo reciben los dos. A la inversa, `add_ordencompra` y `ver_todas_ordencompra` van al Graduado pero **no** a la guardia.

---

## Pantallas
1. **Listado**:
   - pestañas según quién mira: «Por autorizar» (Jefe), «Por gestionar» (guardia), «Por comprar» (Compras), «Mis pedidos» y «Cerradas»;
   - filtros por comercio, destino y fechas, y buscador;
   - total estimado y total real del filtro.
2. **Avisos en el menú**: cuántas órdenes hay por autorizar, por gestionar o por comprar, según el permiso, con las urgentes primero.
3. **Formulario de pedido**: encabezado y una grilla de ítems con el total en vivo, igual que los elementos de la orden de entrega.
4. **Gestionar**: buscador de comercio con alta rápida, presupuesto, forma de pago, precios estimados y «Dividir por comercio».
5. **Registrar compra**: la grilla de ítems con «Compró» y «Precio real» (por defecto, la cantidad pedida y el precio estimado), comprobante y novedades. Antes de firmar, avisa que la orden se cierra.
6. **Detalle e impresión** (ver el mock):
   - membrete, número, fecha y estado;
   - comercio («A definir» si todavía no tiene) y datos del pedido;
   - ítems, con las columnas reales solo una vez ejecutada (lo no comprado aparece tachado y con su nota);
   - totales estimado y real;
   - cuadro de compra realizada;
   - los **4 sellos** (pidió, autorizó, gestionó y ejecutó);
   - firmas en papel para el comercio y para quien recibió en el cuartel;
   - marca de agua «ANULADA» si corresponde.

---

## Relación con Estadísticas (`docs/PLAN_ESTADISTICAS.md`)
- Gasto real por mes, comercio, destino y móvil.
- Diferencia entre lo estimado y lo real.
- Porcentaje de compras parciales.
- Tiempo de cada etapa: pedido → autorización → gestión → compra.
- Órdenes urgentes y órdenes rechazadas.

---

## Período
- La orden toma el año de su `fecha`, que define su número (N° 17/2026).
- Cerrar el período **no** depende de las órdenes. Una orden de 2026 sin ejecutar sigue su circuito en 2027 con su mismo número.
- Las órdenes no entran en `Periodo.problemas_para_cerrar()`.

## Preguntas abiertas
Ninguna por ahora. La lista de unidades (unidad, metro, kg, litro, caja, par…) se puede ajustar al implementar.

---

## Tests a incluir
- Cada transición exige el permiso y el estado correctos: no se saltea ningún paso ni se vuelve atrás.
- No se puede pasar a gestionada sin comercio.
- Dividir por comercio crea una orden autorizada con los ítems elegidos, y la original los pierde.
- Una orden ejecutada queda cerrada: no se edita ni se anula.
- La cantidad comprada no supera la pedida. Los totales real y estimado, y lo pendiente, se calculan bien.
- «Nueva orden con lo que faltó» copia solo las cantidades pendientes.
- Visibilidad:
  - la guardia no ve las pedidas;
  - Graduado y Estadística ven todas;
  - Bombero, Aspirante e Invitado reciben 403 o 404;
  - el Graduado no puede gestionar y la guardia no puede pedir.
- Una orden pedida por el Jefe queda autorizada, con los dos sellos a su nombre.
- La corrección de una ejecutada exige motivo, deja la firma con el antes y el después, y no agrega ítems. Estadística no puede corregir.
- El período se cierra aunque haya órdenes sin ejecutar.
- Cada paso deja su firma y la cadena de firmas se verifica.
