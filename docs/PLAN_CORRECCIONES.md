# Plan: correcciones y mejoras generales

Planificado el 16/11/2026, a partir de una revisión general del sistema (settings, vistas, modelos,
formularios, sesiones, offline, scripts y despliegue). Al momento de la revisión, los **211 tests pasan**.

No es un módulo nuevo: son correcciones sobre lo que ya existe. Ordenadas por importancia, con la
idea de que las de seguridad (1 a 3) se hagan juntas y primero.

---

## 🔴 Seguridad y datos

### 1. Fichas médicas y fotos: exigir permiso, no solo login
**Problema:** en `config/urls.py` todo `/media/` se sirve con `login_required(serve)`. Cualquier
usuario logueado (incluso un Invitado) que tenga o adivine la URL puede bajar la ficha médica de
cualquier integrante y todas las fotos de siniestros. La ficha médica es dato de salud.

**Solución:**
- Vista propia `servir_media(request, path)` que reemplace a `serve` y decida por carpeta:
  - `integrantes/fichas/…` → requiere `view_integrante` (Jefe, Estadística; quien ve la ficha del bombero);
  - `integrantes/…` (fotos de perfil) → cualquier logueado menos Invitado;
  - `partes/…` (fotos de siniestros) → requiere `view_partesiniestro`. **El Invitado no las ve.**
- **Decisión (16/11):** el Invitado no ve fotos ni partes guardados. Hoy tiene `view_partesiniestro`
  en `cargar_catalogos.py`: **se le quita**. Qué puede hacer el Invitado queda por definir; mientras
  tanto, solo entra y no ve nada del sistema.
- En producción real conviene además `X-Accel-Redirect`/`FileResponse`, pero con el volumen del
  cuartel la vista simple alcanza.

**Tests:** un Invitado recibe 403 al pedir una ficha médica por URL; el Jefe la recibe bien.

### 2. Clave inicial: forzar el cambio en el primer login
**Problema:** `crear_usuarios_integrantes` genera para todos la clave `usuario + "_358"`
(`SUFIJO_CLAVE`). Nada obliga a cambiarla: quien conozca el patrón entra a cualquier cuenta
que nunca la cambió.

**Solución:**
- Campo `debe_cambiar_clave` (en `Integrante` o un perfil del usuario), en `True` al crear el usuario.
- Middleware (o chequeo en `ExpiracionTurnoMiddleware`, que ya corre en cada request): si está en
  `True`, redirige a Cambiar clave y no deja usar otra pantalla.
- Al cambiar la clave se pone en `False`.
- Mientras tanto, que el comando genere un sufijo aleatorio por usuario (se imprime igual en el
  listado que se reparte) en lugar del sufijo fijo.

**Tests:** usuario nuevo → cualquier URL redirige a Cambiar clave; después del cambio navega normal.

### 3. Límite de intentos de login
**Problema:** no hay límite de intentos. Con el túnel de cloudflared el login queda expuesto a
internet; combinado con el punto 2, es la puerta de entrada más probable.

**Solución:** `django-axes` (una dependencia, configuración mínima) o un contador propio en la caché
que ya existe (`cache_sistema`): 5 intentos fallidos por usuario+IP → bloqueo 15 minutos, con un
mensaje claro. Registrar los bloqueos en el log (punto 7).

**Tests:** al sexto intento fallido responde bloqueado aunque la clave sea correcta; a los 15 minutos vuelve a dejar.

### 4. `DEBUG` apagado por defecto
**Problema:** `settings.py` usa `DEBUG = os.environ.get('DJANGO_DEBUG', '1') == '1'`: corriendo sin
la variable queda en modo debug con la SECRET_KEY insegura de fallback.

**Solución:** invertir el default (`'0'`), y que el desarrollo sea el que exporte `DJANGO_DEBUG=1`
(agregarlo al README y a un `configuracion_local.ps1` de ejemplo). Si `DEBUG=0` y la SECRET_KEY es
la de fallback, cortar con un error claro al arrancar.

### 5. Fotos en producción: almacenamiento externo
**Problema:** `MEDIA_ROOT` es disco local y el disco de Render free es efímero: cada deploy borra
las fotos de partes y las fichas.

**Decisión (16/11):** la primera etapa de producción es **local (PC del cuartel) con PostgreSQL**,
publicada hacia afuera con un **túnel con nombre de Cloudflare** (link permanente: cuenta gratuita
+ dominio propio + `cloudflared service install` para que arranque con la PC). Con eso las fotos
quedan en el disco de esa PC y **no se pierden**: el punto se resuelve incluyendo `media/` en el
respaldo diario (punto 8).

Si más adelante se pasa a un hosting (Render u otro con disco efímero), ahí sí hace falta
almacenamiento externo para `/media/` (`django-storages` + Cloudflare R2, capa gratuita de 10 GB),
configurado por variable de entorno solo en ese despliegue. La vista del punto 1 sigue controlando
los permisos en los dos casos.

**Ojo:** con link permanente, el login queda expuesto a internet las 24 hs → los puntos 1, 2 y 3
pasan de recomendables a **obligatorios antes de repartir el link**.

### 6. Reseteo de clave por email: sacarlo o hacerlo real
**Problema:** `django.contrib.auth.urls` publica `password_reset`, pero el backend de email es la
consola: el mail «se envía» a ninguna parte, sin error visible. Además casi ningún integrante tiene
email cargado.

**Solución:** quitar esas URL (dejar solo `login/logout/password_change`, que son las que se usan).
La clave olvidada la resuelve el Jefe o el admin, como hoy. Si algún día se quiere el reseteo real,
configurar un backend de verdad.

### 7. Registro de errores (`LOGGING`)
**Problema:** no hay `LOGGING`: un error 500 en el cuartel no queda anotado y nadie se entera.

**Solución:** `LOGGING` con un archivo rotativo (p. ej. `respaldos/errores.log`, 5 archivos de 1 MB)
con nivel WARNING para `django` y para `partes`. Los bloqueos de login (punto 3) y los fallos de
geocodificación también se anotan ahí.

---

## 🟡 Lógica y operación

### 8. Respaldo diario de la base
Hoy la base se copia solo al correr `actualizar.ps1`. La base es el tomo legal.
- Script `respaldar.ps1`:
  - con SQLite: copia `db.sqlite3` a `respaldos\db-AAAA-MM-DD.sqlite3` (`.backup` vía `sqlite3`,
    o copiar con el sistema cerrado);
  - con **Postgres** (la primera etapa de producción): `pg_dump` al mismo esquema de nombres;
  - en los dos casos, copia también la carpeta **`media/`** (las fotos y fichas viven ahí, no en la base);
  - borra los respaldos de más de 60 días.
- Tarea programada de Windows (la crea `instalar.ps1`) que lo corre todas las noches.
- Recordatorio mensual (en el menú del Jefe o a mano): copiar la última a un pendrive o Drive,
  **fuera de la PC**.
- Si se usa Render: el Postgres free **se borra a los 90 días**; anotarlo en el README.

### 9. Comando `verificar_sellos`
Existen las cadenas de sellos (`Firma`, `AnotacionLibroGuardia.verificar_cadena`) pero nada las
recorre periódicamente.
- `manage.py verificar_sellos`: recorre todas las cadenas y termina con error si alguna no verifica.
- Se corre en cada `actualizar.ps1` y en el respaldo diario (punto 8).
- Si falla, aviso en el menú del Jefe: «La verificación de integridad del DD/MM falló».

### 10. Dividir `views.py` y `forms.py`
`views.py` (1.758 líneas) y `forms.py` (1.151) ya son difíciles de navegar, y los 8 módulos
planificados van a caer ahí.
- `partes/views/` como paquete: `partes.py`, `personal.py`, `capacitaciones.py`, `mantenimientos.py`,
  `ordenes.py`, `libro.py`, `comunes.py`. Igual con `forms/`.
- Sin cambiar comportamiento: solo mover y re-exportar desde `__init__.py` para no tocar `urls.py`
  ni los tests de entrada.
- **Hacerlo antes de implementar los módulos nuevos.**

### 11. Aviso de listado cortado
`listado_partes` corta en 200 (`partes[:200]`) sin avisar. Con «todas las fechas» y años de datos,
el usuario no sabe que hay más. Mostrar «Se muestran los primeros 200 de N; afiná la búsqueda o
elegí un año» cuando el corte actúa. (Paginación completa: no hace falta por ahora, el árbol por
fecha ya acota.)

### 12. Tests en automático (CI)
Workflow de GitHub Actions que corra `manage.py test` en cada push. Evita subir una rotura y
el cuartel actualiza con `actualizar.ps1` sabiendo que lo que baja pasó los tests.

### 13. Limpieza de la raíz del repositorio
`cloudflared.exe`, `cloudflared.log`, `db.sqlite3.bak-…` no deberían viajar con el código:
- `cloudflared.exe` lo descarga `instalar.ps1` si no está (o va en una carpeta `herramientas/` ignorada);
- `*.log`, `*.bak-*`, `respaldos/` al `.gitignore`.

---

## 🎨 Gráfico

### 14. Extraer el CSS de `base.html`
766 líneas de CSS inline que cada página descarga entera cada vez. Moverlo a
`partes/static/partes/estilos.css`: con WhiteNoise queda comprimido y cacheable — importa justo en
el caso real de señal débil. `{% block estilos %}` para lo específico de cada pantalla.

### 15. Mejoras de la carga del parte
Las 13 sugerencias de `docs/mock_parte_mejorado.html` (índice con estado por sección, personal
arriba, damnificados plegables, mapa de confirmación, botones de horas, barra de guardado…).
Van en su propio plan: `docs/PLAN_MEJORAS_PARTE.md`, cuando el usuario elija cuáles.

### Lo que no se toca
Responsive e impresión están bien resueltos. El estilo general (tarjetas, chips, colores) es
consistente; los mocks de los módulos nuevos ya lo siguen.

---

## Orden sugerido

| Tanda | Qué | Esfuerzo |
|---|---|---|
| 1 | Seguridad: 1 (media con permiso), 2 (clave inicial), 3 (intentos), 4 (DEBUG) | una tarde |
| 2 | Operación: 7 (logging), 8 (respaldo diario), 9 (verificar_sellos), 13 (limpieza) | un día |
| 3 | Base para crecer: 10 (dividir views/forms), 12 (CI), 11 (aviso de 200), 14 (CSS) | 1–2 días |
| 4 | Producción: 5 (fotos a R2), 6 (sacar password_reset), 15 (mejoras del parte) | medio día + el plan del parte |

## Preguntas abiertas
Ninguna. Queda anotado que el rol **Invitado** todavía no tiene definido qué puede hacer; por ahora
se le quita lo único que tenía (`view_partesiniestro`).

## Tests a incluir
- Invitado → 403 en ficha médica, en fotos de partes y en el listado de partes; Jefe → 200 en todo.
- Usuario con clave inicial → redirigido a Cambiar clave en cualquier URL; tras cambiarla navega normal.
- 6° intento fallido de login → bloqueado 15 min, aun con clave correcta.
- Arranque con `DEBUG=0` y SECRET_KEY de fallback → error claro.
- `verificar_sellos` → OK con datos sanos; falla si se altera una firma a mano.
- Listado con más de 200 resultados → muestra el aviso.
- Después de dividir `views.py`/`forms.py`: toda la suite sigue pasando sin tocar los tests.
