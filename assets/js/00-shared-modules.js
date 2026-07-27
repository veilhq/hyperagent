/* ===== Bayer-dither noise field — extractable module (WI-112 Phase 6) =====
   Reusable WebGL2 noise-field renderer. Used by:
   - Hypervisor: home-anchor.js (mounts into .home-anchor on the homepage)
   - Hyperagent: welcome screen (via .hyperagent/assets/js/06-welcome.js shim)

   Public API exposed on window.HvNoiseField:
     start(hostElement, opts?) — mounts a canvas into hostElement and begins rendering
                                 opts.cellDivisor: dither cell density divisor (default 200).
                                   Higher = smaller/finer cells. Hyperagent passes 400 for
                                   a finer look inside the chat window.
     stop(fadeMs)              — tears down; if fadeMs > 0, fades canvas out before removal

   Reads --accent CSS variable for tint (heavily dimmed to 0.15 luminance).
   No external dependencies — pure vanilla WebGL2 + requestAnimationFrame.
   ================================================================= */

(function () {
  // Skip binding twice (module concatenation could include this multiple times
  // in a mis-configured build).
  if (window.HvNoiseField) return;

  var _canvas = null;
  var _gl = null;
  var _prog = null;
  var _vao = null;
  var _raf = null;
  var _t = 0;
  var _host = null;
  var _cellDivisor = 200;  // Default; caller may override via start(host, {cellDivisor: N}).

  var VERT = '#version 300 es\nvoid main(){float x=float(gl_VertexID%2)*4.0-1.0;float y=float(gl_VertexID/2)*4.0-1.0;gl_Position=vec4(x,y,0,1);}';

  var FRAG = [
    '#version 300 es',
    'precision highp float;',
    'uniform vec2 u_resolution;',
    'uniform float u_time;',
    'uniform vec3 u_tint;',
    'uniform float u_cellDivisor;',
    'out vec4 fragColor;',
    '',
    'float bayer8(vec2 pos) {',
    '    ivec2 p = ivec2(mod(pos, 8.0));',
    '    float m[64] = float[64](',
    '         0.0, 32.0,  8.0, 40.0,  2.0, 34.0, 10.0, 42.0,',
    '        48.0, 16.0, 56.0, 24.0, 50.0, 18.0, 58.0, 26.0,',
    '        12.0, 44.0,  4.0, 36.0, 14.0, 46.0,  6.0, 38.0,',
    '        60.0, 28.0, 52.0, 20.0, 62.0, 30.0, 54.0, 22.0,',
    '         3.0, 35.0, 11.0, 43.0,  1.0, 33.0,  9.0, 41.0,',
    '        51.0, 19.0, 59.0, 27.0, 49.0, 17.0, 57.0, 25.0,',
    '        15.0, 47.0,  7.0, 39.0, 13.0, 45.0,  5.0, 37.0,',
    '        63.0, 31.0, 55.0, 23.0, 61.0, 29.0, 53.0, 21.0',
    '    );',
    '    return m[p.x + p.y * 8] / 64.0;',
    '}',
    '',
    'void main() {',
    '    float t = u_time;',
    '    float cellSize = max(2.0, floor(min(u_resolution.x, u_resolution.y) / u_cellDivisor));',
    '    vec2 cellUv = floor(gl_FragCoord.xy / cellSize) * cellSize;',
    '    vec2 cellPos = cellUv / u_resolution;',
    '',
    '    // Moving radial center',
    '    float cx = 0.5 + sin(t * 0.4) * 0.3;',
    '    float cy = 0.5 + cos(t * 0.3) * 0.3;',
    '    vec2 d = cellPos - vec2(cx, cy);',
    '    float dist = length(d);',
    '',
    '    // Three overlapping trig waves',
    '    float g1 = 0.5 + 0.5 * sin(dist * 6.0 - t * 0.8);',
    '    float g2 = 0.5 + 0.5 * sin((cellUv.x + cellUv.y) * 0.0032 + t * 0.5);',
    '    float g3 = 0.5 + 0.5 * cos((cellUv.y - cellUv.x) * 0.0041 - t * 0.3);',
    '    float val = g1 * 0.5 + g2 * 0.25 + g3 * 0.25;',
    '',
    '    // Squared falloff for dither density',
    '    val = val * val;',
    '',
    '    // Bayer 8x8 dither — clean on/off, gradient via pixel density',
    '    float threshold = bayer8(gl_FragCoord.xy / cellSize);',
    '    if (val < threshold) { fragColor = vec4(0.0, 0.0, 0.0, 1.0); return; }',
    '',
    '    fragColor = vec4(u_tint, 1.0);',
    '}'
  ].join('\n');

  function readAccentTint() {
    // Read --accent CSS variable and convert #rrggbb -> normalized rgb.
    // Dim heavily (x 0.15) so the field reads as ambient texture, not active graphic.
    try {
      var raw = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
      var m = /^#?([0-9a-f]{6})$/i.exec(raw);
      if (m) {
        var hex = m[1];
        var r = parseInt(hex.substr(0, 2), 16) / 255;
        var g = parseInt(hex.substr(2, 2), 16) / 255;
        var b = parseInt(hex.substr(4, 2), 16) / 255;
        return [r * 0.15, g * 0.15, b * 0.15];
      }
    } catch (e) {}
    return [0.09, 0.09, 0.09];
  }

  function initGL() {
    if (!_canvas) return false;
    _gl = _canvas.getContext('webgl2', { alpha: false, antialias: false });
    if (!_gl) return false;
    var vs = _gl.createShader(_gl.VERTEX_SHADER);
    _gl.shaderSource(vs, VERT); _gl.compileShader(vs);
    var fs = _gl.createShader(_gl.FRAGMENT_SHADER);
    _gl.shaderSource(fs, FRAG); _gl.compileShader(fs);
    if (!_gl.getShaderParameter(fs, _gl.COMPILE_STATUS)) {
      console.error('[HvNoiseField] frag compile:', _gl.getShaderInfoLog(fs));
      _gl = null; return false;
    }
    _prog = _gl.createProgram();
    _gl.attachShader(_prog, vs); _gl.attachShader(_prog, fs);
    _gl.linkProgram(_prog);
    if (!_gl.getProgramParameter(_prog, _gl.LINK_STATUS)) {
      console.error('[HvNoiseField] link:', _gl.getProgramInfoLog(_prog));
      _gl = null; _prog = null; return false;
    }
    _vao = _gl.createVertexArray();
    return true;
  }

  function frame() {
    if (!_gl || !_canvas || !_host) { _raf = null; return; }
    var w = _host.clientWidth || 1;
    var h = _host.clientHeight || 1;
    if (_canvas.width !== w || _canvas.height !== h) {
      _canvas.width = w;
      _canvas.height = h;
    }
    _gl.viewport(0, 0, w, h);
    _gl.useProgram(_prog);
    _gl.bindVertexArray(_vao);
    _gl.uniform2f(_gl.getUniformLocation(_prog, 'u_resolution'), w, h);
    _gl.uniform1f(_gl.getUniformLocation(_prog, 'u_time'), _t);
    _gl.uniform1f(_gl.getUniformLocation(_prog, 'u_cellDivisor'), _cellDivisor);
    var tint = readAccentTint();
    _gl.uniform3f(_gl.getUniformLocation(_prog, 'u_tint'), tint[0], tint[1], tint[2]);
    _gl.drawArrays(_gl.TRIANGLE_STRIP, 0, 4);
    _t += 1 / 60;
    _raf = requestAnimationFrame(frame);
  }

  function start(hostEl, opts) {
    if (!hostEl) return;
    stop(0); // idempotent
    _host = hostEl;
    _cellDivisor = (opts && typeof opts.cellDivisor === 'number' && opts.cellDivisor > 0) ? opts.cellDivisor : 200;
    _canvas = document.createElement('canvas');
    _canvas.className = 'hv-noise-field-canvas';
    // Legacy class kept so existing CSS selectors (Hypervisor: home-anchor-canvas) still match
    _canvas.classList.add('home-anchor-canvas');
    _host.insertBefore(_canvas, _host.firstChild);
    _canvas.width = _host.clientWidth;
    _canvas.height = _host.clientHeight;
    _t = Math.random() * 1000;
    if (initGL()) {
      _raf = requestAnimationFrame(frame);
    }
  }

  function stop(fadeMs) {
    if (_raf) { cancelAnimationFrame(_raf); _raf = null; }
    if (_gl) {
      if (_prog) _gl.deleteProgram(_prog);
      _gl = null; _prog = null; _vao = null;
    }
    if (_canvas) {
      var c = _canvas;
      _canvas = null;
      _host = null;
      if (!fadeMs || fadeMs <= 0) {
        if (c.parentNode) c.parentNode.removeChild(c);
      } else {
        c.classList.add('fade-out');
        setTimeout(function () {
          if (c.parentNode) c.parentNode.removeChild(c);
        }, fadeMs);
      }
    } else {
      _host = null;
    }
  }

  window.HvNoiseField = { start: start, stop: stop };
})();

/* ===== Kaomoji greeting picker — extractable module (WI-112 Phase 6) =====
   Rotating greetings for the homepage / Hyperagent welcome screen.
   Kaomoji entries (any greeting that doesn't start with an ASCII letter) get
   the `emote` CSS class applied for glow treatment.

   Public API exposed on window.HvGreeting:
     pick()                  — returns { text, isEmote }
     applyTo(element)        — picks and applies to element (sets textContent + emote class)
   ================================================================= */

(function () {
  if (window.HvGreeting) return;

  var GREETINGS = [
    'welcome back, operator.',
    'workspace online.',
    'good to see you, V.',
    'systems nominal.',
    'hyperspace loaded.',
    'ready when you are.',
    'let\'s get to work.',
    'signal acquired.',
    'context restored.',
    'the vault, as you left it.',
    'still here. still working.',
    'no drift detected.',
    'awaiting instruction.',
    'all lines open.',
    'buffer clear. proceed.',
    'the map is up to date.',
    // Kaomoji greetings — canonical list from steering
    '[+1]',
    '(-_-)b',
    '[\u2713]',
    '\\o/',
    '(._.)',
    '(?_?)',
    '(\uff3e_\uff3e)\uff9e',
    '(\u3000-_-)\u65e6~',
    '(._. )'
  ];

  function pick() {
    var text = GREETINGS[Math.floor(Math.random() * GREETINGS.length)];
    var isEmote = !/^[a-zA-Z]/.test(text);
    return { text: text, isEmote: isEmote };
  }

  function applyTo(element) {
    if (!element) return null;
    var g = pick();
    element.textContent = g.text;
    element.classList.toggle('emote', g.isEmote);
    return g;
  }

  window.HvGreeting = { pick: pick, applyTo: applyTo };
})();


/* ===== Cursor trail — extractable module (WI-119) =====
   WebGL2 ping-pong cursor trail. Stamps the sitewide pointer SVG along the
   motion segment each frame, decaying the previous frame's buffer to produce
   a smooth smear rather than a stuttering sequence of discrete copies.

   Reads a CSS variable (default '--cool') at frame time for tint, so accent
   picker changes cascade live. Idle rAF suspend keeps GPU cost at zero when
   the cursor is parked. Piggybacks the ecosystem a11y toggles — the trail is
   hard-disabled when 'a11y-reduce-motion' or 'a11y-no-glitch' is set on <html>,
   or when the OS reports `prefers-reduced-motion: reduce`.

   Public API exposed on window.HvCursorTrail:
     start(host, opts?)        — mounts canvas into host; idempotent if running.
                                 opts.color   — CSS var name (default '--cool')
                                 opts.decay   — per-frame multiplier (default 0.85)
                                 opts.stampPx — CSS-px stamp size (default 24)
                                 opts.variant — 'stroked' | 'filled' (default 'stroked')
                                 opts.density — smear spacing fraction (default 0.30)
                                 opts.idleMs  — motion-gate window (default 150)
                                 opts.offsetX / opts.offsetY — extra CSS-px offset
     stop(fadeMs)              — tears down; if fadeMs > 0, fades out first.
   ================================================================= */

(function () {
  if (window.HvCursorTrail) return;

  var DEFAULTS = {
    color: '--accent',
    decay: 0.85,
    stampPx: 20,
    variant: 'stroked',
    density: 0.30,
    idleMs: 150,
    offsetX: 0,
    offsetY: 0
  };

  var STAMP_RES = 128;
  var STAMP_HOTSPOT_UV = [2 / 24, 1 - 2 / 24];  // matches SVG (2,2) hotspot after Y-flip
  var TELEPORT_CSS_PX = 400;                     // segments longer than this collapse to one stamp

  var VERT = [
    '#version 300 es',
    'out vec2 vUv;',
    'void main(){',
    '  float x=float(gl_VertexID%2)*4.0-1.0;',
    '  float y=float(gl_VertexID/2)*4.0-1.0;',
    '  vUv=vec2(x,y)*0.5+0.5;',
    '  gl_Position=vec4(x,y,0.0,1.0);',
    '}'
  ].join('\n');

  var DECAY_FRAG = [
    '#version 300 es',
    'precision highp float;',
    'in vec2 vUv;',
    'uniform sampler2D u_prev;',
    'uniform sampler2D u_stampTex;',
    'uniform vec2 u_resolution;',
    'uniform vec2 u_mouseFrom;',
    'uniform vec2 u_mouseTo;',
    'uniform int u_stampCount;',
    'uniform vec2 u_stampHotspot;',
    'uniform float u_decay;',
    'uniform float u_stampPx;',
    'uniform vec3 u_color;',
    'uniform float u_doStamp;',
    'out vec4 fragColor;',
    'const int MAX_STAMPS = 64;',
    'void main() {',
    '  vec4 prev = texture(u_prev, vUv);',
    '  vec4 faded = prev * u_decay;',
    '  if (u_doStamp > 0.5 && u_stampCount > 0) {',
    '    vec2 pix = vUv * u_resolution;',
    '    float bestAlpha = 0.0;',
    '    for (int i = 0; i < MAX_STAMPS; i++) {',
    '      if (i >= u_stampCount) break;',
    '      float t = (u_stampCount > 1) ? float(i) / float(u_stampCount - 1) : 0.0;',
    '      vec2 m = mix(u_mouseFrom, u_mouseTo, t);',
    '      vec2 stampUv = (pix - m) / u_stampPx + u_stampHotspot;',
    '      if (stampUv.x >= 0.0 && stampUv.x <= 1.0 && stampUv.y >= 0.0 && stampUv.y <= 1.0) {',
    '        bestAlpha = max(bestAlpha, texture(u_stampTex, stampUv).a);',
    '      }',
    '    }',
    '    if (bestAlpha > 0.15) {',
    '      faded = max(faded, vec4(u_color * bestAlpha, bestAlpha));',
    '    }',
    '  }',
    '  fragColor = faded;',
    '}'
  ].join('\n');

  var PRESENT_FRAG = [
    '#version 300 es',
    'precision highp float;',
    'in vec2 vUv;',
    'uniform sampler2D u_tex;',
    'out vec4 fragColor;',
    'void main() { fragColor = texture(u_tex, vUv); }'
  ].join('\n');

  // Module-level state (mirrors HvNoiseField). Only one instance active at a time.
  var _host = null;
  var _canvas = null;
  var _gl = null;
  var _decayProg = null;
  var _presentProg = null;
  var _uDecay = null;
  var _uPresent = null;
  var _vao = null;
  var _fbos = [null, null];
  var _texs = [null, null];
  var _read = 0, _write = 1;
  var _bufW = 0, _bufH = 0;
  var _stampTex = null;
  var _stampReady = false;
  var _opts = null;
  var _raf = null;
  var _running = false;
  var _mouseX = -1, _mouseY = -1;
  var _prevX = -1, _prevY = -1;
  var _lastMotionTs = 0;
  var _hasEverMoved = false;
  var _energy = 0;
  var _onMouseMove = null;
  var _onResize = null;

  function isDisabled() {
    var html = document.documentElement;
    if (html.classList.contains('a11y-reduce-motion')) return true;
    if (html.classList.contains('a11y-no-glitch')) return true;
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return true;
    return false;
  }

  function hexToRgb(hex) {
    var m = /^#?([0-9a-f]{6})$/i.exec((hex || '').trim());
    if (!m) return [0, 0.8, 0.8];
    var h = m[1];
    return [
      parseInt(h.substr(0, 2), 16) / 255,
      parseInt(h.substr(2, 2), 16) / 255,
      parseInt(h.substr(4, 2), 16) / 255
    ];
  }

  function readColor() {
    var raw = '';
    try { raw = getComputedStyle(document.documentElement).getPropertyValue(_opts.color).trim(); } catch (e) {}
    return hexToRgb(raw || '#00cccc');
  }

  function compileShader(type, src) {
    var s = _gl.createShader(type);
    _gl.shaderSource(s, src); _gl.compileShader(s);
    if (!_gl.getShaderParameter(s, _gl.COMPILE_STATUS)) {
      console.error('[HvCursorTrail] shader compile:', _gl.getShaderInfoLog(s));
      return null;
    }
    return s;
  }

  function makeProgram(vs, fs) {
    var v = compileShader(_gl.VERTEX_SHADER, vs);
    var f = compileShader(_gl.FRAGMENT_SHADER, fs);
    if (!v || !f) return null;
    var p = _gl.createProgram();
    _gl.attachShader(p, v); _gl.attachShader(p, f); _gl.linkProgram(p);
    if (!_gl.getProgramParameter(p, _gl.LINK_STATUS)) {
      console.error('[HvCursorTrail] link:', _gl.getProgramInfoLog(p));
      return null;
    }
    return p;
  }

  function createTex(w, h) {
    var t = _gl.createTexture();
    _gl.bindTexture(_gl.TEXTURE_2D, t);
    _gl.texImage2D(_gl.TEXTURE_2D, 0, _gl.RGBA, w, h, 0, _gl.RGBA, _gl.UNSIGNED_BYTE, null);
    _gl.texParameteri(_gl.TEXTURE_2D, _gl.TEXTURE_MIN_FILTER, _gl.LINEAR);
    _gl.texParameteri(_gl.TEXTURE_2D, _gl.TEXTURE_MAG_FILTER, _gl.LINEAR);
    _gl.texParameteri(_gl.TEXTURE_2D, _gl.TEXTURE_WRAP_S, _gl.CLAMP_TO_EDGE);
    _gl.texParameteri(_gl.TEXTURE_2D, _gl.TEXTURE_WRAP_T, _gl.CLAMP_TO_EDGE);
    return t;
  }

  function resize() {
    if (!_gl || !_canvas) return;
    var dpr = window.devicePixelRatio || 1;
    var w = Math.max(1, Math.floor(window.innerWidth * dpr));
    var h = Math.max(1, Math.floor(window.innerHeight * dpr));
    if (w === _bufW && h === _bufH) return;
    _bufW = w; _bufH = h;
    _canvas.width = w; _canvas.height = h;
    for (var i = 0; i < 2; i++) {
      if (_texs[i]) _gl.deleteTexture(_texs[i]);
      if (_fbos[i]) _gl.deleteFramebuffer(_fbos[i]);
      _texs[i] = createTex(w, h);
      _fbos[i] = _gl.createFramebuffer();
      _gl.bindFramebuffer(_gl.FRAMEBUFFER, _fbos[i]);
      _gl.framebufferTexture2D(_gl.FRAMEBUFFER, _gl.COLOR_ATTACHMENT0, _gl.TEXTURE_2D, _texs[i], 0);
      _gl.viewport(0, 0, w, h);
      _gl.clearColor(0, 0, 0, 0); _gl.clear(_gl.COLOR_BUFFER_BIT);
    }
    _gl.bindFramebuffer(_gl.FRAMEBUFFER, null);
  }

  function buildStampSvg(variant) {
    // Lucide mouse-pointer-2 path — same icon as the sitewide cursor CSS.
    // Stroke/fill white so the shader tint (u_color) drives the visible color.
    var fill = variant === 'filled' ? 'white' : 'none';
    // Stroke width tuned to match the sitewide cursor's visible line weight at
    // stampPx=20. Same value for both variants; the filled variant reads bolder
    // via its fill, not via extra stroke.
    var sw = 1.5;
    return "<svg xmlns='http://www.w3.org/2000/svg' width='" + STAMP_RES + "' height='" + STAMP_RES +
      "' viewBox='0 0 24 24' fill='" + fill + "' stroke='white' stroke-width='" + sw +
      "' stroke-linecap='round' stroke-linejoin='round'>" +
      "<path d='M4.037 4.688a.495.495 0 0 1 .651-.651l16 6.5a.5.5 0 0 1-.063.947l-6.124 1.58a2 2 0 0 0-1.438 1.435l-1.579 6.126a.5.5 0 0 1-.947.063z'/>" +
      "</svg>";
  }

  function loadStampTexture(variant) {
    var svgStr = buildStampSvg(variant);
    var img = new Image();
    img.onload = function () {
      if (!_gl || !_stampTex) return;
      var off = document.createElement('canvas');
      off.width = STAMP_RES; off.height = STAMP_RES;
      var ctx = off.getContext('2d');
      ctx.clearRect(0, 0, STAMP_RES, STAMP_RES);
      ctx.drawImage(img, 0, 0, STAMP_RES, STAMP_RES);
      _gl.bindTexture(_gl.TEXTURE_2D, _stampTex);
      _gl.pixelStorei(_gl.UNPACK_FLIP_Y_WEBGL, true);
      _gl.pixelStorei(_gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
      _gl.texImage2D(_gl.TEXTURE_2D, 0, _gl.RGBA, _gl.RGBA, _gl.UNSIGNED_BYTE, off);
      _gl.pixelStorei(_gl.UNPACK_FLIP_Y_WEBGL, false);
      _stampReady = true;
    };
    img.src = 'data:image/svg+xml;utf8,' + encodeURIComponent(svgStr);
  }

  function onMouseMoveImpl(e) {
    var dpr = window.devicePixelRatio || 1;
    var cssX = e.clientX + _opts.offsetX;
    var cssY = e.clientY + _opts.offsetY;
    _mouseX = cssX * dpr;
    _mouseY = (window.innerHeight - cssY) * dpr;
    _lastMotionTs = performance.now();
    _hasEverMoved = true;
    wake();
  }

  function frame(ts) {
    _raf = null;
    if (!_running || !_gl) return;

    var now = ts || performance.now();
    var inMotion = (now - _lastMotionTs) < _opts.idleMs;
    var doStamp = _hasEverMoved && _mouseX >= 0 && inMotion && _stampReady;

    var dpr = window.devicePixelRatio || 1;
    var fromX = _prevX, fromY = _prevY;
    if (fromX < 0 || fromY < 0) { fromX = _mouseX; fromY = _mouseY; }
    var dx = _mouseX - fromX, dy = _mouseY - fromY;
    var segDist = Math.hypot(dx, dy);
    if (segDist > TELEPORT_CSS_PX * dpr) { fromX = _mouseX; fromY = _mouseY; segDist = 0; }

    var stampPxDev = _opts.stampPx * dpr;
    var spacing = Math.max(1, stampPxDev * _opts.density);
    var stampCount = Math.min(64, Math.max(1, Math.ceil(segDist / spacing) + 1));

    // --- Decay + stamp pass ---
    _gl.useProgram(_decayProg);
    _gl.bindVertexArray(_vao);
    _gl.bindFramebuffer(_gl.FRAMEBUFFER, _fbos[_write]);
    _gl.viewport(0, 0, _bufW, _bufH);
    _gl.activeTexture(_gl.TEXTURE0);
    _gl.bindTexture(_gl.TEXTURE_2D, _texs[_read]);
    _gl.uniform1i(_uDecay.prev, 0);
    _gl.activeTexture(_gl.TEXTURE1);
    _gl.bindTexture(_gl.TEXTURE_2D, _stampTex);
    _gl.uniform1i(_uDecay.stampTex, 1);
    _gl.uniform2f(_uDecay.resolution, _bufW, _bufH);
    _gl.uniform2f(_uDecay.mouseFrom, fromX, fromY);
    _gl.uniform2f(_uDecay.mouseTo, _mouseX, _mouseY);
    _gl.uniform1i(_uDecay.stampCount, doStamp ? stampCount : 0);
    _gl.uniform2f(_uDecay.stampHotspot, STAMP_HOTSPOT_UV[0], STAMP_HOTSPOT_UV[1]);
    _gl.uniform1f(_uDecay.decay, _opts.decay);
    _gl.uniform1f(_uDecay.stampPx, stampPxDev);
    var col = readColor();
    _gl.uniform3f(_uDecay.color, col[0], col[1], col[2]);
    _gl.uniform1f(_uDecay.doStamp, doStamp ? 1.0 : 0.0);
    _gl.drawArrays(_gl.TRIANGLES, 0, 3);

    if (doStamp) _energy = 1.0; else _energy *= _opts.decay;

    // --- Present pass ---
    _gl.bindFramebuffer(_gl.FRAMEBUFFER, null);
    _gl.viewport(0, 0, _bufW, _bufH);
    _gl.useProgram(_presentProg);
    _gl.activeTexture(_gl.TEXTURE0);
    _gl.bindTexture(_gl.TEXTURE_2D, _texs[_write]);
    _gl.uniform1i(_uPresent.tex, 0);
    _gl.enable(_gl.BLEND);
    _gl.blendFunc(_gl.ONE, _gl.ONE_MINUS_SRC_ALPHA);
    _gl.drawArrays(_gl.TRIANGLES, 0, 3);
    _gl.disable(_gl.BLEND);

    var tmp = _read; _read = _write; _write = tmp;
    _prevX = _mouseX; _prevY = _mouseY;

    // Idle suspend: stop the loop once motion is idle AND trail has fully decayed.
    if (!inMotion && _energy < 0.01) {
      _running = false;
      return;
    }
    _raf = requestAnimationFrame(frame);
  }

  function wake() {
    if (_running || !_gl) return;
    if (isDisabled()) return;
    _running = true;
    _raf = requestAnimationFrame(frame);
  }

  function start(hostEl, opts) {
    if (_gl) return;                 // idempotent — already running
    if (isDisabled()) return;
    if (!hostEl) return;

    _opts = Object.assign({}, DEFAULTS, opts || {});
    _host = hostEl;

    _canvas = document.createElement('canvas');
    _canvas.className = 'hv-cursor-trail-canvas';
    _canvas.style.position = 'fixed';
    _canvas.style.top = '0';
    _canvas.style.left = '0';
    _canvas.style.width = '100vw';
    _canvas.style.height = '100vh';
    _canvas.style.pointerEvents = 'none';
    _canvas.style.background = 'transparent';
    // Sit directly below the DOM .cursor-box companion (--z-cursor: 550)
    _canvas.style.zIndex = 'calc(var(--z-cursor, 550) - 1)';
    _host.appendChild(_canvas);

    _gl = _canvas.getContext('webgl2', { alpha: true, antialias: false, premultipliedAlpha: false });
    if (!_gl) {
      console.warn('[HvCursorTrail] WebGL2 not available; trail disabled.');
      _host.removeChild(_canvas);
      _canvas = null; _host = null; _opts = null;
      return;
    }

    _decayProg = makeProgram(VERT, DECAY_FRAG);
    _presentProg = makeProgram(VERT, PRESENT_FRAG);
    if (!_decayProg || !_presentProg) { stop(0); return; }

    _uDecay = {
      prev: _gl.getUniformLocation(_decayProg, 'u_prev'),
      stampTex: _gl.getUniformLocation(_decayProg, 'u_stampTex'),
      resolution: _gl.getUniformLocation(_decayProg, 'u_resolution'),
      mouseFrom: _gl.getUniformLocation(_decayProg, 'u_mouseFrom'),
      mouseTo: _gl.getUniformLocation(_decayProg, 'u_mouseTo'),
      stampCount: _gl.getUniformLocation(_decayProg, 'u_stampCount'),
      stampHotspot: _gl.getUniformLocation(_decayProg, 'u_stampHotspot'),
      decay: _gl.getUniformLocation(_decayProg, 'u_decay'),
      stampPx: _gl.getUniformLocation(_decayProg, 'u_stampPx'),
      color: _gl.getUniformLocation(_decayProg, 'u_color'),
      doStamp: _gl.getUniformLocation(_decayProg, 'u_doStamp')
    };
    _uPresent = { tex: _gl.getUniformLocation(_presentProg, 'u_tex') };
    _vao = _gl.createVertexArray();

    _stampTex = _gl.createTexture();
    _gl.bindTexture(_gl.TEXTURE_2D, _stampTex);
    _gl.texImage2D(_gl.TEXTURE_2D, 0, _gl.RGBA, 1, 1, 0, _gl.RGBA, _gl.UNSIGNED_BYTE, new Uint8Array([0, 0, 0, 0]));
    _gl.texParameteri(_gl.TEXTURE_2D, _gl.TEXTURE_MIN_FILTER, _gl.LINEAR);
    _gl.texParameteri(_gl.TEXTURE_2D, _gl.TEXTURE_MAG_FILTER, _gl.LINEAR);
    _gl.texParameteri(_gl.TEXTURE_2D, _gl.TEXTURE_WRAP_S, _gl.CLAMP_TO_EDGE);
    _gl.texParameteri(_gl.TEXTURE_2D, _gl.TEXTURE_WRAP_T, _gl.CLAMP_TO_EDGE);
    _stampReady = false;
    loadStampTexture(_opts.variant);

    resize();

    _onMouseMove = onMouseMoveImpl;
    _onResize = resize;
    window.addEventListener('mousemove', _onMouseMove, { passive: true });
    window.addEventListener('resize', _onResize);
  }

  function stop(fadeMs) {
    if (!_canvas && !_gl) return;      // idempotent
    if (_raf) { cancelAnimationFrame(_raf); _raf = null; }
    _running = false;
    if (_onMouseMove) window.removeEventListener('mousemove', _onMouseMove);
    if (_onResize) window.removeEventListener('resize', _onResize);
    _onMouseMove = null; _onResize = null;

    function teardown() {
      if (_gl) {
        for (var i = 0; i < 2; i++) {
          if (_texs[i]) _gl.deleteTexture(_texs[i]);
          if (_fbos[i]) _gl.deleteFramebuffer(_fbos[i]);
          _texs[i] = null; _fbos[i] = null;
        }
        if (_stampTex) { _gl.deleteTexture(_stampTex); _stampTex = null; }
        if (_decayProg) { _gl.deleteProgram(_decayProg); _decayProg = null; }
        if (_presentProg) { _gl.deleteProgram(_presentProg); _presentProg = null; }
        var lose = _gl.getExtension('WEBGL_lose_context'); if (lose) lose.loseContext();
        _gl = null;
      }
      if (_canvas && _canvas.parentNode) _canvas.parentNode.removeChild(_canvas);
      _canvas = null; _host = null; _opts = null;
      _stampReady = false;
      _mouseX = _mouseY = _prevX = _prevY = -1;
      _hasEverMoved = false; _energy = 0;
    }

    if (fadeMs && fadeMs > 0 && _canvas) {
      _canvas.style.transition = 'opacity ' + fadeMs + 'ms linear';
      _canvas.style.opacity = '0';
      setTimeout(teardown, fadeMs);
    } else {
      teardown();
    }
  }

  window.HvCursorTrail = { start: start, stop: stop };
})();
