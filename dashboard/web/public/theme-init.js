// Apply saved display choices before first paint so there is no flash. A file, not an inline script,
// so the dashboard bridge's Content-Security-Policy (script-src 'self') allows it.
// These are per-browser display preferences only; nothing about a project or result is kept here.
try {
  var root = document.documentElement;
  var t = localStorage.getItem("p6-theme");
  if (t === "light" || t === "dark") root.dataset.theme = t;
  else if (window.matchMedia && matchMedia("(prefers-color-scheme: light)").matches) root.dataset.theme = "light";
  if (localStorage.getItem("p6-terms") === "show") root.dataset.terms = "show";
  if (localStorage.getItem("p6-density") === "compact") root.dataset.density = "compact";
} catch (e) {}
