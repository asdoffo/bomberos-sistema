# Plan: Pagos por actividades

Planificado el 14/11/2026 y actualizado con las respuestas del usuario. Todavía no está implementado.
Mock: `docs/mock_pagos.html` (abrir en el navegador). Tiene 6 pantallas:
1. resumen del mes;
2. detalle de un integrante;
3. tarifas;
4. monto en la capacitación;
5. planilla de pago;
6. recibo.

Objetivo: para las actividades que se cobran, ver un resumen y un detalle de lo que debe cobrar cada integrante. Cada tipo de intervención que se cobra tiene un costo asociado.

---

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Tarifa de las salidas | Por **tipo de intervención**. Todos los participantes cobran lo mismo: interviniente, apresto y chofer, convocado o en el cuartel. |
| Capacitaciones | **Se pagan**, con un monto **por capacitación**, que se fija de antemano. No se calcula por nivel ni por horas. |
| Período | **Mensual**. |
| Responsable | Un **Graduado con la función de Tesorería**. Permiso nuevo: carga tarifas, fija el monto de las capacitaciones, cierra la liquidación y registra los pagos. |
| Quién ve el resumen de todos | **Tesorería y Estadística**. Estadística ve todo (resumen, detalle por integrante, recibos, tarifas), pero **solo lectura**. El Graduado sin Tesorería no ve. |
| Monto de la capacitación | Lo carga quien tiene el permiso del módulo (**Tesorería**). |
| Forma de pago | **Efectivo, transferencia o cheque**. |
| Tope | No hay tope de participaciones ni de monto. |
| Constancia del pago | Al registrar el pago se genera un **recibo** numerado, que queda en el sistema. Se puede imprimir y firmar. |
| Vista del bombero | Cada integrante ve **sus propios pagos y recibos** en Mi perfil. |

---

## Situación actual (revisado en el código y en la base el 14/11/2026)
- `TipoSiniestro.se_cobra` marca qué tipos se cobran. Hoy solo «09 · Actividades comerciales»
  (prevenciones, simulacros y capacitaciones remuneradas, alquiler de unidad, suministro de agua).
- `PartePersonal.cobro`: la columna «Cobró» de la grilla del parte aparece si el tipo se cobra y el usuario puede corregir el parte (guardia o Jefe).
- `AsistenciaCapacitacion.cobro`: «cobró» por asistente.
- `ParticipacionMantenimiento.cobro` (planificado, solo en la tabla): **no** entra en los pagos.
- No hay montos ni registro de pagos.

---

## Cómo se calcula
- **Salidas**: cada `PartePersonal` con «cobró», de un tipo que se cobra, de un parte **cerrado**. Monto = tarifa del tipo vigente el día del parte.
- **Capacitaciones**: cada `AsistenciaCapacitacion` con «cobró». Monto = el fijado en esa capacitación. Si la capacitación dura varios días, se paga igual una vez por capacitación.
- **Mes de la liquidación**: el de la fecha de la actividad.
  - Un parte que todavía no está cerrado no entra. Entra en la primera liquidación que se cierre después de que se cierre el parte.
  - El resumen avisa «partes del mes sin cerrar».

---

## Liquidación mensual
1. **En curso**: el mes actual; los montos se calculan en vivo.
2. **Cerrada**: Tesorería la cierra con su firma (`Firma.sellar`).
   - Se **congela** cada renglón (integrante, actividad y monto).
   - Una participación liquidada no se vuelve a pagar.
   - Si después cambia la tarifa, el monto de la capacitación o el parte, la liquidación cerrada no cambia.
3. **Pago por integrante**: Tesorería registra el pago (fecha, forma, observaciones) y el sistema genera el **recibo** con número correlativo por año, el detalle de los conceptos y la firma de quien pagó. Cuando todos cobraron, la liquidación queda **pagada**.

**Correcciones posteriores.** Si se corrige un parte o una asistencia ya liquidados, la diferencia (positiva o negativa) entra como **ajuste** en la próxima liquidación, con referencia a la original.

---

## Datos

### `TarifaIntervencion`
- `tipo_siniestro` (FK; solo los que se cobran), `monto`, `vigente_desde` y `cargo`.
- Se aplica la de mayor `vigente_desde` ≤ fecha del parte.
- No se puede crear con fecha anterior a una liquidación cerrada.

### Cambio en `Capacitacion`
- `monto_pago`: decimal opcional, el monto por asistente que cobra.
- Lo edita solo Tesorería. En el formulario, quien carga la capacitación lo ve pero no lo puede cambiar.
- Sin monto, la columna «Cobró» de los asistentes no se puede tildar.

### `Liquidacion`
- `anio` y `mes` (únicos juntos) y `estado`: en curso, cerrada o pagada.
- `cerro` y `fecha_cierre`, con firma.

### `RenglonLiquidacion` (uno por participación pagada; se congela al cerrar)
- `liquidacion` e `integrante`.
- Origen: `parte_personal` **o** `asistencia`, con `UniqueConstraint` en cada uno, para que no se pague dos veces.
- `fecha_actividad`, `descripcion` (texto congelado) y `monto`.
- `es_ajuste` y `ajusta_a` (FK al renglón original).

### `ReciboPago` (uno por integrante y liquidación)
- Número correlativo por año (`CorrelativoAnual`): Recibo 34/2026.
- `liquidacion`, `integrante` y `total` (congelado).
- `fecha_pago`, `forma_pago` (efectivo, transferencia o cheque), `pago` (quién lo registró), `observaciones` y la firma.
- Si es **cheque**: `cheque_numero` y `cheque_banco`, obligatorios; se imprimen en el recibo.
- Si es **transferencia**: `referencia_transferencia`, opcional (número de operación).
- El recibo **no se borra ni se edita**. Si hubo un error, se **anula con motivo** y se emite otro.

---

## Pantallas

1. **Resumen del mes** (Tesorería; Estadística lo ve sin los botones de acción):
   - filtros por liquidación, actividad y estado del pago;
   - estado de la liquidación con su firma;
   - indicadores: total, integrantes, participaciones y pendiente;
   - tabla por integrante: salidas y $, capacitaciones y $, total, estado del pago, botón «Registrar pago» o enlace al recibo;
   - advertencias: partes sin cerrar, tipos sin tarifa, capacitaciones con «cobró» sin monto;
   - botones «Cerrar liquidación», «Exportar a Excel» y «Planilla de pago».
2. **Detalle de un integrante**:
   - cada participación con su fecha, enlace, tipo, participación y monto;
   - total del mes y acumulado del año;
   - un desplegable con lo que no se cobra y el motivo.
3. **Mis pagos** (Mi perfil, cualquier integrante): el mismo detalle, solo el propio, con sus recibos.
4. **Tarifas** (Tesorería): la tarifa vigente por tipo de intervención, las anteriores y el alta de una nueva con fecha «desde» y firma.
5. **Monto en la capacitación**: un campo nuevo en el formulario de capacitación, editable solo por Tesorería.
6. **Planilla de pago imprimible**: una fila por integrante con su total y el número de recibo, y el sello del cierre.
7. **Recibo imprimible**:
   - número, fecha, integrante, monto en números y letras, mes y forma de pago (con número y banco si es cheque);
   - conceptos (salidas agrupadas por tipo y capacitaciones);
   - sello de quien pagó y una línea para la firma del integrante si se imprime.

---

## Permisos
- Grupo nuevo **«Tesorería»**, que se suma al rol del integrante (por ejemplo, Graduado + Tesorería), igual que «Compras» en la orden de compra. Permisos:
  - `ver_pagos`;
  - `gestionar_tarifas` (incluye el monto de las capacitaciones);
  - `cerrar_liquidacion`;
  - `registrar_pago`.
- **Estadística** recibe solo `ver_pagos`: ve el resumen, el detalle de cada integrante, los recibos y las tarifas, pero no carga, no cierra ni paga.
- **Nadie más ve el resumen de todos**, ni siquiera el Jefe, salvo que también esté en Tesorería.
- **Mis pagos**: cualquier integrante con usuario ve solo lo suyo. El detalle o el recibo de otro da 404.
- Marcar «cobró» en el parte y en la capacitación sigue igual que hoy.

---

## Relación con otros planes
- **Estadísticas** (`docs/PLAN_ESTADISTICAS.md`): «participaciones cobradas» pasa a tener monto. Como Estadística ve todo, las estadísticas pueden mostrar el total pagado por mes, por tipo de actividad y por integrante. Esos montos solo se muestran a quien tiene `ver_pagos`: el Jefe y el Graduado ven cantidades, no pesos.
- **Orden de compra** (`docs/PLAN_ORDEN_COMPRA.md`): el grupo «Tesorería» podría ser el mismo que «Compras». Se deja separado.

---

## Preguntas abiertas
Ninguna.

---

## Tests a incluir
- La salida se paga con la tarifa del tipo vigente el día del parte, igual para interviniente, apresto y chofer, y para convocado o en el cuartel.
- La capacitación se paga con su `monto_pago`, una vez por asistente que cobró, sin importar los días.
- Solo entran partes cerrados. Un parte que se cierra después entra en la liquidación siguiente.
- Una participación no se paga dos veces. Al cerrar, los montos se congelan, y las correcciones posteriores entran como ajuste.
- No se cierra una liquidación con participaciones sin tarifa o capacitaciones sin monto.
- No se puede crear una tarifa con fecha anterior a una liquidación cerrada.
- Registrar el pago genera un recibo con número correlativo y firma. El recibo no se edita: se anula con motivo.
- Tesorería y Estadística ven el resumen; el Jefe sin Tesorería no. Estadística no puede cargar tarifas, cerrar ni registrar pagos. «Mis pagos» muestra solo lo propio.
- El pago con cheque exige número y banco.
- Sin Tesorería, no se puede editar `monto_pago` de la capacitación.
