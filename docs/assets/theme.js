// Theme toggle shared by both pages. Light by default; the choice is remembered per browser.
(() => {
  const root = document.documentElement;
  try { const t = localStorage.getItem("theme"); if (t) root.dataset.theme = t; } catch (e) {}
  const isDark = () => root.dataset.theme === "dark";  // light by default
  const sun = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
  const moon = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
  document.addEventListener("DOMContentLoaded", () => {
    const b = document.getElementById("theme");
    if (!b) return;
    const paint = () => { b.innerHTML = isDark() ? sun : moon; b.setAttribute("aria-label", isDark() ? "Switch to light theme" : "Switch to dark theme"); };
    paint();
    b.addEventListener("click", () => {
      root.dataset.theme = isDark() ? "light" : "dark";
      try { localStorage.setItem("theme", root.dataset.theme); } catch (e) {}
      paint();
      window.dispatchEvent(new Event("themechange"));
    });
  });
})();
