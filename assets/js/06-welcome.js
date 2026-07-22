/* ===== Hyperagent: Welcome noise field (WI-113 Phase 6 shim) =====
   Delegates to window.HvNoiseField from 00-shared-modules.js.
   Keeps the startWelcomeNoise(container) / destroyWelcomeNoise(immediate)
   signatures so existing callers in 02-handlers.js, 03-ui.js, 08-tabs.js
   don't have to change. The 06- filename ordering is preserved to keep the
   file's load position in the concat pipeline.
*/

function startWelcomeNoise(container) {
  var host = container || (typeof msgs !== 'undefined' ? msgs : null);
  if (!host) return;
  var w = host.querySelector('.welcome');
  if (!w) return;
  if (window.HvNoiseField) {
    // Hyperagent's welcome sits inside a chat window — finer dither reads
    // better here than Hypervisor's homepage-optimized default. cellDivisor
    // 400 restores the pre-Phase-6 visual density.
    window.HvNoiseField.start(w, { cellDivisor: 400 });
  }
}

function destroyWelcomeNoise(immediate) {
  if (window.HvNoiseField) {
    window.HvNoiseField.stop(immediate ? 0 : 600);
  }
}
