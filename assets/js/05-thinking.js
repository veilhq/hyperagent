/* ===== Hyperagent: Thinking Indicator (WebGL2 CRT Scan) ===== */

// --- CRT scan thinking indicator (WebGL2) ---
// Note: This file executes outside the main IIFE. DOM refs come from window.
var thinkingBar = document.getElementById('thinking-bar');
var thinkingGL = null;
var thinkingProg = null;
var thinkingRaf = null;
var thinkingTime = 0;
var thinkingVAO = null;

// --- Thinking bar color fade state ---
var thinkColorR = 0, thinkColorG = 0, thinkColorB = 0;
var thinkTargetR = 0, thinkTargetG = 0, thinkTargetB = 0;
var thinkColorSeeded = false;
var thinkLerpSpeed = 0.04;

function hexToRgbArr(hex) {
  hex = hex.trim();
  if (hex[0] === '#') hex = hex.slice(1);
  return [parseInt(hex.slice(0,2),16), parseInt(hex.slice(2,4),16), parseInt(hex.slice(4,6),16)];
}

function setThinkingTargetColor(hex) {
  var rgb = hexToRgbArr(hex);
  thinkTargetR = rgb[0]; thinkTargetG = rgb[1]; thinkTargetB = rgb[2];
}

function resetThinkingColor() {
  var accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#00ff41';
  setThinkingTargetColor(accent);
}

function setThinkingColorForTool(name) {
  var n = (name || '').toLowerCase();
  var root = getComputedStyle(document.documentElement);
  if (n.indexOf('read') > -1 || n.indexOf('grep') > -1 || n.indexOf('glob') > -1 ||
      n.indexOf('search') > -1 || n.indexOf('code') > -1 || n.indexOf('web_fetch') > -1 ||
      n.indexOf('web_search') > -1 || n.indexOf('knowledge') > -1 || n.indexOf('get_') > -1 ||
      n.indexOf('list_') > -1 || n.indexOf('introspect') > -1) {
    setThinkingTargetColor(root.getPropertyValue('--cool').trim() || '#00cccc');
  } else if (n.indexOf('write') > -1 || n.indexOf('shell') > -1 || n.indexOf('aws') > -1 ||
           n.indexOf('create') > -1 || n.indexOf('update') > -1 || n.indexOf('move_') > -1 ||
           n.indexOf('delete') > -1 || n.indexOf('add_tag') > -1 || n.indexOf('migrate') > -1) {
    setThinkingTargetColor(root.getPropertyValue('--warm').trim() || '#ffb000');
  } else {
    setThinkingTargetColor(root.getPropertyValue('--accent').trim() || '#00ff41');
  }
}

var THINKING_VERT = '#version 300 es\nvoid main(){float x=float(gl_VertexID%2)*4.0-1.0;float y=float(gl_VertexID/2)*4.0-1.0;gl_Position=vec4(x,y,0,1);}';

var THINKING_FRAG = [
  '#version 300 es',
  'precision highp float;',
  'uniform vec2 u_resolution;',
  'uniform float u_time;',
  'uniform vec3 u_color;',
  'out vec4 fragColor;',
  '',
  'float bayer8(vec2 pos) {',
  '    int m[64] = int[64](',
  '         0, 32,  8, 40,  2, 34, 10, 42,',
  '        48, 16, 56, 24, 50, 18, 58, 26,',
  '        12, 44,  4, 36, 14, 46,  6, 38,',
  '        60, 28, 52, 20, 62, 30, 54, 22,',
  '         3, 35, 11, 43,  1, 33,  9, 41,',
  '        51, 19, 59, 27, 49, 17, 57, 25,',
  '        15, 47,  7, 39, 13, 45,  5, 37,',
  '        63, 31, 55, 23, 61, 29, 53, 21',
  '    );',
  '    ivec2 p = ivec2(mod(pos, 8.0));',
  '    return float(m[p.x + p.y * 8]) / 64.0;',
  '}',
  '',
  'void main() {',
  '    float t = u_time;',
  '    float cell = 2.0;',
  '    vec2 cellUv = floor(gl_FragCoord.xy / cell) * cell;',
  '    float cx = u_resolution.x * 0.5 + sin(t * 0.4) * u_resolution.x * 0.3;',
  '    float cy = u_resolution.y * 0.5 + cos(t * 0.3) * u_resolution.y * 0.3;',
  '    float dx = (cellUv.x - cx) / u_resolution.x;',
  '    float dy = (cellUv.y - cy) / u_resolution.y;',
  '    float dist = sqrt(dx*dx + dy*dy);',
  '    float g1 = 0.5 + 0.5 * sin(dist * 6.0 - t * 0.8);',
  '    float g2 = 0.5 + 0.5 * sin((cellUv.x + cellUv.y) * 0.0032 + t * 0.5);',
  '    float g3 = 0.5 + 0.5 * cos((cellUv.y - cellUv.x) * 0.0041 - t * 0.3);',
  '    float val = g1 * 0.5 + g2 * 0.25 + g3 * 0.25;',
  '    val = val * val;',
  '    float threshold = bayer8(gl_FragCoord.xy / cell);',
  '    if (val < threshold) { fragColor = vec4(0.0, 0.0, 0.0, 1.0); return; }',
  '    fragColor = vec4(u_color * 0.78, 1.0);',
  '}'
].join('\n');

function initThinkingGL() {
  if (thinkingGL) return true;
  if (!thinkingBar) thinkingBar = document.getElementById('thinking-bar');
  if (!thinkingBar) return false;
  thinkingGL = thinkingBar.getContext('webgl2', { alpha: false, antialias: false });
  if (!thinkingGL) return false;
  var gl = thinkingGL;
  // Compile
  var vs = gl.createShader(gl.VERTEX_SHADER);
  gl.shaderSource(vs, THINKING_VERT);
  gl.compileShader(vs);
  var fs = gl.createShader(gl.FRAGMENT_SHADER);
  gl.shaderSource(fs, THINKING_FRAG);
  gl.compileShader(fs);
  if (!gl.getShaderParameter(fs, gl.COMPILE_STATUS)) {
    console.error('[thinking-bar] frag:', gl.getShaderInfoLog(fs));
    thinkingGL = null; return false;
  }
  thinkingProg = gl.createProgram();
  gl.attachShader(thinkingProg, vs);
  gl.attachShader(thinkingProg, fs);
  gl.linkProgram(thinkingProg);
  if (!gl.getProgramParameter(thinkingProg, gl.LINK_STATUS)) {
    console.error('[thinking-bar] link:', gl.getProgramInfoLog(thinkingProg));
    thinkingGL = null; return false;
  }
  gl.deleteShader(vs);
  gl.deleteShader(fs);
  thinkingVAO = gl.createVertexArray();
  gl.bindVertexArray(thinkingVAO);
  return true;
}

function thinkingDitherFrame() {
  if (!thinkingGL) return;
  var gl = thinkingGL;
  var w = thinkingBar.width;
  var h = thinkingBar.height;
  if (w === 0 || h === 0) {
    thinkingRaf = requestAnimationFrame(thinkingDitherFrame);
    return;
  }

  // Lerp color
  thinkColorR += (thinkTargetR - thinkColorR) * thinkLerpSpeed;
  thinkColorG += (thinkTargetG - thinkColorG) * thinkLerpSpeed;
  thinkColorB += (thinkTargetB - thinkColorB) * thinkLerpSpeed;

  gl.viewport(0, 0, w, h);
  gl.useProgram(thinkingProg);
  gl.uniform2f(gl.getUniformLocation(thinkingProg, 'u_resolution'), w, h);
  gl.uniform1f(gl.getUniformLocation(thinkingProg, 'u_time'), thinkingTime);
  gl.uniform3f(gl.getUniformLocation(thinkingProg, 'u_color'), thinkColorR/255, thinkColorG/255, thinkColorB/255);
  gl.drawArrays(gl.TRIANGLES, 0, 3);

  thinkingTime += 0.05;
  thinkingRaf = requestAnimationFrame(thinkingDitherFrame);
}

function showThinking() {
  if (!thinkingBar) thinkingBar = document.getElementById('thinking-bar');
  if (!thinkingBar) return;
  thinkingBar.classList.add('active');
  if (!thinkColorSeeded) {
    var accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#00ff41';
    var rgb = hexToRgbArr(accent);
    thinkColorR = rgb[0]; thinkColorG = rgb[1]; thinkColorB = rgb[2];
    thinkTargetR = rgb[0]; thinkTargetG = rgb[1]; thinkTargetB = rgb[2];
    thinkColorSeeded = true;
  }
  thinkingBar.width = thinkingBar.offsetWidth;
  thinkingBar.height = thinkingBar.offsetHeight;
  thinkingTime = Math.random() * 100;
  if (initThinkingGL()) {
    if (!thinkingRaf) thinkingRaf = requestAnimationFrame(thinkingDitherFrame);
  }
  // Show inline typing dots
  if (!document.getElementById('typing-indicator')) {
    var ti = document.createElement('div');
    ti.id = 'typing-indicator';
    ti.className = 'typing-indicator';
    ti.innerHTML = '<span class="thinking-dot"></span>';
    window.msgs.appendChild(ti);
    window.scrollBottom();
  }
}

function hideThinking() {
  if (!thinkingBar) thinkingBar = document.getElementById('thinking-bar');
  if (!thinkingBar) return;
  thinkingBar.classList.remove('active');
  if (thinkingRaf) { cancelAnimationFrame(thinkingRaf); thinkingRaf = null; }
  var ti = document.getElementById('typing-indicator');
  if (ti) ti.remove();
}
