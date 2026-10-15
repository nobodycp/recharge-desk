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
 *   * `aria-expanded` is kept in sync with the state,
 *   * below 992 px a CLOSED panel is `inert` (its off-screen links are not
 *     tabbable / readable); at >= 992 px the sidebar is always visible and is
 *     never inert,
 *   * Escape returns focus to the toggle button,
 *   * crossing the breakpoint (matchMedia change) re-syncs; resizing up to
 *     desktop also closes a drawer that was left open.
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

  // Keep in step with the `max-width: 991.98px` drawer breakpoint in design-system.css.
  var mq = window.matchMedia ? window.matchMedia("(max-width: 991.98px)") : null;

  function isDrawer() {
    return !!(mq && mq.matches);
  }

  function syncInert() {
    var p = parts();
    if (!p) return;
    var closed = !p.panel.classList.contains("is-open");
    if (isDrawer() && closed) p.panel.setAttribute("inert", "");
    else p.panel.removeAttribute("inert");
  }

  function setOpen(open) {
    var p = parts();
    if (!p) return;
    p.panel.classList.toggle("is-open", open);
    if (p.backdrop) p.backdrop.classList.toggle("is-visible", open);
    if (p.toggle) p.toggle.setAttribute("aria-expanded", open ? "true" : "false");
    syncInert();
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
    if ((e.key === "Escape" || e.key === "Esc") && isOpen()) {
      setOpen(false);
      var p = parts();
      if (p && p.toggle && p.toggle.focus) p.toggle.focus();
    }
  });

  function onBreakpointChange() {
    // A drawer left open must not reappear open after a round trip to desktop.
    if (!isDrawer() && isOpen()) setOpen(false);
    else syncInert();
  }
  if (mq) {
    if (mq.addEventListener) mq.addEventListener("change", onBreakpointChange);
    else if (mq.addListener) mq.addListener(onBreakpointChange);
  }

  // A boosted body swap brings a fresh (closed) panel from the server: re-sync it.
  document.addEventListener("htmx:load", syncInert);
  syncInert();
})();
