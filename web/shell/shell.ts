const THEME_KEY = 'library:theme:v1';
const THEMES = new Set(['light', 'dark', 'sepia']);

function applySavedTheme(): void {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved !== null && THEMES.has(saved)) {
      document.documentElement.dataset.theme = saved;
      return;
    }
  } catch {
    // localStorage may be unavailable in hardened browser contexts.
  }

  document.documentElement.removeAttribute('data-theme');
}

applySavedTheme();

console.info('Virtual Library shell scaffold loaded.');
