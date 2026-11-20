# Sistema de Partes de Siniestro — Bomberos Voluntarios de Villa María

Guía para presentar el avance al tutor y al cuartel (estado al 16/10/2026).
Diagramas: [`der_agregados.png`](der_agregados.png) (solo lo agregado) y [`der_completo.png`](der_completo.png).

---

## 1. Qué tenemos hoy (contra lo que se pidió)

| Lo que pidió el cuartel / el SRS | Estado | Cómo se ve en el sistema |
|---|---|---|
| Alta del parte por el guardia, con N° de control correlativo por año (ej. 13/2026), automático y no editable | **Hecho** | "Nuevo parte". El número lo asigna el sistema al guardar. |
| Fecha y hora de llamada que no se tipean ni se cambian | **Hecho** | Se toman del reloj de la PC al abrir la pantalla. Después quedan fijas: en Completar solo se ven. |
| Alta completa (no se crea un parte a medias) | **Hecho** | Obligatorios: calle **con altura**, barrio, tipo, motivo y teléfono (característica + número). |
| Dirección rápida | **Hecho** | Sugiere calles de Villa María mientras se escribe (sin internet). El barrio aparece al instante si ya hubo un parte cerca; el mapa (OpenStreetMap) lo confirma. |
| Salida que se puede cancelar (horarios y km no obligatorios) | **Hecho** | Casilla "La unidad salió": si se destilda, se vacían horarios y km. |
| Completar en el lugar desde el celular, **sin señal**, y sincronizar al volver | **Hecho y probado con un celular real** | "Descargar en este celular" → modo avión → Completar → Guardar → al volver la señal se envía solo. |
| Fotos del hecho desde el celular | **Hecho** | Se sacan sin señal, quedan en el celular y se suben solas. El guardia las ve en el detalle. |
| Vehículos siniestrados (1° a 4°), damnificados con triage, entidades que intervinieron | **Hecho** | Completar sigue el orden de la planilla "Control de salida". |
| Personal que concurrió por **número de mérito** (cambia cada año) | **Hecho** | Grilla de números, como en el papel. |
| Negativa a la atención con constancia imprimible | **Hecho** | Desde el detalle del parte. |
| Cierre del parte por el guardia, con controles | **Hecho** | No deja cerrar si falta, por ejemplo, el personal. Actualiza el km del móvil. |
| Cada bombero completa **una sola vez**; corrige solo el guardia, también cerrado | **Hecho** | El bombero ve "Ya enviaste tus datos". El guardia ve "Corregir datos" / "Corregir parte cerrado". |
| Datos válidos (DNI, edad, teléfono, patente, nombres) | **Hecho** | Ver sección 3. |
| Roles: guardia, jefe, instructor, administración | **Hecho** | Grupos de Django con permisos. |
| Sesión de 6 h por cambio de guardia | **Hecho** | Solo para el Personal de guardia; los bomberos con celular quedan logueados 14 días. |
| Rangos por escalafón: oficiales, suboficiales, bomberos; el Comandante General es la cabeza | **Hecho** | Catálogo de rangos con escalafón; el perfil muestra "Comandante General (Oficiales, cabeza del cuerpo)". |
| Ver y cargar los bomberos con los datos de **RUBA** | **Hecho** | Sección "Bomberos" (jefe y administración): listado con búsqueda y filtros por escalafón y estado, ficha completa y alta/edición con familiares e historial. |
| **Métricas de participación por bombero** | **Hecho (por integrante)** | En la ficha: salidas por año, veces como guardia, salidas del año por tipo de siniestro y últimas salidas. En el listado, las salidas del año de cada uno. **Falta** un resumen anual de todo el cuerpo (totales por tipo de siniestro, para planificar capacitaciones). |
| Los otros 5 procesos (mantenimiento, capacitación, itinerario, cursos) | Fase 2 | Documentados, sin empezar (decisión de alcance del MVP). |

### Preguntas frecuentes
- **¿Policial · Tránsito · Forense · Juzgado estaba en el papel?** Sí: es un bloque de la planilla
  "Control de salida" (Sí/No, N° de móvil, A cargo). Lo mismo **U N°, Km regreso, Combustible nivel,
  Carga, Litros, Pesos**. Se respetó el vocabulario y el orden de la planilla.
- **¿Se pueden ver métricas de un bombero?** Sí: el jefe y administración entran a "Bomberos" →
  ficha del integrante → "Participación" (salidas por año, como guardia, por tipo de siniestro, últimas
  salidas). Cada integrante ve las suyas en "Mi perfil". Falta el resumen anual de todo el cuerpo.
- **¿Qué datos de RUBA tiene la ficha?** Identificación, contacto, salud (grupo y factor, donante
  Sí/No/Indefinido, ficha médica), carrera (rango con escalafón, cargo, fechas de ingreso, cadete,
  aspirante, bombero y último ascenso), instructor y forestal (tipo y fecha de certificación),
  formación, si tiene hijos, **familiares** (vínculo, apellido, nombre, DNI, nacimiento, vive,
  observaciones) e **historial** (desde, cuerpo, estado, formación, motivo de baja, fin de licencia).
  Quedó afuera la "jerarquía (campo viejo)" de RUBA.

---

## 2. Qué se agregó al DER original

Ver [`der_agregados.png`](der_agregados.png): amarillo = entidad nueva, celeste = entidad con atributos
nuevos, en rojo el atributo.

| Agregado | Para qué |
|---|---|
| **CENTRO_SALUD** (nueva) | Catálogo de hospitales y clínicas. Antes "Hacia" era texto libre; ahora **DAMNIFICADO.hacia** apunta a esta tabla (no se escribe distinto cada vez). Idea tomada del sistema médico Shaman, a pedido del tutor. |
| **BARRIO** (nueva) | Catálogo de barrios de Villa María con su coordenada, para sugerir y normalizar el barrio. |
| **DESCARGA_OFFLINE** (nueva) | Qué celulares se llevaron cada parte y cuándo enviaron por última vez. El guardia lo ve antes de cerrar. |
| **ENVIO_COMPLETAR** (nueva) | Cada guardado de Completar con un id único: evita aplicar dos veces el mismo envío (si se corta la señal a mitad), registra **quién y cuándo** (auditoría, y la regla de "una sola vez") y guarda los avisos cuando dos equipos cargaron datos distintos. |
| **FAMILIAR** (nueva) | Grupo familiar del integrante, como en RUBA. |
| **HISTORIAL_INTEGRANTE** (nueva) | Pases entre cuerpos, licencias y bajas, como en RUBA. |
| INTEGRANTE: **ficha_medica, tiene_hijos, formacion, fecha_cadete, fecha_aspirante, fecha_bombero, cargo, es_bombero_forestal, tipo_forestal, fecha_certificacion_forestal** | Datos de la ficha de RUBA que faltaban. **donante_sangre** pasó de Sí/No a Sí/No/Indefinido. |
| RANGO.**escalafon** | Oficiales / Suboficiales / Bomberos. |
| INTEGRANTE.**id_usuario** | Vincula el integrante con su usuario del sistema (así el guardia del parte es el usuario logueado). |
| INTEGRANTE.**matricula_profesional** | Matrícula de quien atiende (enfermeros, médicos), para la negativa. |
| NEGATIVA_ATENCION: **tipo_firmante, id_atendio, matricula_atendio, testigo_nombre, testigo_dni** | Constancia legal más completa (paciente, responsable o testigo; quién atendió). Idea de Shaman. |
| Base **CapturaOffline** (UUID + fecha_captura_local + sincronizado) | Ya estaba en el DER para las tablas "verdes"; en el código es una clase base común que heredan 7 tablas. |

---

## 3. Cómo se resolvió cada cosa y dónde tocarla si piden un cambio en el acto

> Ruta de los archivos desde la carpeta del proyecto. Después de cambiar código Python hay que
> reiniciar el servidor (`Ctrl+C` y `runserver` de nuevo). Las plantillas (`.html`) se ven al recargar.

### Cambios que se hacen **sin programar** (panel de administración `/admin/`)
| Si piden… | Dónde |
|---|---|
| Agregar un tipo de siniestro, motivo, entidad (ej. "Defensa Civil"), barrio, hospital, móvil, rango | Admin → la tabla correspondiente → "Agregar" |
| Dar de alta un usuario y asignarle rol | Admin → Usuarios → Grupos ("Personal de guardia", "Jefe de Cuerpo Activo"…) y vincularlo en Integrantes |
| Corregir un dato de un parte cerrado (incluso la hora de llamada) | Admin → Partes de siniestro (solo administrador) |

### Cambios en el código
| Si piden… | Archivo | Qué tocar |
|---|---|---|
| Que un campo del alta sea obligatorio u opcional | `partes/forms.py` | `ParteSiniestroForm.__init__` (ahí están barrio y teléfono) |
| Cambiar el texto de una etiqueta ("TE de donde llaman", "U N°"…) | `partes/forms.py` | `labels` del formulario correspondiente |
| Cambiar el orden de los bloques de Completar | `partes/templates/partes/completar_parte.html` | Mover el bloque `<div class="card">` |
| Característica telefónica por defecto (353) o cantidad de dígitos | `partes/validadores.py` | `CARACTERISTICA_LOCAL`, `DIGITOS_TELEFONO` |
| Edad máxima, formato de DNI o de patente | `partes/validadores.py` | `EDAD_MAXIMA`, `dni`, `patente` |
| Qué falta para poder cerrar un parte | `partes/models.py` | `ParteSiniestro.problemas_para_cerrar` |
| Quién puede corregir (hoy guardia y jefe) | `partes/views.py` | `puede_corregir` |
| Que el bombero pueda completar más de una vez | `partes/views.py` | `_guardar_completar` (la línea con `ya_completo`) |
| Duración de la sesión (06:00 el guardia, 12 h, 10 min) | `config/settings.py` | `SESION_HORA_CAMBIO_GUARDIA`, `SESION_HORAS_FIJA`, `SESION_HORAS_INACTIVIDAD`, `SESION_MINUTOS_CORTA` |
| Qué regla de sesión tiene cada rol, y qué rol manda | `partes/sesion.py` / `partes/roles.py` | `REGLAS` / `PRIORIDAD` |
| Rangos y escalafones de la carga inicial | `partes/management/commands/cargar_catalogos.py` | `ESCALAFONES` |
| Permisos de cada rol | `partes/management/commands/cargar_catalogos.py` | `ROLES` (después correr `manage.py cargar_catalogos`) |
| Qué datos tiene la ficha del bombero y en qué orden | `partes/forms.py` | `SECCIONES_FICHA` (la usan el formulario y la ficha) |
| Quién ve la sección Bomberos | Admin → Grupos | Permiso "Can view integrante" (hoy: jefe y administración) |
| Colores de la interfaz | `partes/templates/partes/base.html` | variables `--rojo`, `--azul`… en `:root` |
| Cuánto espera el celular antes de usar la copia sin señal (10 s) | `partes/templates/partes/sw.js` | `ESPERA_MAXIMA` |
| Cuánto tarda en buscar el barrio después de escribir la altura | `partes/templates/partes/crear_parte.html` | `setTimeout(buscarBarrio, 250)` |

### Cómo funciona cada parte (para explicarlo)
- **Número correlativo por año**: al guardar, el modelo toma el máximo del año y suma 1, dentro de
  una transacción y con reintentos si dos guardias guardan a la vez (`ParteSiniestro.save`).
- **Hora de llamada**: el navegador manda la hora de la PC en un campo oculto; si difiere más de 2 h
  del servidor se usa la del servidor (para que no se pueda "atrasar" una llamada).
- **Dirección y barrio**: lista local de calles (sin internet) + historial de partes anteriores
  (barrio al instante) + OpenStreetMap/Georef (confirmación). Lo consultado queda guardado en la base
  (caché) para responder al instante la próxima vez.
- **Sin señal**: un *service worker* guarda las pantallas del parte descargado. Lo que se guarda va
  a una base del navegador (IndexedDB) y se envía al volver la señal. Al sincronizar, el servidor
  compara "cómo estaba el parte al descargarlo" con "cómo quedó" y aplica solo lo que cambió ese
  celular: el personal y los rodados se **suman**; en los datos sueltos (horarios, km) **gana el
  último**, y queda un aviso visible para el guardia (`partes/offline.py`).
- **Fotos sin señal**: quedan en el celular con un id propio; si se reenvían no se duplican.
- **Una sola vez**: cada envío queda en ENVIO_COMPLETAR con el usuario; si ya hay uno de ese bombero,
  el sistema no acepta otro. El guardia y el jefe no tienen ese límite.

---

## 4. Patrones de diseño que se usaron

| Patrón | Dónde | Para qué |
|---|---|---|
| **MVT (Modelo–Vista–Template)**, la variante de MVC de Django | Todo el proyecto: `models.py`, `views.py`, `templates/` | Separar datos, lógica y pantallas. |
| **Modelo de dominio rico (Active Record)** | `ParteSiniestro.cerrar`, `problemas_para_cerrar`, `save` (correlativo), tiempos operativos | Las reglas de negocio viven en el modelo y valen igual desde las pantallas, el celular y el admin. |
| **Herencia abstracta (Template)** | `CapturaOffline` → 7 tablas | Los campos de captura sin señal se definen una vez. |
| **Mixins (composición)** | `ControlDeDatos`, `TelefonoConCaracteristica` en `forms.py` | Agregar validación y teclado numérico a varios formularios sin repetir código. |
| **Decorador** | `requiere_permiso`, `login_required`, `require_POST` en `views.py` | Controles de acceso que se "ponen encima" de cada vista. |
| **Cadena de responsabilidad (Middleware)** | `ExpiracionTurnoMiddleware` en `sesion.py` | Revisa el vencimiento del turno en cada pedido antes de llegar a la vista. |
| **Observador (señales)** | `registrar_inicio` escucha `user_logged_in` | Anotar la hora de login sin tocar el código de login de Django. |
| **Adaptador / Gateway** | `geocodificacion.py` (`_pedir_json`) | Aísla los servicios externos (OpenStreetMap, Georef); en los tests se simulan. |
| **Cache-aside** | `buscar_direccion` | Primero busca en la caché; si no está, consulta el mapa y la guarda. |
| **Bandeja de salida (Outbox) / Offline-first** | IndexedDB `pendientes` y `fotos` en `base.html` | Lo cargado sin señal espera en el celular y se envía en orden al volver. |
| **Clave de idempotencia** | `_envio` / ENVIO_COMPLETAR, UUID de filas y fotos | Reenviar el mismo guardado no duplica nada. |
| **Combinación de 3 vías (three-way merge)** | `partes/offline.py` | Igual que Git: base + cambios de un equipo + estado actual del servidor. |
| **Command** | `manage.py cargar_catalogos`, `actualizar_calles` | Tareas de carga repetibles e idempotentes. |
| **Control de acceso por roles (RBAC)** | Grupos y permisos de Django (`ROLES`) | Cada rol ve y hace solo lo que le corresponde. |

---

## 5. Medidas de seguridad

| Riesgo | Medida |
|---|---|
| Acceso sin usuario | Todas las pantallas exigen login. Las fotos (`/media/`) solo se ven logueado, también en producción. |
| Que alguien haga algo que su rol no permite | Permisos por rol (crear, cerrar, ver confidencial); la descripción confidencial solo la ven jefe y administración. El bombero completa una vez; corregir es del guardia. |
| PC del cuartel que queda con la sesión abierta | La sesión del guardia vence a las 6 h del login, haya actividad o no. |
| Falsificación de pedidos (CSRF) | Token CSRF en todos los formularios y en los envíos del celular (`X-CSRFToken`). |
| Inyección de código en pantalla (XSS) | Las plantillas escapan todo; el JavaScript pinta con `textContent` y los datos viajan con `json_script`. |
| Usar el servidor para atacar a otros (SSRF) | La búsqueda de direcciones solo consulta URLs fijas y corta el texto a 150 caracteres. |
| Datos inválidos o maliciosos | Validación en el servidor (modelos y formularios) además del navegador: DNI, edad, teléfono, patente, nombres, km de regreso mayor al del móvil, solo imágenes de hasta 15 MB. |
| Manipular la hora de llamada | Tolerancia de 2 h contra el reloj del servidor; después del alta no se puede cambiar desde la pantalla. |
| Datos duplicados o a medias | Transacciones atómicas al guardar; ids únicos por envío, fila y foto. |
| Modificar un parte cerrado | Bloqueado para todos salvo guardia/jefe, y cada corrección queda registrada con usuario y fecha (ENVIO_COMPLETAR). |
| Contraseñas | Guardadas con hash (PBKDF2, de Django) y validadores de contraseña. |
| Producción | `DEBUG` apagado, clave secreta por variable de entorno, cookies `Secure`, `ALLOWED_HOSTS` y orígenes CSRF de confianza; se revisa con `manage.py check --deploy`. El modo sin señal exige HTTPS. |
| Servicios externos | Tiempo máximo de 5 s, 1 consulta por segundo a OpenStreetMap (su política) y si fallan se carga a mano. |

---

## 6. Lo que sigue
1. Resumen anual de todo el cuerpo (totales por tipo de siniestro y por integrante, para planificar).
2. Despliegue real con HTTPS y fotos en un almacenamiento persistente.
3. Validar con el cuartel: catálogos, barrios, texto legal de la negativa, permisos por rol.
4. Fase 2: los otros procesos en papel.
