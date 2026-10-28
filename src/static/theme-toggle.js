(function () {
  'use strict';

  const storageKey = 'indus_theme';

  function applyTheme(theme) {
    const next = theme === 'light' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
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

  function currentTheme() {
    return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  }

  function hookToggleButtons() {
    const btn = document.getElementById('themeToggle');
    if (!btn) return;

    function refreshLabel() {
      const isDark = currentTheme() === 'dark';
      btn.textContent = isDark ? 'Light' : 'Dark';
      btn.setAttribute('aria-pressed', isDark ? 'true' : 'false');
    }

    btn.addEventListener('click', () => {
      const next = currentTheme() === 'dark' ? 'light' : 'dark';
      try {
        localStorage.setItem(storageKey, next);
      } catch (_) {}
      applyTheme(next);
      refreshLabel();
    });

    refreshLabel();
  }

  applyTheme(getPreferredTheme());

  document.addEventListener('DOMContentLoaded', () => {
    hookToggleButtons();
  });
})();
