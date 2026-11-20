# Plan: módulo de Estadísticas

Planificado el 14/11/2026. Todavía no está implementado.
Mock: `docs/mock_estadisticas.html` (abrir en el navegador). Tiene 3 pantallas: el tablero institucional (con las pestañas Resumen, Intervenciones, Capacitación, Mantenimiento y móviles, y Gestión), la tabla de personal y la ficha estadística del bombero.

Objetivo: medir el desempeño de cada bombero y la actividad de la institución (intervenciones, capacitaciones y mantenimientos) en un año calendario.

---

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Acceso | Quien tenga el permiso `ver_estadisticas` (Jefe, Estadística, Graduado) ve todo. El Bombero ve solo sus propios datos. |
| Cobro en mantenimiento | Se agrega el campo `cobro` en la tabla, pero **no** se muestra en la pantalla web del mantenimiento (solo en el admin). |
| Apresto | Cuenta como participación, pero **no** suma horas de intervención. |
| Capacitaciones de varios días | Se cargan los días, cada uno con su horario. |
| Horas de capacitación por bombero | Solo suman las horas reales: los días a los que asistió. La asistencia se registra por día. |
| Tipos de mantenimiento | Pasan a ser un catálogo editable. Los tipos los define el usuario. |
| Índice compuesto de desempeño | No se hace por ahora. |

## Pendiente de definir

- [ ] La lista de tipos de mantenimiento. La migración crea al menos «Preventivo» y «Correctivo / reparación»; el resto se carga después desde el admin.
- [ ] Si el **cobro** de una capacitación de varios días va por capacitación (como ahora) o por día. Este plan supone que sigue **por capacitación**.

---

## Criterios estadísticos

1. **Siempre con denominador.** Ejemplo: salidas a las que fue / salidas ocurridas mientras estaba en actividad. Así se puede comparar a quien entró en junio con quien está desde enero.
2. **Tiempos con mediana y percentil 90**, no con promedio: una salida de varias horas deforma el promedio.
3. **Horas-hombre** (duración × personas) como unidad común para sumar intervenciones, capacitaciones y mantenimientos.
4. **Comparar contra el grupo** (mediana del cuerpo y del escalafón), no con un ranking.
5. **Aviso cuando hay pocos casos**: menos de 5 casos en una categoría.
6. **Contar por separado** las salidas canceladas y los partes sin salida.
7. El **rango** de cada participación es el que tenía en ese momento (`PartePersonal.rango`), no el actual.

---

## Etapa 0: Cambios en la base de datos

### 0.1 Catálogo `TipoMantenimiento`
- Modelo nuevo con `descripcion` (única), `activo` y `orden`, igual que `Departamento`.
- En `Mantenimiento.tipo`, el campo de opciones fijas (`Tipo.PREVENTIVO` / `Tipo.CORRECTIVO`) pasa a ser una FK `PROTECT` a `TipoMantenimiento`.
- Migración de datos: crea «Preventivo» y «Correctivo / reparación» y apunta los registros existentes al tipo que corresponde.
- Se administra desde el admin. En los mantenimientos nuevos solo se ofrecen los tipos activos.
- Revisar los formularios, plantillas y tests que usan `Mantenimiento.Tipo`.

### 0.2 Días de capacitación: `DiaCapacitacion`
- Modelo nuevo con:
  - `capacitacion`: FK a `Capacitacion`, `CASCADE`, `related_name="dias"`;
  - `fecha`, `hora_inicio` y `hora_fin`;
  - `UniqueConstraint(capacitacion, fecha)`;
  - `ordering = ["fecha", "hora_inicio"]`.
- Cada día tiene la propiedad `horas`, que usa `horas_entre(hora_inicio, hora_fin)` (contempla que termine pasada la medianoche).
- `Capacitacion.horas` pasa a ser la suma de las horas de sus días.
- `Capacitacion.fecha` se mantiene como **primer día**, porque define `anio` y el número correlativo. Al guardar se sincroniza con el día más temprano.
- `fecha_fin`, `hora_inicio` y `hora_fin` de `Capacitacion` se eliminan después de migrar o quedan calculados a partir de los días. Revisar dónde se usan (listados, ficha, impresión).
- Migración de datos: crea un día por capacitación existente con su `fecha`, `hora_inicio` y `hora_fin`. Si tenía `fecha_fin`, crea un día por cada fecha del rango con el mismo horario.
- Formulario: una grilla de días (formset) para agregar y quitar renglones. Al menos un día obligatorio.
- Validación: no se puede guardar en un período cerrado, y la fecha de cada día tiene que pertenecer al mismo año que la capacitación.

### 0.3 Asistencia por día
- `AsistenciaCapacitacion` se mantiene: una por bombero y capacitación, con su `cobro`.
- Se le agrega `dias = ManyToManyField(DiaCapacitacion, blank=True)`, con los días a los que asistió.
- Horas del bombero = suma de las horas de sus `dias`.
- Validación: los días marcados tienen que ser de la misma capacitación.
- Pantalla: en la grilla de asistentes, una casilla por día, **tildadas por defecto**. Si la capacitación tiene un solo día, las casillas no se muestran.
- Migración de datos: cada asistencia existente queda con todos los días de su capacitación.
- `periodos.resumen_bomberos()` cambia: las horas de capacitación salen de los días asistidos, no de `capacitacion.horas`.

### 0.4 Cobro en mantenimiento
- Se agrega `ParticipacionMantenimiento.cobro = BooleanField(default=False, verbose_name="cobró")`.
- **No** se muestra en el formulario web del mantenimiento. Solo en el admin.
- Por ahora no entra en las métricas. Queda la consulta preparada para cuando se use.

### 0.5 Permiso `ver_estadisticas`
- Se define en `Meta.permissions` de un modelo existente (por ejemplo `Periodo`) o en un modelo nuevo sin tabla.
- Se asigna en `partes/management/commands/cargar_catalogos.py` a **Jefe**, **Estadística** y **Graduado**.

### 0.6 `ResumenPeriodo` ampliado
Para que un año cerrado quede congelado con todas las métricas, se agregan:
- `horas_intervencion` (sin apresto);
- `salidas_cobradas` y `capacitaciones_cobradas`;
- `detalle`: un JSONField con los conteos por tipo de siniestro, por concurrencia, por participación y por tipo de mantenimiento, y las horas de mantenimiento por tipo.

El CSV del tomo (`_resumen_csv`) y la firma del cierre del período se actualizan con los campos nuevos.

---

## Etapa 1: Métricas por bombero

### Lógica
Archivo nuevo `partes/estadisticas.py` con funciones que devuelven diccionarios, como `periodos.py`. `resumen_bomberos()` pasa a usarlas para no duplicar cuentas.

Las funciones reciben un rango de fechas (por defecto el año calendario) y filtros opcionales.

### Métricas por bombero

**Intervenciones**
- Cantidad total.
- Por tipo de siniestro.
- Por concurrencia (en el cuartel o convocado).
- Por participación (interviniente, apresto o chofer).
- Horas de intervención: de la salida al regreso del parte, solo como interviniente o chofer. **El apresto no suma horas.** Los partes con salida cancelada o sin horarios no suman.
- Salidas nocturnas (22 a 6) y de fin de semana.
- Asistencia: salidas a las que fue / salidas con la unidad en la calle mientras estaba en actividad.

**Participaciones cobradas**
- En salidas (`PartePersonal.cobro`) y en capacitaciones (`AsistenciaCapacitacion.cobro`).

**Capacitaciones**
- Cantidad.
- Horas reales (días asistidos).
- Obligatorias asistidas / obligatorias dictadas en el período.
- Veces como instructor (`a_cargo_integrante`) y como ayudante.

**Mantenimientos**
- Cantidad y horas, en total y por tipo.

**Gestión y regularidad**
- Partes como guardia y partes cerrados.
- Meses del período con al menos una actividad.

**Comparación**
- Mediana del cuerpo y de su escalafón para cada métrica, y el percentil del bombero.

### Período en actividad (denominador)
- Se calcula con `fecha_ingreso` y los registros de `HistorialIntegrante` (licencias y bajas).
- Si el historial no está cargado, se toma el período completo y la pantalla lo aclara.

### Pantallas
1. **Tabla de personal** (`ver_estadisticas`):
   - una fila por bombero, ordenable;
   - filtros por año, escalafón, grupo de guardia y estado;
   - las celdas por debajo del percentil 25 o por encima del 75 se resaltan;
   - exportación a CSV/Excel.
2. **Ficha estadística del bombero**:
   - indicadores al lado de la mediana del cuerpo;
   - un gráfico mes a mes;
   - desgloses por tipo, concurrencia, participación y tipo de mantenimiento.

   Quién la ve:
   - con `ver_estadisticas`, la de cualquier bombero (enlace desde la tabla y desde la ficha actual);
   - sin el permiso, solo la propia (sección en Mi perfil). Si intenta abrir la de otro, recibe un **403**.

---

## Etapa 2: Tablero institucional (`ver_estadisticas`)

Arriba, filtros por año, rango de fechas, tipo de siniestro, grupo de guardia y escalafón.

### Pestañas

**Resumen**
- Indicadores con su variación respecto del año anterior: partes, horas-hombre, mediana de respuesta, bomberos con actividad, horas de capacitación y costo de mantenimiento.
- Gráfico de partes por mes.

**Intervenciones**
- Partes por tipo y por motivo.
- Mapa de calor día de la semana × hora.
- Tiempos de despacho (llamada→salida), desplazamiento (salida→llegada), respuesta (llamada→llegada) y servicio (salida→regreso): mediana y P90, abiertos por tipo y por franja horaria.
- Dotación por salida: mediana, proporción en el cuartel / convocados, cantidad de choferes.
- Mapa y barrios más frecuentes.
- Damnificados por triage, sexo y franja de edad; traslados por centro de salud; negativas a la atención.
- Entidades que intervinieron.
- Salidas canceladas y apoyos solicitados.

**Capacitación**
- Cantidad, horas dictadas y horas-hombre.
- Asistencia promedio por capacitación.
- Distribución por nivel, modalidad, dictada/recibida y destino.
- Cobertura: % del cuerpo con al menos X horas.

**Mantenimiento y móviles**
- Mantenimientos por tipo, por móvil o elemento, y por lugar (cuartel o taller).
- Costos (repuestos y mano de obra) y horas-hombre.
- Por móvil: salidas, km recorridos y combustible (litros y pesos).

**Gestión**
- Tiempo desde la llamada hasta el cierre del parte.
- Partes corregidos después de cerrados (firmas `CORRECCION_PARTE`).
- Porcentaje de partes con datos completos: horarios, coordenadas y km.
- Participaciones cobradas por mes y por tipo, para la liquidación.

### Gráficos
- Chart.js guardado en `partes/static/`, **sin CDN**, para que funcione sin señal.
- Barras para categorías; líneas para la evolución mensual; mediana con marca de P90 para los tiempos.
- Aviso de pocos casos cuando una categoría tiene menos de 5.

---

## Etapa 3: Más adelante
- Comparación entre años.
- Alerta de bomberos con 2 meses o más sin actividad.
- Versión imprimible para la memoria anual.
- Índice compuesto de desempeño: descartado por ahora; se puede reconsiderar.

---

## Tests a incluir
- Migraciones de datos:
  - tipos de mantenimiento existentes → catálogo;
  - capacitaciones con y sin `fecha_fin` → días;
  - asistencias existentes → todos los días.
- Horas de una capacitación de varios días, y horas de un bombero que asistió solo a algunos.
- No se puede marcar en una asistencia un día de otra capacitación.
- El apresto cuenta como participación pero no suma horas.
- Horarios que cruzan la medianoche.
- Salida cancelada: no suma horas ni cuenta en los tiempos operativos.
- Un bombero sin `ver_estadisticas` ve su ficha y recibe 403 en la de otro.
- Jefe, Estadística y Graduado ven la tabla y el tablero; Bombero, Aspirante e Invitado no.
- El `cobro` de mantenimiento no aparece en el formulario web.
- El resumen de un período cerrado no cambia aunque después se modifiquen los datos.
