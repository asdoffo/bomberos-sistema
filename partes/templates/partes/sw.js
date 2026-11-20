/* Service worker: permite abrir sin señal los partes descargados en el celular.
   Las páginas se piden siempre al servidor; si no hay señal (o tarda demasiado)
   se muestra la copia guardada, y si no hay copia, la pantalla "Sin conexión".
   Lo que se guarda sin señal no pasa por acá: lo maneja base.html con IndexedDB. */
var CACHE = "partes-offline";
var SIN_CONEXION = "{% url 'sin_conexion' %}";
var ESPERA_MAXIMA = 10000;  // ms; con señal débil es mejor mostrar la copia que esperar

self.addEventListener("install", function (evento) {
  evento.waitUntil(caches.open(CACHE)
    .then(function (cache) { return cache.add(SIN_CONEXION); })
    .then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (evento) {
  evento.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", function (evento) {
  var pedido = evento.request;
  if (pedido.method !== "GET" || pedido.mode !== "navigate") return;
  evento.respondWith(navegar(pedido));
});

function conLimite(promesa, ms) {
  return new Promise(function (ok, mal) {
    var reloj = setTimeout(function () { mal(new Error("sin respuesta")); }, ms);
    promesa.then(function (r) { clearTimeout(reloj); ok(r); },
                 function (e) { clearTimeout(reloj); mal(e); });
  });
}

async function navegar(pedido) {
  var cache = await caches.open(CACHE);
  var url = new URL(pedido.url);
  var guardada = await cache.match(url.pathname, { ignoreSearch: true });
  // ?sin_conexion=1 abre a propósito la copia del celular (para corregir lo que el servidor rechazó).
  if (guardada && url.searchParams.has("sin_conexion")) return guardada;
  try {
    var respuesta = await conLimite(fetch(pedido), ESPERA_MAXIMA);
    // Las copias del detalle se mantienen al día. La de Completar no: lo cargado en el
    // celular se reaplica sobre la misma pantalla en la que se cargó.
    if (guardada && respuesta.ok && !url.pathname.endsWith("/completar/")) {
      cache.put(url.pathname, respuesta.clone());
    }
    return respuesta;
  } catch (error) {
    return guardada || (await cache.match(SIN_CONEXION)) ||
      new Response("Sin conexión", { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } });
  }
}
