/* ===== Hyperagent: ultracode sparkles =====
   While the window carries .ultracode, a few sparkles twinkle at random
   spots in the inner-glow band, one every couple of seconds. */
(function () {
  var MAX_LIVE = 3;
  var MIN_GAP = 1200;
  var MAX_GAP = 3600;
  var BAND = 20; // px of glow depth in from each edge

  var app = document.getElementById("app");
  var layout = document.querySelector(".main-layout");
  if (!app || !layout) return;

  var field = null;
  var timer = null;

  function reduced() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function ensureField() {
    if (field) return field;
    field = document.createElement("div");
    field.className = "ultracode-sparkles";
    field.setAttribute("aria-hidden", "true");
    layout.appendChild(field);
    return field;
  }

  // A random point in the glow band: pick an edge, then a spot along it.
  function place(el) {
    var along = Math.random() * 100 + "%";
    var depth = Math.round(2 + Math.random() * BAND);
    var edge = Math.floor(Math.random() * 4);
    var far = "calc(100% - " + depth + "px)";
    el.style.left = edge === 2 ? depth + "px" : edge === 3 ? far : along;
    el.style.top = edge === 0 ? depth + "px" : edge === 1 ? far : along;
  }

  function spawn() {
    var host = ensureField();
    if (host.children.length >= MAX_LIVE || document.hidden) return;
    var s = document.createElement("span");
    s.className = "sparkle";
    s.innerHTML = "<i></i><i></i><i></i><i></i>";
    place(s);
    s.firstChild.addEventListener("animationend", function (e) {
      if (e.animationName === "sparkleSeg") s.remove();
    });
    host.appendChild(s);
  }

  function schedule() {
    timer = setTimeout(function () {
      spawn();
      schedule();
    }, MIN_GAP + Math.random() * (MAX_GAP - MIN_GAP));
  }

  function sync() {
    var on = app.classList.contains("ultracode") && !reduced();
    if (on && timer === null) schedule();
    if (!on && timer !== null) {
      clearTimeout(timer);
      timer = null;
      if (field) field.textContent = "";
    }
  }

  new MutationObserver(sync).observe(app, { attributes: true, attributeFilter: ["class"] });
  sync();
})();
