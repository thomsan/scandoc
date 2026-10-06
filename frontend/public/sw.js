// Cache only the application shell. Authenticated responses and documents never enter Cache Storage.
const CACHE = "scandoc-shell-v1";
self.addEventListener("install", (event) =>
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE);
      const html = await fetch("/", { cache: "reload" });
      if (!html.ok) throw new Error("Shell unavailable");
      const text = await html.clone().text();
      const assets = [
        ...text.matchAll(/(?:src|href)="(\/assets\/[^\"]+)"/g),
      ].map((match) => match[1]);
      await cache.addAll([
        ...assets,
        "/manifest.webmanifest",
        "/icon.svg",
        "/icon-192.png",
        "/icon-512.png",
      ]);
      await cache.put("/", html);
    })(),
  ),
);
self.addEventListener("activate", (event) =>
  event.waitUntil(
    (async () => {
      for (const name of await caches.keys())
        if (name !== CACHE) await caches.delete(name);
      await self.clients.claim();
    })(),
  ),
);
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (
    event.request.method !== "GET" ||
    url.origin !== self.location.origin ||
    !(url.pathname === "/" ||
      url.pathname.startsWith("/assets/") ||
      ["/manifest.webmanifest", "/icon.svg", "/icon-192.png", "/icon-512.png"].includes(url.pathname))
  )
    return;
  event.respondWith(
    (async () => {
      if (event.request.mode === "navigate") {
        try {
          const response = await fetch(event.request);
          if (response.ok) {
            const cache = await caches.open(CACHE);
            const html = await response.clone().text();
            const assets = [
              ...html.matchAll(/(?:src|href)="(\/assets\/[^\"]+)"/g),
            ].map((match) => match[1]);
            await cache.addAll(assets);
            await cache.put("/", response.clone());
            return response;
          }
        } catch (_) {}
        return await caches.match("/");
      }
      const cached = await caches.match(event.request);
      if (cached) return cached;
      try {
        return await fetch(event.request);
      } catch (error) {
        if (event.request.mode === "navigate") return await caches.match("/");
        throw error;
      }
    })(),
  );
});
