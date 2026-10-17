// Mimics the viewer's exported seek so the rally list can be tested in dev.
window.oso_seek = (t) => { document.getElementById("t").textContent = `seeked to t = ${t.toFixed(1)} s`; };
