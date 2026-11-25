// Apply the saved theme before first paint (no flash). A file, not an inline script, so the
// gateway's Content-Security-Policy can refuse every inline script (S-10, web/security-headers.conf).
try {
  var t = localStorage.getItem("factory-theme") || "dark";
  var d = t === "dark" || (t === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", d);
} catch (e) {}
