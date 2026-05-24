const themeToggle = document.getElementById('themeToggle');

function getStoredTheme() {
  return localStorage.getItem('siteTheme');
}

function getPreferredTheme() {
  const stored = getStoredTheme();
  if (stored) return stored;
  return window.matchMedia('(prefers-color-scheme:dark)').matches ? 'dark' : 'light';
}

function updateToggleLabel(theme) {
  if (!themeToggle) return;
  themeToggle.textContent = theme === 'dark' ? 'Light Mode' : 'Dark Mode';
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem('siteTheme', theme);
  updateToggleLabel(theme);
}

if (themeToggle) {
  const initialTheme = getPreferredTheme();
  setTheme(initialTheme);

  themeToggle.addEventListener('click', () => {
    const nextTheme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    setTheme(nextTheme);
  });
}
