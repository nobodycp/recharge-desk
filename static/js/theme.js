/**
 * Theme toggle: a single sun/moon icon button flips
 * <html data-bs-theme> between "light" and "dark" and persists the
 * choice in localStorage. The initial theme is set inline in
 * theme_head.html before paint to avoid a flash of the wrong palette.
 *
 * Backwards-compatible with the old paired light/dark buttons (still
 * used inside Storybook-style screens) — those use [data-app-theme]
 * to pick a specific theme rather than toggle.
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

    // Old paired buttons (kept for backwards-compatibility).
    document.querySelectorAll(".app-theme-toggle [data-app-theme]").forEach(function (btn) {
      var on = btn.getAttribute("data-app-theme") === theme;
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });

    // New single-toggle buttons just need their aria-pressed state in
    // sync; the visible icon is driven entirely by CSS via
    // [data-bs-theme] on <html>.
    document.querySelectorAll("[data-app-theme-toggle]").forEach(function (btn) {
      btn.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
    });
  }

  function currentTheme() {
    var t = document.documentElement.getAttribute("data-bs-theme");
    return t === "dark" ? "dark" : "light";
  }

  function onReady() {
    document.querySelectorAll(".app-theme-toggle [data-app-theme]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        applyTheme(btn.getAttribute("data-app-theme"));
      });
    });

    document.querySelectorAll("[data-app-theme-toggle]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        applyTheme(currentTheme() === "dark" ? "light" : "dark");
      });
    });

    applyTheme(currentTheme());
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", onReady);
  } else {
    onReady();
  }

  window.setAppTheme = applyTheme;
})();
