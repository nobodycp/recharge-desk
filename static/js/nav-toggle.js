/**
 * Mobile sidebar toggle.
 *
 * Replaces the previous Alpine.js component on <body> in
 * base_management.html. Tiny enough that pulling in a 44 KB Alpine
 * runtime — and the `'unsafe-eval'` CSP hole it required — was
 * disproportionate.
 *
 * Markup contract:
 *   <body data-rd-nav>
 *     <div data-rd-nav-backdrop></div>
 *     <aside data-rd-nav-panel></aside>
 *     <button data-rd-nav-toggle aria-controls="...">…</button>
 *
 * Behaviour:
 *   * clicking the toggle button flips the open state,
 *   * clicking the backdrop closes the panel,
 *   * pressing Escape anywhere closes the panel,
 *   * `is-open` / `is-visible` classes are added/removed so the
 *     existing CSS keeps working untouched,
 *   * `aria-expanded` is kept in sync with the state.
 *
 * Listeners are delegated and the state lives on the panel's class, so the
 * toggle keeps working after boosted navigation replaces the shell markup.
 */
(function () {
  "use strict";

  function parts() {
    var body = document.querySelector("[data-rd-nav]");
    if (!body) return null;
    var panel = body.querySelector("[data-rd-nav-panel]");
    if (!panel) return null;
    return {
      panel: panel,
      backdrop: body.querySelector("[data-rd-nav-backdrop]"),
      toggle: body.querySelector("[data-rd-nav-toggle]"),
    };
  }

  function setOpen(open) {
    var p = parts();
    if (!p) return;
    p.panel.classList.toggle("is-open", open);
    if (p.backdrop) p.backdrop.classList.toggle("is-visible", open);
    if (p.toggle) p.toggle.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function isOpen() {
    var p = parts();
    return !!(p && p.panel.classList.contains("is-open"));
  }

  document.addEventListener("click", function (e) {
    if (!e.target.closest) return;
    if (e.target.closest("[data-rd-nav-toggle]")) {
      setOpen(!isOpen());
    } else if (e.target.closest("[data-rd-nav-backdrop]")) {
      setOpen(false);
    }
  });

  document.addEventListener("keydown", function (e) {
    if ((e.key === "Escape" || e.key === "Esc") && isOpen()) setOpen(false);
  });
})();
