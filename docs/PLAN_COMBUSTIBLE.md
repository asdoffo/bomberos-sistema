# Plan: Gestión de combustible por unidad

Planificado el 14/11/2026 y actualizado con las respuestas del usuario. Todavía no está implementado.
Mock: `docs/mock_combustible.html` (abrir en el navegador). Tiene 3 pantallas: resumen por unidad, detalle de un móvil y la sección de combustible del parte.

Objetivo: para cada unidad, ver el combustible cargado, los km recorridos entre cada carga y el gasto por km.

---

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Dónde se registra | **En el parte de siniestro**: si se cargó combustible, los litros, el costo y los km actuales del móvil (km de regreso). Toda salida de un móvil, incluida ir a cargar, tiene su parte (por ejemplo, «Mantenimiento / tareas generales» u «Organización funcional»). **No hay pantalla de carga aparte ni tabla nueva.** |
| Forma de pago | Cuenta corriente o tarjeta. |
| Tanque lleno | Se puede llenar, pero a veces no se llena: se agrega la casilla «se llenó el tanque» y las cargas parciales se suman al tramo siguiente. |
| Horas de bomba | No se usan hoy, pero se deja el **campo disponible** (opcional) en el parte. No entra en los cálculos. |
| Combustible en bidones (motobombas, generadores, motosierras) | **Queda afuera**: no se registra en el parte ni en este módulo. |
| Presupuesto mensual | No, por ahora. |
| Quién ve el resumen y el detalle | **Jefe, Graduado y Estadística**, con un permiso nuevo `ver_combustible`. El Personal de guardia no (sigue cargando los datos en el parte, como hoy). |

---

## Situación actual (revisado en la base el 14/11/2026)
- El parte ya tiene: `carga_combustible` (casilla), `litros`, `pesos` (costo), `combustible_nivel` y `km_regreso`. El km de la carga es el km de regreso del parte.
- **Datos incompletos**: de los 3 partes con «carga» tildada, 2 no tienen litros ni costo. Hoy nada lo impide.
- No se registra si se llenó el tanque, ni la forma de pago.

---

## Método de cálculo (criterio estadístico)

1. **De tanque lleno a tanque lleno.** Al llenar el tanque, los litros que entran son los que se gastaron desde el llenado anterior:
   - consumo del tramo = litros cargados hasta este llenado (incluidas las cargas parciales intermedias) / km desde el llenado anterior;
   - las **parciales** no tienen consumo propio: se suman al tramo del próximo llenado.
2. **Unidades**:
   - **L/100 km** (también km/L si se prefiere);
   - **$/km** = costo del tramo / km del tramo;
   - **$/L** = costo / litros, para seguir el precio.
3. **Período**:
   - consumo = Σ litros / Σ km, **sin** la primera carga, que es el punto de partida;
   - no se promedian los consumos de cada tramo;
   - el gasto del período incluye todas las cargas.
4. **Sin llenados marcados** (los partes viejos): se usa la aproximación Σ litros posteriores a la primera / (km de la última − km de la primera), marcada como «estimado».
5. **Autobombas**: la bomba consume con el camión parado, sin sumar km.
   - Se comparan con ellas mismas: su mediana y su evolución mes a mes.
   - Se marcan los tramos con partes de **Incendio** de ese móvil, como aproximación del uso de bomba.
   - Si algún día se cargan las horas de bomba, se puede pasar a L/hora.
6. **Valores fuera de lo normal**: un tramo que supera en más de 30% la mediana del móvil, sin incendios en el tramo que lo expliquen, se marca. Puede ofrecer «Reportar tarea» (ver el plan de tareas pendientes).
7. **Pocos datos**: con menos de 2 llenados no se calcula el consumo, y se muestra «faltan datos».

**Qué partes entran en el cálculo:**
- Solo partes con móvil, km de regreso y «se cargó combustible» con litros.
- Los partes sin carga **sí** aportan km (sirven para saber cuánto anduvo el móvil), pero el consumo se mide siempre entre cargas.

---

## Cambios en la base de datos (solo en `ParteSiniestro`)
- `tanque_lleno`: booleano, **marcado por defecto** cuando se tilda «se cargó combustible». Para los partes existentes queda en `False` (desconocido), y por eso esos datos salen como «estimado».
- `forma_pago_combustible`: cuenta corriente o tarjeta.
- `horas_bomba`: decimal opcional, para registrar el uso de la bomba si algún día se usa. Por ahora no entra en los cálculos.
- `Vehiculo.capacidad_tanque` (litros, opcional): sirve para validar que los litros de una carga no superen el tanque.

## Reglas nuevas en el parte
- Con «se cargó combustible» tildado:
  - **litros y costo son obligatorios para cerrar el parte** (se agregan a `problemas_para_cerrar()`);
  - hace falta móvil y km de regreso.
  - Esto evita que se repitan los partes con carga sin litros.
- Los litros no pueden superar `capacidad_tanque`, si está cargada.
- El formulario muestra, antes de guardar, el precio por litro y el consumo desde el último llenado (ver el mock).
- Si se destilda «se cargó combustible», se borran los litros, el costo, el tanque lleno y la forma de pago, igual que hoy con la salida cancelada.

---

## Pantallas

1. **Resumen por unidad** (filtros de fechas, tipo de unidad y combustible):
   - indicadores: litros, gasto, km, precio promedio por litro y unidades con consumo fuera de lo normal;
   - tabla por móvil, ordenable: cargas, litros, gasto, km, L/100 km, $/km, última carga y marcas («autobomba», «tramo alto», «faltan datos», «estimado»);
   - gráficos de consumo por unidad (barras, con las autobombas en otro color) y gasto por mes;
   - gasto por forma de pago (cuenta corriente / tarjeta), para conciliar con la estación y el resumen de la tarjeta;
   - exportación a Excel e impresión.
2. **Detalle de un móvil**:
   - indicadores del período;
   - tabla de cargas: fecha, km, km desde la anterior, litros, tanque lleno o parcial, costo, $/L, L/100 km y $/km del tramo, enlace al parte, forma de pago y tipo de parte;
   - gráfico del consumo por tramo con la mediana del móvil;
   - controles: partes con carga sin litros (los viejos), cargas parciales, evolución del precio.

   Se llega desde el resumen y desde la ficha del móvil.
3. **Sección del parte** («Unidad y combustible», en Completar y Corregir datos): los campos actuales, más tanque lleno, forma de pago, horas de bomba (opcional) y el cálculo en vivo.

---

## Relación con otros planes
- **Estadísticas** (`docs/PLAN_ESTADISTICAS.md`): la pestaña «Mantenimiento y móviles» usa estos cálculos.
- **Tareas pendientes** (`docs/PLAN_TAREAS_PENDIENTES.md`): desde un tramo con consumo alto se puede reportar una tarea para el móvil.

---

## Permisos
- Permiso nuevo `ver_combustible`, asignado en `cargar_catalogos.py` a Jefe, Graduado y Estadística.
- **Cuidado:** `PERMISOS_GRADUADO` se arma a partir de `PERMISOS_GUARDIA`, así que el permiso se agrega al Graduado aparte, **no** en `PERMISOS_GUARDIA`.
- La carga de los datos (en el parte) no cambia: la hace quien completa el parte.

## Preguntas abiertas
Ninguna.

---

## Tests a incluir
- Consumo de tanque lleno a tanque lleno, con cargas parciales intermedias que se suman al tramo siguiente.
- El consumo del período excluye la primera carga y no promedia los tramos.
- Con menos de 2 llenados se muestra «faltan datos». Los partes viejos (sin «tanque lleno») salen como «estimado».
- No se cierra un parte con «se cargó combustible» y sin litros o sin costo.
- Los litros no superan `capacidad_tanque`.
- Al destildar «se cargó combustible» se limpian sus datos.
- Un tramo con más de 30% sobre la mediana se marca solo si no hubo partes de Incendio de ese móvil en el tramo.
- `horas_bomba` se guarda, pero no cambia ningún cálculo.
- Jefe, Graduado y Estadística ven el resumen; la guardia, el Bombero, el Aspirante y el Invitado reciben 403.
