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
 */
(function () {
  "use strict";

  function init() {
    var body = document.querySelector("[data-rd-nav]");
    if (!body) return;

    var panel = body.querySelector("[data-rd-nav-panel]");
    var backdrop = body.querySelector("[data-rd-nav-backdrop]");
    var toggle = body.querySelector("[data-rd-nav-toggle]");
    if (!panel || !toggle) return;

    var open = false;

    function apply() {
      panel.classList.toggle("is-open", open);
      if (backdrop) backdrop.classList.toggle("is-visible", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    }

    function setOpen(next) {
      if (next === open) return;
      open = !!next;
      apply();
    }

    toggle.addEventListener("click", function () {
      setOpen(!open);
    });

    if (backdrop) {
      backdrop.addEventListener("click", function () {
        setOpen(false);
      });
    }

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" || e.key === "Esc") setOpen(false);
    });

    apply();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
