// Apply the saved theme before first paint so there is no flash. A file, not an inline script,
// so the dashboard bridge's Content-Security-Policy (script-src 'self') allows it.
try {
  var t = localStorage.getItem("p6-theme");
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
} catch (e) {}
