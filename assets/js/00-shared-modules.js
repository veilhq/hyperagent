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
