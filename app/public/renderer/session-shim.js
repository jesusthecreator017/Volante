// oso app addition (not part of the renderer build): lets the app show a stored session's replay.
// The renderer always fetches assets/replay/replay.bin (EMBEDDING.md). With ?session=<id> this shim serves that URL
// from the app's IndexedDB (same origin) instead, so every session can have its own replay without touching the
// bundled assets (which are read-only inside the iOS/Android app bundle). Without ?session it changes nothing.
(function () {
  const id = new URLSearchParams(location.search).get("session");
  if (!id) {
    window.__osoReplayReady = Promise.resolve();
    return;
  }
  window.__osoReplayReady = new Promise((resolve) => {
    const req = indexedDB.open("oso", 1);
    req.onerror = () => resolve();
    req.onsuccess = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains("sessions")) return resolve();
      const get = db.transaction("sessions").objectStore("sessions").get(id);
      get.onerror = () => resolve();
      get.onsuccess = () => {
        const bytes = get.result && get.result.replay;
        if (bytes) {
          const orig = window.fetch.bind(window);
          window.fetch = (input, init) => {
            const url = typeof input === "string" ? input : input.url;
            if (/assets\/replay\/replay\.bin(\?|$)/.test(url)) {
              return Promise.resolve(new Response(bytes, { headers: { "content-type": "application/octet-stream" } }));
            }
            return orig(input, init);
          };
        } else {
          const s = document.getElementById("status");
          if (s) s.textContent = "this session has no replay.bin — showing the bundled one";
        }
        resolve();
      };
    };
  });
})();
