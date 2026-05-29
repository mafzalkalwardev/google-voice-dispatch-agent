(function () {
  'use strict';

  // Expose debug hook (optional)


  const storageKey = 'indus_theme';

  function applyTheme(theme) {
    const html = document.documentElement;
    if (theme === 'dark') html.setAttribute('data-theme', 'dark');
    else html.removeAttribute('data-theme');
  }

  function getPreferredTheme() {
    let theme = null;

    try {
      theme = localStorage.getItem(storageKey);
    } catch (_) {
      theme = null;
    }

    if (theme === 'light' || theme === 'dark') return theme;

    const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    return prefersDark ? 'dark' : 'light';
  }

  function hookToggleButtons() {
    const btn = document.getElementById('themeToggle');
    if (!btn) return;

    function refreshLabel() {
      const current = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
      btn.textContent = current === 'dark' ? 'Light' : 'Dark';
      const isDark = current === 'dark';
      btn.setAttribute('aria-pressed', isDark ? 'true' : 'false');
    }

    btn.addEventListener('click', () => {
      const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
      const next = isDark ? 'light' : 'dark';
      try {
        localStorage.setItem(storageKey, next);
      } catch (_) {}
      applyTheme(next);
      refreshLabel();
    });

    refreshLabel();
  }


  // Apply immediately to avoid flash of wrong theme.
  // Still safe: only uses localStorage + matchMedia.
  applyTheme(getPreferredTheme());

  document.addEventListener('DOMContentLoaded', () => {
    hookToggleButtons();
  });
})();

