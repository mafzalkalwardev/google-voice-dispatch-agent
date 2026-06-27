(function () {
  'use strict';

  // Expose debug hook (optional)


  const storageKey = 'ft_theme';

  function applyTheme(theme) {
    const html = document.documentElement;
    html.setAttribute('data-theme', theme === 'light' ? 'light' : 'dark');
  }

  function getPreferredTheme() {
    let theme = null;

    try {
      theme = localStorage.getItem(storageKey);
    } catch (_) {
      theme = null;
    }

    if (theme === 'light' || theme === 'dark') return theme;
    return 'dark';
  }

  function hookToggleButtons() {
    const btn = document.getElementById('themeToggle');
    if (!btn) return;

    function refreshLabel() {
      const current = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
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
