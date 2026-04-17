/**
 * Theme toggle: syncs [data-app-theme] buttons with <html data-bs-theme>.
 * Initial theme is set inline in theme_head.html before paint.
 */
(function () {
  "use strict";

  var KEY = "recharge-desk-theme";

  function applyTheme(theme) {
    if (theme !== "light" && theme !== "dark") {
      return;
    }
    document.documentElement.setAttribute("data-bs-theme", theme);
    document.documentElement.style.colorScheme = theme;
    try {
      localStorage.setItem(KEY, theme);
    } catch (e) {}

    document.querySelectorAll(".app-theme-toggle [data-app-theme]").forEach(function (btn) {
      var on = btn.getAttribute("data-app-theme") === theme;
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function onReady() {
    document.querySelectorAll(".app-theme-toggle [data-app-theme]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        applyTheme(btn.getAttribute("data-app-theme"));
      });
    });
    var cur = document.documentElement.getAttribute("data-bs-theme");
    if (cur === "light" || cur === "dark") {
      applyTheme(cur);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", onReady);
  } else {
    onReady();
  }

  window.setAppTheme = applyTheme;
})();
