/* ===== Hyperagent: Welcome Noise Field (WebGL2) ===== */

// --- Welcome noise field (WebGL2) ---
var welcomeCanvas = null;
var welcomeGL = null;
var welcomeProg = null;
var welcomeRaf = null;
var welcomeTime = 0;
var welcomeVAO = null;
var welcomeHost = null;  // The container the current canvas is bound to

var WELCOME_VERT = '#version 300 es\nvoid main(){float x=float(gl_VertexID%2)*4.0-1.0;float y=float(gl_VertexID/2)*4.0-1.0;gl_Position=vec4(x,y,0,1);}';

var WELCOME_FRAG = [
  '#version 300 es',
  'precision highp float;',
  'uniform vec2 u_resolution;',
  'uniform float u_time;',
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
  '    float cellSize = max(2.0, floor(min(u_resolution.x, u_resolution.y) / 400.0));',
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
  '    fragColor = vec4(vec3(0.09), 1.0);',
  '}'
].join('\n');

function initWelcomeGL() {
  if (!welcomeCanvas) return false;
  if (welcomeGL) return true;
  welcomeGL = welcomeCanvas.getContext('webgl2', { alpha: false, antialias: false });
  if (!welcomeGL) return false;
  var gl = welcomeGL;
  var vs = gl.createShader(gl.VERTEX_SHADER);
  gl.shaderSource(vs, WELCOME_VERT);
  gl.compileShader(vs);
  var fs = gl.createShader(gl.FRAGMENT_SHADER);
  gl.shaderSource(fs, WELCOME_FRAG);
  gl.compileShader(fs);
  if (!gl.getShaderParameter(fs, gl.COMPILE_STATUS)) {
    console.error('[welcome-canvas] frag:', gl.getShaderInfoLog(fs));
    welcomeGL = null; return false;
  }
  welcomeProg = gl.createProgram();
  gl.attachShader(welcomeProg, vs);
  gl.attachShader(welcomeProg, fs);
  gl.linkProgram(welcomeProg);
  if (!gl.getProgramParameter(welcomeProg, gl.LINK_STATUS)) {
    console.error('[welcome-canvas] link:', gl.getProgramInfoLog(welcomeProg));
    welcomeGL = null; return false;
  }
  gl.deleteShader(vs);
  gl.deleteShader(fs);
  welcomeVAO = gl.createVertexArray();
  gl.bindVertexArray(welcomeVAO);
  return true;
}

function welcomeNoiseFrame() {
  if (!welcomeGL || !welcomeCanvas) return;
  var host = welcomeHost || (welcomeCanvas && welcomeCanvas.parentNode);
  if (!host) return;
  var gl = welcomeGL;
  // Sync canvas backing store to container size (prevents stretch on resize)
  var dw = host.clientWidth;
  var dh = host.clientHeight;
  if (dw && dh && (welcomeCanvas.width !== dw || welcomeCanvas.height !== dh)) {
    welcomeCanvas.width = dw;
    welcomeCanvas.height = dh;
  }
  var w = welcomeCanvas.width;
  var h = welcomeCanvas.height;
  if (w === 0 || h === 0) {
    welcomeRaf = requestAnimationFrame(welcomeNoiseFrame);
    return;
  }
  gl.viewport(0, 0, w, h);
  gl.useProgram(welcomeProg);
  gl.uniform2f(gl.getUniformLocation(welcomeProg, 'u_resolution'), w, h);
  gl.uniform1f(gl.getUniformLocation(welcomeProg, 'u_time'), welcomeTime);
  gl.drawArrays(gl.TRIANGLES, 0, 3);
  welcomeTime += 0.015;
  welcomeRaf = requestAnimationFrame(welcomeNoiseFrame);
}

function startWelcomeNoise(container) {
  // Explicit container arg lets callers target a specific tab's msgs element
  // instead of depending on the global `msgs` being correct at call time.
  // Falls back to `msgs` for the initial launch call in 03-ui.js.
  var host = container || (typeof msgs !== 'undefined' ? msgs : null);
  if (!host) return;
  var w = host.querySelector('.welcome');
  if (!w) return;
  // Tear down any prior noise field before starting a new one — prevents
  // orphaned canvases / leaked WebGL contexts if a second welcome activates
  // while a first is still live.
  if (welcomeCanvas || welcomeGL || welcomeRaf) {
    destroyWelcomeNoise(true); // immediate, no fade
  }
  welcomeHost = host;
  welcomeCanvas = document.createElement('canvas');
  welcomeCanvas.className = 'welcome-canvas';
  // Mount as first child so the welcome content (z-index:1) renders on top
  host.insertBefore(welcomeCanvas, host.firstChild);
  // Initial size — the frame loop will resync each tick if this is 0
  welcomeCanvas.width = host.clientWidth;
  welcomeCanvas.height = host.clientHeight;
  welcomeTime = Math.random() * 1000;
  if (initWelcomeGL()) {
    welcomeRaf = requestAnimationFrame(welcomeNoiseFrame);
  }
}

function destroyWelcomeNoise(immediate) {
  if (welcomeRaf) { cancelAnimationFrame(welcomeRaf); welcomeRaf = null; }
  if (welcomeGL) {
    if (welcomeProg) welcomeGL.deleteProgram(welcomeProg);
    welcomeGL = null;
    welcomeProg = null;
    welcomeVAO = null;
  }
  if (welcomeCanvas) {
    var c = welcomeCanvas;
    welcomeCanvas = null;
    welcomeHost = null;
    if (immediate) {
      if (c.parentNode) c.parentNode.removeChild(c);
    } else {
      c.classList.add('fade-out');
      setTimeout(function() { if (c.parentNode) c.parentNode.removeChild(c); }, 600);
    }
  } else {
    welcomeHost = null;
  }
}
