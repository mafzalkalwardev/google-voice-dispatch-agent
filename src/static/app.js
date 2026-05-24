/* INDUS TRANSPORTS LLC — Dispatch Agent Console — global JS */

(function () {
  'use strict';

  // ── Global run-status badge ──────────────────────────────────────────────
  async function pollGlobalStatus() {
    try {
      const r = await fetch('/api/run/status');
      const state = await r.json();
      const badge = document.getElementById('globalRunBadge');
      const pill  = document.getElementById('globalStatusPill');

      if (badge) badge.style.display = state.is_running ? 'inline-block' : 'none';

      if (pill) {
        pill.textContent = state.status.toUpperCase();
        pill.className = 'status-pill';
        if (state.status === 'running') {
          pill.classList.add('status-pill--running');
        } else if (state.status === 'completed') {
          pill.classList.add('status-pill--completed');
        } else if (state.status === 'failed') {
          pill.classList.add('status-pill--failed');
        } else if (state.status === 'stopping') {
          pill.classList.add('status-pill--stopping');
        }
      }
    } catch (_) {
      // server might be starting — swallow
    }
    setTimeout(pollGlobalStatus, 4000);
  }

  document.addEventListener('DOMContentLoaded', pollGlobalStatus);
})();
