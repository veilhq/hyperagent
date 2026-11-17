// Status sprite: Hyperagent's state as a three-fold inkblot on a 25x25 square-cell LED grid.
(function () {
  'use strict';
  var GRID = 25, CELLS = GRID * GRID, CENTER = (GRID - 1) / 2, RADIUS = GRID / 2;
  var LEVELS = 8, FLASH_STEPS = 4, TICK_MS = 100, REDUCED_TICK_MS = 1000;
  var POLL_MS = 2000, ALARM_S = 30;
  var SEED_NAME = 'hyperagent';
  var MID_X = 12, MID_Y = 12;
  // Three-way rotation alone gives a pinwheel; adding the mirror gives a trefoil.
  var MIRROR = true;
  // Morph timing in ticks: wave delay per cell of distance, random spread, flash fade, ghost hold.
  var WAVE = 0.5, JITTER = 4, FLASH_DECAY = 0.62, GHOST_TICKS = 2, GHOST_INK = 0.16;
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
  // FNV-1a, so an app name always seeds the same inkblot and patterns.
  function hash(text) {
    var value = 2166136261;
    for (var k = 0; k < text.length; k++) { value ^= text.charCodeAt(k); value = Math.imul(value, 16777619); }
    return value >>> 0;
  }
  // Cheap per-cell noise in [0, 1) so symmetric cells share their morph timing.
  function cellNoise(x, y, salt) {
    var value = Math.imul(x * 374761393 + y * 668265263 + salt * 2246822519, 3266489917);
    value = Math.imul(value ^ (value >>> 15), 2654435761);
    return ((value ^ (value >>> 13)) >>> 0) / 4294967296;
  }
  function distance(x, y) { return Math.sqrt((x - MID_X) * (x - MID_X) + (y - MID_Y) * (y - MID_Y)); }

  // Three-point symmetry: every point has copies at 120 and 240 degrees around the center, plus their mirrors when MIRROR is on.
  var FOLDS = 3;
  function images(dx, dy) {
    var found = [];
    for (var k = 0; k < FOLDS; k++) {
      var angle = k * Math.PI * 2 / FOLDS, cos = Math.cos(angle), sin = Math.sin(angle);
      var rx = dx * cos - dy * sin, ry = dx * sin + dy * cos;
      found.push([rx, ry]);
      if (MIRROR) found.push([-rx, ry]);
    }
    return found;
  }
  // Each cell's symmetric partners on the grid, and the lowest index among them so partners share timing.
  var ORBITS = new Array(CELLS), CANON = new Int32Array(CELLS);
  for (var oi = 0; oi < CELLS; oi++) {
    var members = [oi];
    images(oi % GRID - MID_X, Math.floor(oi / GRID) - MID_Y).forEach(function (point) {
      var px = Math.round(MID_X + point[0]), py = Math.round(MID_Y + point[1]);
      if (px >= 0 && py >= 0 && px < GRID && py < GRID && members.indexOf(py * GRID + px) < 0) members.push(py * GRID + px);
    });
    ORBITS[oi] = members;
    CANON[oi] = Math.min.apply(null, members);
  }
  function setOrbit(target, i, value) { ORBITS[i].forEach(function (j) { target[j] = value; }); }

  // Inkblot from metaballs copied three ways around the center; spread scales the same blot from a drop to full size.
  var blots = {};
  function inkblot(seed, spread) {
    var key = seed + ':' + spread;
    if (blots[key]) return blots[key];
    var rng = mulberry32(seed), blobs = [];
    blobs.push({ x: 0, y: 0, r: 0.8 + spread * 1.2, weight: 1 });
    var count = 2 + Math.floor(rng() * 3);
    for (var k = 0; k < count; k++) {
      var reach = 1.5 + rng() * spread * 8, angle = rng() * Math.PI * 2;
      blobs.push({ x: Math.cos(angle) * reach, y: Math.sin(angle) * reach, r: 0.5 + rng() * spread * 1.8, weight: 1 });
    }
    // Holes: negative blobs that open the white gaps of a pressed blot.
    var holes = Math.floor(rng() * 3 * spread);
    for (var h = 0; h < holes; h++) {
      var holeReach = 1 + rng() * spread * 6, holeAngle = rng() * Math.PI * 2;
      blobs.push({ x: Math.cos(holeAngle) * holeReach, y: Math.sin(holeAngle) * holeReach, r: 0.5 + rng() * spread, weight: -1.6 });
    }
    var copies = [];
    blobs.forEach(function (blob) {
      var points = blob.x === 0 && blob.y === 0 ? [[0, 0]] : images(blob.x, blob.y);
      points.forEach(function (point) { copies.push({ x: MID_X + point[0], y: MID_Y + point[1], r: blob.r, weight: blob.weight }); });
    });
    var cells = new Uint8Array(CELLS);
    for (var i = 0; i < CELLS; i++) {
      var x = i % GRID, y = Math.floor(i / GRID), ink = 0;
      copies.forEach(function (blob) {
        var bx = x - blob.x, by = y - blob.y;
        ink += blob.weight * blob.r * blob.r / (bx * bx + by * by + 0.01);
      });
      if (ink >= 1.1) setOrbit(cells, i, 1);
    }
    // Spatter: a few loose drops around a grown blot.
    var drops = Math.floor(spread * 4 * rng());
    for (var d = 0; d < drops; d++) {
      var dropReach = 4 + rng() * spread * 9, dropAngle = rng() * Math.PI * 2;
      var dx = Math.round(MID_X + Math.cos(dropAngle) * dropReach), dy = Math.round(MID_Y + Math.sin(dropAngle) * dropReach);
      if (dx >= 0 && dy >= 0 && dx < GRID && dy < GRID) setOrbit(cells, dy * GRID + dx, 1);
    }
    return (blots[key] = cells);
  }
  function inked(cells, ink) {
    var levels = new Float32Array(CELLS);
    for (var i = 0; i < CELLS; i++) if (cells[i]) levels[i] = ink;
    return levels;
  }
  // Empty cells touching the blot, one per symmetric set: where a bleed can spread.
  function edges(cells) {
    var found = [];
    for (var i = 0; i < CELLS; i++) {
      var x = i % GRID, y = Math.floor(i / GRID);
      if (cells[i] || CANON[i] !== i) continue;
      if ((x > 0 && cells[i - 1]) || (x < GRID - 1 && cells[i + 1]) || (y > 0 && cells[i - GRID]) || (y < GRID - 1 && cells[i + GRID])) found.push(i);
    }
    return found;
  }

  // Morph: cells move to their target in a wave from the center out; each change flashes, and removed cells leave a brief ghost.
  var morph = {
    ink: new Float32Array(CELLS), target: new Float32Array(CELLS), due: new Int32Array(CELLS).fill(-1),
    flash: new Float32Array(CELLS), ghost: new Uint8Array(CELLS), salt: 0
  };
  function morphTo(levels, wave) {
    morph.salt++;
    var spread = wave === 0 ? 1 : JITTER;
    for (var i = 0; i < CELLS; i++) {
      if (levels[i] === morph.target[i]) continue;
      morph.target[i] = levels[i];
      var x = i % GRID, y = Math.floor(i / GRID);
      morph.due[i] = reduceMotion ? ticks : ticks + Math.round(distance(x, y) * wave + cellNoise(CANON[i], 0, morph.salt) * spread);
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
          if (!reduceMotion) morph.flash[i] = 1;
          if (next === 0) morph.ghost[i] = GHOST_TICKS;
        }
      }
      field[i] = morph.ghost[i] ? Math.max(morph.ink[i], GHOST_INK) : morph.ink[i];
    }
  }

  // One stage of the app's inkblot per status; each pattern only picks targets and the morph animates them.
  var PATTERNS = {
    // Drop: the blot shrunk to a still seed.
    sleep: {
      init: function (rng, seed) { morphTo(inked(inkblot(seed, 0.15), 0.3), WAVE); return {}; },
      step: function () {}
    },
    // Blot: the full shape at rest, now and then one symmetric set of edge cells soaks in and dries.
    idle: {
      init: function (rng, seed) {
        var cells = inkblot(seed, 0.6);
        morphTo(inked(cells, 0.55), WAVE);
        return { ticks: 0, cells: cells, edges: edges(cells), soak: false };
      },
      step: function (state, rng) {
        if (state.ticks++ % 30 !== 29 || !state.edges.length) return;
        var levels = inked(state.cells, 0.55);
        state.soak = !state.soak;
        if (state.soak) setOrbit(levels, state.edges[Math.floor(rng() * state.edges.length)], 0.55);
        morphTo(levels, 0);
      }
    },
    // Bleed: ink creeps out from the blot edge, three ways at once, as fast as the log lines arrive, then retracts.
    live: {
      init: function (rng, seed) {
        var cells = inkblot(seed, 0.6);
        morphTo(inked(cells, 0.7), WAVE);
        return { cells: cells, edges: edges(cells), bleeds: [] };
      },
      step: function (state, rng, app) {
        var chance = Math.min(0.9, 0.05 + Math.log2(1 + app.rate) / 8), changed = false;
        if (rng() < chance && state.edges.length) {
          state.bleeds.push({ cell: state.edges[Math.floor(rng() * state.edges.length)], life: 8 + Math.floor(rng() * 12) });
          changed = true;
        }
        state.bleeds = state.bleeds.filter(function (bleed) {
          if (--bleed.life > 0) return true;
          changed = true;
          return false;
        });
        if (!changed) return;
        var levels = inked(state.cells, 0.7);
        state.bleeds.forEach(function (bleed) { setOrbit(levels, bleed.cell, 0.7); });
        morphTo(levels, 0);
      }
    },
    // Kaleidoscope: the blot keeps turning to a new three-way figure, drawn as lace so it never reads as a solid lump.
    busy: {
      init: function (rng, seed) { return { ticks: 0, seed: seed, turn: 0, every: 12 + Math.floor(rng() * 6) }; },
      step: function (state) {
        if (state.ticks++ % state.every !== 0) return;
        morphTo(inked(laced(inkblot(state.seed + state.turn++ * 7919, 0.7)), 0.8), WAVE);
      }
    },
    // Smear: a cooling error, where the blot breaks into speckle and re-forms on a slow beat.
    beacon: {
      init: function (rng, seed) {
        var cells = inkblot(seed, 0.6);
        morphTo(inked(cells, 0.85), WAVE);
        return { ticks: 0, cells: cells, period: 24 + Math.floor(rng() * 10) };
      },
      step: function (state, rng) {
        var phase = state.ticks++ % state.period;
        if (phase === 0) morphTo(inked(state.cells, 0.85), WAVE);
        if (phase === Math.floor(state.period / 2)) morphTo(speckle(state.cells, rng, 0.45, 0.85), WAVE);
      }
    },
    // Ripple: a turn just finished cleanly, the blot pulsing bright and back on a quick beat.
    success: {
      init: function (rng, seed) { return { ticks: 0, cells: inkblot(seed, 0.6) }; },
      step: function (state) {
        var phase = state.ticks++ % 10;
        if (phase === 0) morphTo(inked(state.cells, 1), WAVE);
        if (phase === 5) morphTo(inked(state.cells, 0.6), WAVE);
      }
    },
    // Breathe: the agent is blocked on the operator, the blot swelling and fading on a slow beat until answered.
    waiting: {
      init: function (rng, seed) { return { ticks: 0, cells: inkblot(seed, 0.6) }; },
      step: function (state) {
        var phase = state.ticks++ % 16;
        if (phase === 0) morphTo(inked(state.cells, 0.85), WAVE);
        if (phase === 8) morphTo(inked(state.cells, 0.4), WAVE);
      }
    },
    // Shatter: a fresh error, flipping between the blot and symmetric noise every few frames.
    alarm: {
      init: function (rng, seed) { return { ticks: 0, cells: inkblot(seed, 0.6) }; },
      step: function (state, rng) {
        var phase = state.ticks++ % 6;
        if (phase === 0) morphTo(inked(state.cells, 1), 0);
        if (phase === 3) morphTo(noise(rng, 0.3, 1), 0);
      }
    }
  };
  // The blot with each symmetric set kept at the given odds, plus stray sets nearby.
  function speckle(cells, rng, keep, ink) {
    var levels = new Float32Array(CELLS);
    for (var i = 0; i < CELLS; i++) {
      if (CANON[i] !== i) continue;
      var x = i % GRID, near = cells[i] || (x > 0 && cells[i - 1]) || (i >= GRID && cells[i - GRID]);
      if (near && rng() < (cells[i] ? keep : keep / 4)) setOrbit(levels, i, ink);
    }
    return levels;
  }
  // The blot's outline plus every third ring out from the center, which opens a filled shape into lace.
  function laced(cells) {
    var out = new Uint8Array(CELLS);
    for (var i = 0; i < CELLS; i++) {
      if (CANON[i] !== i || !cells[i]) continue;
      var x = i % GRID, y = Math.floor(i / GRID);
      var inside = x > 0 && x < GRID - 1 && y > 0 && y < GRID - 1 && cells[i - 1] && cells[i + 1] && cells[i - GRID] && cells[i + GRID];
      if (!inside || Math.round(distance(x, y)) % 3 === 0) setOrbit(out, i, 1);
    }
    return out;
  }
  function noise(rng, density, ink) {
    var levels = new Float32Array(CELLS);
    for (var i = 0; i < CELLS; i++) if (CANON[i] === i && rng() < density / 2) setOrbit(levels, i, ink);
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
    var pitch = Math.min(width, height) / GRID;
    // Square cells, the system dot-matrix shape: halfSide is half of each side.
    var halfSide = pitch * 0.38;
    var originX = (width - pitch * GRID) / 2, originY = (height - pitch * GRID) / 2;
    var cornerPx = (parseFloat(getComputedStyle(sprite).borderTopLeftRadius) || 0) * dpr;
    buildMask(Math.max(0, cornerPx / pitch - 0.5));
    var leds = new Array(CELLS);
    for (var i = 0; i < CELLS; i++) {
      if (!mask[i]) continue;
      var ledX = Math.floor(originX + (i % GRID + 0.5) * pitch) + 0.5, ledY = Math.floor(originY + (Math.floor(i / GRID) + 0.5) * pitch) + 0.5;
      var pixels = [];
      for (var ly = Math.floor(ledY - halfSide); ly <= Math.ceil(ledY + halfSide); ly++) {
        for (var lx = Math.floor(ledX - halfSide); lx <= Math.ceil(ledX + halfSide); lx++) {
          var ldx = lx + 0.5 - ledX, ldy = ly + 0.5 - ledY;
          if (lx >= 0 && ly >= 0 && lx < width && ly < height && Math.abs(ldx) <= halfSide && Math.abs(ldy) <= halfSide) pixels.push((ly * width + lx) * 4);
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

  // Move: pointer capture keeps the gesture alive outside the window.
  var gesture = null, pending = false;
  sprite.addEventListener('pointerdown', function (e) {
    if (e.button !== 0) return;
    e.preventDefault();
    sprite.setPointerCapture(e.pointerId);
    var g = { sx: e.screenX, sy: e.screenY, bounds: null, moved: false };
    gesture = g;
    var bridge = api();
    if (bridge) bridge.get_bounds().then(function (bounds) { g.bounds = bounds; });
  });
  sprite.addEventListener('pointermove', function (e) {
    var g = gesture;
    if (!g) return;
    var dx = e.screenX - g.sx, dy = e.screenY - g.sy;
    if (!g.moved && dx * dx + dy * dy < 9) return;
    g.moved = true;
    if (!g.bounds || pending) return;
    var dpr = window.devicePixelRatio || 1;
    pending = true;
    api().set_bounds(g.bounds.x + dx * dpr, g.bounds.y + dy * dpr, g.bounds.w, g.bounds.h)
      .then(function () { pending = false; });
  });
  function endGesture() {
    var g = gesture;
    gesture = null;
    if (g && g.moved && api()) api().save_bounds();
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
