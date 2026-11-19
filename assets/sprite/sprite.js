// Status sprite: Hyperagent's state as one-dot line-work on a 25x25 square-cell LED grid.
(function () {
  'use strict';
  var GRID = 25, CELLS = GRID * GRID, CENTER = (GRID - 1) / 2, RADIUS = GRID / 2;
  var LEVELS = 8, FLASH_STEPS = 4, TICK_MS = 100, REDUCED_TICK_MS = 1000;
  var POLL_MS = 2000, ALARM_S = 30;
  var SEED_NAME = 'hyperagent';
  var MID_X = 12, MID_Y = 12;
  // Morph timing in ticks: wave delay per cell of distance, random spread, flash fade, ghost hold.
  var WAVE = 0.5, JITTER = 4, FLASH_DECAY = 0.62, GHOST_TICKS = 2, GHOST_INK = 0.16;
  // Idle rule half-width and blink beat in ticks (one --period-blink); seconds of trace motion per tick.
  var LINE_HALF = 5, BLINK_TICKS = 10, TICK_S = 0.1;
  // Scope trace: reach from center in cells, and the curve parameter step between trail samples.
  var TRACE_AMP = 8, TRACE_STEP = 0.03;
  // Waiting ping: ring reach in cells, and the beat in ticks (the ring travels, then a pause).
  var PING_REACH = 8, PING_TICKS = 16;
  // Error stages: fault line half-width, spike frame length in ticks, and the cooling beat in ticks.
  var FAULT_HALF = 9, SPIKE_TICKS = 2, BEACON_TICKS = 20;
  var SEVERITY = ['sleep', 'idle', 'live', 'success', 'busy', 'waiting', 'beacon', 'alarm'];
  // The token each stage's flash blends toward; stages not listed use --live.
  var TINTS = { success: '--success', waiting: '--warning', beacon: '--error', alarm: '--error' };
  var TINT_TOKENS = ['--live', '--success', '--warning', '--error'];

  var sprite = document.getElementById('sprite');
  var canvas = document.getElementById('matrix');
  var ctx = canvas.getContext('2d');
  var label = document.getElementById('label');
  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // Display mask: the full grid with its corners rounded by cornerCells, matching the tile shape.
  var mask = new Uint8Array(CELLS);
  function buildMask(cornerCells) {
    var straight = RADIUS - cornerCells;
    for (var my = 0; my < GRID; my++) {
      for (var mx = 0; mx < GRID; mx++) {
        var mdx = Math.max(0, Math.abs(mx - CENTER) - straight), mdy = Math.max(0, Math.abs(my - CENTER) - straight);
        mask[my * GRID + mx] = mdx * mdx + mdy * mdy <= cornerCells * cornerCells ? 1 : 0;
      }
    }
  }
  buildMask(0);

  function api() { return window.pywebview && window.pywebview.api ? window.pywebview.api : null; }

  function mulberry32(seed) {
    return function () {
      seed = (seed + 0x6D2B79F5) | 0;
      var t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  // FNV-1a, so an app name always seeds the same patterns.
  function hash(text) {
    var value = 2166136261;
    for (var k = 0; k < text.length; k++) { value ^= text.charCodeAt(k); value = Math.imul(value, 16777619); }
    return value >>> 0;
  }
  // Cheap per-cell noise in [0, 1) for morph timing.
  function cellNoise(x, y, salt) {
    var value = Math.imul(x * 374761393 + y * 668265263 + salt * 2246822519, 3266489917);
    value = Math.imul(value ^ (value >>> 15), 2654435761);
    return ((value ^ (value >>> 13)) >>> 0) / 4294967296;
  }
  function distance(x, y) { return Math.sqrt((x - MID_X) * (x - MID_X) + (y - MID_Y) * (y - MID_Y)); }

  // Morph: cells move to their target in a wave from the center out; each change flashes, and removed cells leave a brief ghost.
  var morph = {
    ink: new Float32Array(CELLS), target: new Float32Array(CELLS), due: new Int32Array(CELLS).fill(-1),
    flash: new Float32Array(CELLS), ghost: new Uint8Array(CELLS), quiet: new Uint8Array(CELLS), salt: 0
  };
  // Quiet changes land at once with no flash, for motion that stays in grey ink.
  function morphTo(levels, wave, quiet) {
    morph.salt++;
    var spread = wave === 0 ? 1 : JITTER;
    for (var i = 0; i < CELLS; i++) {
      if (levels[i] === morph.target[i]) continue;
      morph.target[i] = levels[i];
      morph.quiet[i] = quiet ? 1 : 0;
      var x = i % GRID, y = Math.floor(i / GRID);
      morph.due[i] = reduceMotion || quiet ? ticks : ticks + Math.round(distance(x, y) * wave + cellNoise(i, 0, morph.salt) * spread);
    }
  }
  function advance(field) {
    for (var i = 0; i < CELLS; i++) {
      morph.flash[i] = morph.flash[i] > 0.05 ? morph.flash[i] * FLASH_DECAY : 0;
      if (morph.ghost[i]) morph.ghost[i]--;
      if (morph.due[i] >= 0 && morph.due[i] <= ticks) {
        morph.due[i] = -1;
        var was = morph.ink[i], next = morph.target[i];
        if (was !== next) {
          morph.ink[i] = next;
          if (!reduceMotion && !morph.quiet[i]) morph.flash[i] = 1;
          if (next === 0) morph.ghost[i] = GHOST_TICKS;
        }
      }
      field[i] = morph.ghost[i] ? Math.max(morph.ink[i], GHOST_INK) : morph.ink[i];
    }
  }

  // One pattern per status; each pattern only picks targets and the morph animates them.
  var PATTERNS = {
    // Standby: one dim dot held at the center, powered down.
    sleep: {
      init: function () {
        var levels = new Float32Array(CELLS);
        levels[MID_Y * GRID + MID_X] = 0.3;
        morphTo(levels, WAVE);
        return {};
      },
      step: function () {}
    },
    // Line: a flat dim rule across the center row, one mirrored pair of its dots blinking bright on each beat.
    idle: {
      init: function () {
        morphTo(ruled(-1), WAVE);
        return { ticks: 0, pair: -1 };
      },
      step: function (state, rng) {
        var phase = state.ticks++ % BLINK_TICKS;
        if (phase === 0) state.pair = 1 + Math.floor(rng() * LINE_HALF);
        if (phase === 0 || phase === BLINK_TICKS / 2) morphTo(ruled(phase === 0 ? state.pair : -1), 0, true);
      }
    },
    // Murmur: the scope trace at half speed with a short tail, the tail growing with the rate log lines arrive.
    live: {
      init: function (rng) { return { clock: 0, phase: rng() * Math.PI * 2 }; },
      step: function (state, rng, app) {
        state.clock += TICK_S * 0.5;
        var tail = Math.min(4, 1.5 + Math.log2(1 + app.rate) / 2);
        morphTo(trace(state.clock, state.phase, tail, TRACE_AMP * 0.8), 0);
      }
    },
    // Signal: a bright point drawing a Lissajous figure with a fading tail; its frequency ratio drifts so the figure keeps reshaping.
    busy: {
      init: function (rng) { return { clock: 0, phase: rng() * Math.PI * 2 }; },
      step: function (state) {
        state.clock += TICK_S;
        morphTo(trace(state.clock, state.phase, 5, TRACE_AMP), 0);
      }
    },
    // Break: a cooling error, the fault line split at the center and flaring bright once per slow beat.
    beacon: {
      init: function () { return { ticks: 0 }; },
      step: function (state) {
        var phase = state.ticks++ % BEACON_TICKS;
        if (phase === 0 || phase === 3) morphTo(broken(phase === 0 ? 0.85 : 0.35), 0);
      }
    },
    // Settle: the trace lands on the idle rule and a bright pulse runs from center out to both ends on each beat.
    success: {
      init: function () { return { ticks: 0 }; },
      step: function (state) {
        var phase = state.ticks++ % BLINK_TICKS;
        morphTo(ruled(phase <= LINE_HALF ? phase : -1, 0.5), 0);
      }
    },
    // Ping: the agent is blocked on the operator, a steady center dot sending out a ring that widens and fades until answered.
    waiting: {
      init: function () { return { ticks: 0 }; },
      step: function (state) {
        var phase = state.ticks++ % PING_TICKS;
        morphTo(ping(phase < PING_REACH ? phase + 1 : -1), 0);
      }
    },
    // Fault: a fresh error, the wide line jolted by jagged spikes that jump every few frames.
    alarm: {
      init: function () { return { ticks: 0 }; },
      step: function (state, rng) {
        if (state.ticks++ % SPIKE_TICKS === 0) morphTo(spiked(rng), 0);
      }
    }
  };
  // The scope trace at time clock (seconds): the last tail radians of the curve, brightest at the head and fading behind it.
  function trace(clock, phase, tail, amp) {
    var levels = new Float32Array(CELLS), head = clock * 2.2;
    var fx = 3 + 0.4 * Math.sin(clock * 0.11 + phase), fy = 2 + 0.25 * Math.sin(clock * 0.07);
    for (var t = head - tail; t <= head; t += TRACE_STEP) {
      var px = amp * Math.sin(fx * t + phase), py = amp * Math.sin(fy * t);
      var x = Math.round(MID_X + px), y = Math.round(MID_Y + py);
      if (x < 0 || y < 0 || x >= GRID || y >= GRID) continue;
      var fresh = 1 - (head - t) / tail, ink = 0.25 + 0.75 * fresh * fresh;
      if (ink > levels[y * GRID + x]) levels[y * GRID + x] = ink;
    }
    return levels;
  }
  // The idle rule: LINE_HALF dots each side of center on the center row, with the pair at the given distance lit bright.
  function ruled(pair, ink) {
    var levels = new Float32Array(CELLS);
    for (var dx = -LINE_HALF; dx <= LINE_HALF; dx++) levels[MID_Y * GRID + MID_X + dx] = Math.abs(dx) === pair ? 0.85 : ink || 0.35;
    return levels;
  }
  // The center dot and, for radius > 0, a one-dot ring at that radius dimming as it widens.
  function ping(radius) {
    var levels = new Float32Array(CELLS);
    levels[MID_Y * GRID + MID_X] = 1;
    if (radius <= 0) return levels;
    for (var i = 0; i < CELLS; i++) {
      if (Math.abs(distance(i % GRID, Math.floor(i / GRID)) - radius) < 0.5) levels[i] = 1 - 0.6 * radius / PING_REACH;
    }
    return levels;
  }
  // The fault line with two or three vertical spikes at random columns, each brightest at its tip.
  function spiked(rng) {
    var levels = new Float32Array(CELLS), spikes = 2 + Math.floor(rng() * 2);
    for (var dx = -FAULT_HALF; dx <= FAULT_HALF; dx++) levels[MID_Y * GRID + MID_X + dx] = 0.4;
    for (var k = 0; k < spikes; k++) {
      var x = MID_X + Math.round((rng() * 2 - 1) * FAULT_HALF), height = 2 + Math.floor(rng() * 6), dir = rng() < 0.5 ? -1 : 1;
      for (var h = 1; h <= height; h++) levels[(MID_Y + dir * h) * GRID + x] = h === height ? 1 : 0.7;
    }
    return levels;
  }
  // The fault line with its center three dots cut out, at the given ink.
  function broken(ink) {
    var levels = new Float32Array(CELLS);
    for (var dx = -FAULT_HALF; dx <= FAULT_HALF; dx++) if (Math.abs(dx) > 1) levels[MID_Y * GRID + MID_X + dx] = ink;
    return levels;
  }

  function stateOf(status) {
    if (status.error_age !== null) return status.error_age < ALARM_S ? 'alarm' : 'beacon';
    if (status.waiting) return 'waiting';
    if (status.busy) return 'busy';
    if (status.success_age !== null && status.success_age !== undefined && !status.closed) return 'success';
    if (status.closed) return 'sleep';
    return status.rate > 0 ? 'live' : 'idle';
  }
  function describe(status, state) {
    if (state === 'alarm' || state === 'beacon') return 'error ' + Math.round(status.error_age) + 's';
    if (state === 'live') return 'live ' + status.rate + '/m';
    if (state === 'waiting') return 'needs input';
    if (state === 'success') return 'done';
    return state;
  }

  var field = new Float32Array(CELLS);
  var shownLevels = new Int16Array(CELLS);
  var status = null, ticks = 0, dirty = true;
  var focus = { state: null };

  function showLabel() {
    label.textContent = status ? describe(status, focus.state) : 'waiting';
  }
  function show(state) {
    if (state === focus.state) return;
    var seed = hash(SEED_NAME);
    var rng = mulberry32(seed + SEVERITY.indexOf(state) * 7919);
    focus = { state: state, rng: rng, pattern: PATTERNS[state], data: PATTERNS[state].init(rng, seed) };
    showLabel();
  }

  // Renders hard-edged square LEDs straight into ImageData; the tile background shows through.
  var view = null;
  function hexToRgb(color) {
    var value = color.trim();
    var rgb = value.match(/^rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)/);
    if (rgb) return [+rgb[1], +rgb[2], +rgb[3]];
    value = value.replace('#', '');
    if (value.length === 3) value = value.replace(/./g, '$&$&');
    var number = parseInt(value.slice(0, 6), 16);
    return [(number >> 16) & 255, (number >> 8) & 255, number & 255];
  }
  function layout() {
    var dpr = window.devicePixelRatio || 1;
    var rect = canvas.getBoundingClientRect();
    var width = Math.max(1, Math.round(rect.width * dpr)), height = Math.max(1, Math.round(rect.height * dpr));
    canvas.width = width;
    canvas.height = height;
    var styles = getComputedStyle(document.documentElement);
    var off = hexToRgb(styles.getPropertyValue('--border'));
    var on = hexToRgb(styles.getPropertyValue('--text-bright'));
    var tints = TINT_TOKENS.map(function (token) { return hexToRgb(styles.getPropertyValue(token)); });
    // Whole-pixel pitch and an LED side of matching parity, so every LED is identical and the field centers to the pixel.
    var pitch = Math.max(1, Math.floor(Math.min(width, height) / GRID));
    if ((width - pitch * GRID) % 2 && pitch > 1) pitch--;
    var side = Math.max(1, Math.round(pitch * 0.76));
    if ((pitch - side) % 2) side = side + 1 <= pitch ? side + 1 : side - 1;
    var inset = (pitch - side) / 2;
    var originX = Math.floor((width - pitch * GRID) / 2), originY = Math.floor((height - pitch * GRID) / 2);
    var cornerPx = (parseFloat(getComputedStyle(sprite).borderTopLeftRadius) || 0) * dpr;
    buildMask(Math.max(0, cornerPx / pitch - 0.5));
    var leds = new Array(CELLS);
    for (var i = 0; i < CELLS; i++) {
      if (!mask[i]) continue;
      var ledX = originX + (i % GRID) * pitch + inset, ledY = originY + Math.floor(i / GRID) * pitch + inset;
      var pixels = [];
      for (var ly = ledY; ly < ledY + side; ly++) {
        for (var lx = ledX; lx < ledX + side; lx++) {
          if (lx >= 0 && ly >= 0 && lx < width && ly < height) pixels.push((ly * width + lx) * 4);
        }
      }
      leds[i] = Int32Array.from(pixels);
    }
    // One palette per tint: grey ink levels, each blended toward the tint by flash step.
    var palettes = {};
    TINT_TOKENS.forEach(function (token, index) {
      var tint = tints[index], palette = [];
      for (var level = 0; level < LEVELS; level++) {
        var t = level / (LEVELS - 1);
        var grey = [0, 1, 2].map(function (channel) { return off[channel] + (on[channel] - off[channel]) * t; });
        for (var step = 0; step < FLASH_STEPS; step++) {
          var blend = step / (FLASH_STEPS - 1);
          palette.push([0, 1, 2].map(function (channel) { return Math.round(grey[channel] + (tint[channel] - grey[channel]) * blend); }));
        }
      }
      palettes[token] = palette;
    });
    var bridge = api();
    if (bridge && bridge.set_tile_region) bridge.set_tile_region(cornerPx);
    view = { base: new Uint8ClampedArray(width * height * 4), leds: leds, palettes: palettes, tint: null, image: ctx.createImageData(width, height) };
    shownLevels.fill(-1);
    dirty = true;
  }
  function draw() {
    if (!view) return;
    var token = TINTS[focus.state] || '--live';
    if (token !== view.tint) { view.tint = token; dirty = true; }
    // Without motion there is no flash, so a tinted stage holds its color on the inked cells instead.
    var held = reduceMotion && token !== '--live';
    for (var i = 0; i < CELLS; i++) {
      if (!mask[i]) continue;
      var level = Math.max(0, Math.min(LEVELS - 1, Math.round(field[i] * (LEVELS - 1))));
      var step = held && level > 0 ? FLASH_STEPS - 1 : Math.round(morph.flash[i] * (FLASH_STEPS - 1));
      var shade = level * FLASH_STEPS + step;
      if (shade !== shownLevels[i]) { shownLevels[i] = shade; dirty = true; }
    }
    if (!dirty) return;
    dirty = false;
    var data = view.image.data;
    data.set(view.base);
    for (var c = 0; c < CELLS; c++) {
      var pixels = view.leds[c];
      if (!pixels) continue;
      var color = view.palettes[view.tint][shownLevels[c]];
      for (var k = 0; k < pixels.length; k++) {
        var offset = pixels[k];
        data[offset] = color[0]; data[offset + 1] = color[1]; data[offset + 2] = color[2]; data[offset + 3] = 255;
      }
    }
    ctx.putImageData(view.image, 0, 0);
  }

  function tick() {
    if (!document.hidden) {
      if (status) {
        show(stateOf(status));
        focus.pattern.step(focus.data, focus.rng, status);
        advance(field);
        if (ticks % 10 === 0) showLabel();
      }
      ticks++;
      draw();
    }
    setTimeout(tick, reduceMotion ? REDUCED_TICK_MS : TICK_MS);
  }

  function pollStatus() {
    api().get_status().then(function (next) { status = next; })
      .catch(function () { status = null; })
      .then(function () { setTimeout(pollStatus, POLL_MS); });
  }

  // Semantic colors, matching the chat window: light mode's fixed set, a preset's own, or the dark defaults.
  var LIGHT_SEMANTICS = { success: '#007a00', warning: '#b35900', error: '#cc0000' };
  var DARK_SEMANTICS = { success: '#00ff41', warning: '#ffb000', error: '#ff3333' };

  // Theme: the accent drives --live, the semantics drive the stage tints; light mode switches the whole ink scale.
  function applySpriteTheme(palette) {
    var root = document.documentElement, light = !!(palette && palette.lightMode);
    root.classList.toggle('a11y-bw-theme', light);
    ['accent', 'warm', 'cool', 'comp'].forEach(function (name) {
      if (palette && palette[name] && !light) root.style.setProperty('--' + name, palette[name]);
      else root.style.removeProperty('--' + name);
    });
    var semantics = light ? LIGHT_SEMANTICS : (palette && palette.semantics) || DARK_SEMANTICS;
    ['success', 'warning', 'error'].forEach(function (name) {
      root.style.setProperty('--' + name, semantics[name] || DARK_SEMANTICS[name]);
    });
    layout();
  }
  window.applySpriteTheme = applySpriteTheme;

  // Move: Python tracks the cursor in physical pixels, so the drag holds across monitors with different scaling.
  var gesture = null, pending = false;
  sprite.addEventListener('pointerdown', function (e) {
    if (e.button !== 0 || !api()) return;
    e.preventDefault();
    sprite.setPointerCapture(e.pointerId);
    gesture = { sx: e.screenX, sy: e.screenY, moved: false };
    api().begin_drag();
  });
  sprite.addEventListener('pointermove', function (e) {
    var g = gesture;
    if (!g) return;
    var dx = e.screenX - g.sx, dy = e.screenY - g.sy;
    if (!g.moved && dx * dx + dy * dy < 9) return;
    g.moved = true;
    if (pending) return;
    pending = true;
    api().drag_to().then(function () { pending = false; }, function () { pending = false; });
  });
  function endGesture() {
    var g = gesture;
    gesture = null;
    if (g && api()) api().end_drag(g.moved);
  }
  sprite.addEventListener('pointerup', endGesture);
  sprite.addEventListener('pointercancel', endGesture);
  sprite.addEventListener('contextmenu', function (e) { e.preventDefault(); });

  window.addEventListener('pywebviewready', function () {
    api().get_accent().then(applySpriteTheme).catch(layout);
    pollStatus();
  });
  window.addEventListener('resize', layout);
  layout();
  tick();
})();
