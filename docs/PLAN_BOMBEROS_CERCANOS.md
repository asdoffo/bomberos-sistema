# Plan: Sugerir bomberos que viven cerca del siniestro

Planificado el 14/11/2026 y actualizado con las respuestas del usuario. Todavía no está implementado.
Mock: `docs/mock_bomberos_cercanos.html` (abrir en el navegador). Tiene 4 pantallas:
1. nuevo parte con los sugeridos y el mapa;
2. domicilio en la ficha del bombero;
3. domicilios sin ubicar;
4. configuración.

Objetivo: al cargar la dirección de un parte, sugerir los integrantes que viven dentro de un radio configurable (500 m por defecto), con su domicilio y teléfono, en una lista y en un mapa, junto con la distancia del cuartel al siniestro.

---

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Quién ve las sugerencias | **Personal de guardia, Graduado y Jefe**: quienes crean y corrigen partes. **Estadística no**: la sugerencia sirve en la emergencia, al crear el parte, y Estadística trabaja con datos ya generados. |
| Estados que se sugieren | **Solo Activo y Aspirante** (fijo, no configurable). |
| Botón «Convocar» | **No.** Solo se muestra la lista para llamar. Hay un botón para **mostrar u ocultar** la lista sugerida. |
| Carga de domicilios | **Ya están cargados en el perfil del integrante** (`domicilio`, `localidad`, `telefono`). No hace falta una pantalla de carga: solo ubicarlos en el mapa. |
| Otras localidades | Si el domicilio queda dentro del radio, se sugiere **sin importar la ciudad** (Villa Nueva, zona rural…). |
| Distancia del cuartel | **Sí**: se muestra la distancia cuartel → siniestro, y se marca a quien vive más cerca del siniestro que el cuartel. |
| Radio | Configurable por el admin; 500 m por defecto. |

---

## Situación técnica (revisado el 14/11/2026)
- **A verificar en producción:** en la base local (`db.sqlite3`) y en su copia (`db.sqlite3.bak-20261110`), los 142 integrantes tienen `domicilio`, `telefono` y `email` vacíos. Según el usuario, los datos están cargados en el perfil, así que seguramente están en la base de producción. Antes de implementar, confirmar que esos campos tienen datos.
- `Integrante.domicilio` es texto libre y **no tiene coordenadas**.
- El parte sí guarda `latitud` y `longitud`, obtenidas con `geocodificacion.py` (Nominatim y Georef).
  - **Ojo:** el buscador del parte está **limitado a Villa María** (`GEOCODIFICACION_AREA` con `bounded=1`, y Georef con la localidad censal de Villa María). Para los domicilios de otras localidades hay que buscar con la **localidad del integrante** y sin ese límite.
- El sistema no usa ninguna biblioteca de mapas, ni tiene una tabla de configuración.

---

## Cómo funciona

1. **Ubicar los domicilios** (una vez, y después cada vez que cambian):
   - Un comando `ubicar_domicilios` recorre los integrantes Activos y Aspirantes con domicilio y guarda `latitud` y `longitud`. Respeta el límite de Nominatim de 1 consulta por segundo: con 142 integrantes tarda unos 3 minutos.
   - Al editar el domicilio o la localidad en la ficha, se vuelve a ubicar solo, salvo que se haya ubicado a mano.
   - Si no se encuentra (zona rural, sin altura), queda en la lista de **domicilios sin ubicar**, para ubicarlo a mano moviendo el punto en el mapa.
2. **Calcular la distancia.** Cuando la dirección del parte queda ubicada, se calcula la distancia en línea recta (Haversine) del siniestro a cada integrante con coordenadas, y del siniestro al **cuartel**. Se hace en Python; son pocos integrantes.
3. **Mostrar la lista.**
   - Primero, los que están dentro del radio, ordenados por distancia.
   - Después, si hay pocos o ninguno, los **N más cercanos fuera del radio** (configurable), separados.
   - Cada uno se marca **«más cerca que el cuartel»** si corresponde.
   - Es solo información para llamar: el guardia carga al convocado en la grilla de personal como siempre.

**Quién se sugiere:**
- integrantes en estado Activo o Aspirante;
- con coordenadas;
- con `sugerible` tildado;
- que no estén ya cargados en el parte.

**Advertencia:** la distancia es en línea recta, no por calle. Para radios de 500 m a 2 km alcanza.

---

## Datos

### Cambios en `Integrante`
- `latitud` y `longitud` (decimales, opcionales).
- `ubicacion_manual`: booleano. Si el punto se movió a mano y después cambia el domicilio, se avisa en lugar de volver a ubicarlo solo.
- `sugerible`: booleano, verdadero por defecto, «puede ser sugerido para salidas cerca de su domicilio». Sirve para quien no quiere aparecer.

### `Configuracion` (tabla nueva, un solo registro, editable en el admin)
- `radio_sugerencia_m`: entero, **500** por defecto, entre 100 y 5.000.
- `cercanos_fuera_de_radio`: entero, 3 por defecto; 0 = no mostrar.
- `cuartel_latitud` y `cuartel_longitud`: la ubicación del cuartel (General Paz 358), ubicada una vez y corregible.
- Queda como tabla general de configuración, para otros parámetros que hoy están fijos en el código.

---

## Pantallas

1. **Nuevo parte y Corregir datos** (debajo de la ubicación), solo para guardia, Graduado y Jefe:
   - aparece cuando la dirección queda ubicada;
   - **distancia cuartel → siniestro**;
   - **botón mostrar / ocultar** la lista; cada navegador recuerda la última elección;
   - selector rápido de radio (500 / 1.000 / 2.000 m) para esa consulta, sin cambiar la configuración;
   - lista: distancia, nombre, rango, grupo de guardia, si es chofer, «más cerca que el cuartel», domicilio, legajo y teléfono (enlace para llamar y WhatsApp);
   - **mapa**: el siniestro, el círculo del radio, el cuartel con la línea de su distancia, y las casas (verde dentro del radio, gris fuera). Al tocar un punto se ve el nombre, el domicilio y el teléfono;
   - aviso de cuántos Activos y Aspirantes no tienen el domicilio ubicado.
2. **Ficha del bombero**: muestra si el domicilio quedó ubicado, con «Corregir en el mapa» y la casilla «Puede ser sugerido».
3. **Domicilios sin ubicar**: Activos y Aspirantes sin domicilio, sin teléfono o no encontrados en el mapa, con «Ubicar a mano». Tiene un botón para ubicar a todos.
4. **Configuración** (admin de Django): radio, cercanos fuera del radio y ubicación del cuartel.

### Mapa
- **Leaflet**, guardado en `partes/static/` (sin CDN), con las teselas de **OpenStreetMap**, sin clave. Lleva la atribución «© OpenStreetMap».
- Sin señal, el mapa no carga, pero **la lista se ve igual**, porque las distancias se calculan en el servidor.

---

## Privacidad
- Las sugerencias, con domicilio y teléfono, las ven **solo guardia, Graduado y Jefe**, y solo en las pantallas de crear o corregir un parte.
- Domicilio y teléfono no salen en impresiones, exportaciones ni en la constancia del parte.
- No se guarda en el parte a quiénes se sugirió.
- Se respeta `sugerible = False`.

---

## Preguntas abiertas
Ninguna. Solo queda **verificar en la base de producción** que `domicilio`, `localidad` y `telefono` tienen datos (en la copia local están vacíos).

---

## Tests a incluir
- La distancia con Haversine da bien para puntos conocidos (±1%).
- Se sugieren solo Activos y Aspirantes, con coordenadas, con `sugerible`, que no estén ya en el parte, y ordenados por distancia. Auxiliares y Retirados nunca.
- Un integrante de otra localidad dentro del radio se sugiere.
- El radio y los «cercanos fuera de radio» salen de `Configuracion`.
- Se calcula la distancia cuartel → siniestro y se marca bien «más cerca que el cuartel».
- Al ubicar domicilios se usa la localidad del integrante, no el límite de Villa María.
- Al cambiar el domicilio se vuelve a ubicar, salvo `ubicacion_manual` (en ese caso, se avisa).
- Con el buscador caído, el domicilio se guarda sin coordenadas y nada se rompe.
- La guardia, el Graduado y el Jefe ven las sugerencias. Estadística, Bombero, Aspirante e Invitado no.
- Domicilio y teléfono no salen en impresiones ni exportaciones.
