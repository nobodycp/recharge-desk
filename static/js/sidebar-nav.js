/**
 * Sidebar accordion navigation.
 *
 * Markup contract (management_nav.html):
 *   <nav data-rd-sidebar-nav>
 *     <div data-rd-nav-group="operations|administration|catalog|reports">
 *       <button data-rd-nav-group-toggle aria-expanded="…">…</button>
 *       <div class="rd-nav-group-panel">…links…</div>
 *     </div>
 *   </nav>
 *
 * Behaviour:
 *   * All four groups are closed by default. The server-side template adds
 *     `is-open` to whichever group contains the current page so the active
 *     link is visible on load.
 *   * Clicking a group's header toggles that group independently; sibling
 *     groups are left alone.
 */
(function () {
  "use strict";

  function setGroupOpen(group, open) {
    if (!group) return;
    group.classList.toggle("is-open", open);
    var toggle = group.querySelector("[data-rd-nav-group-toggle]");
    if (toggle) toggle.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function init() {
    var nav = document.querySelector("[data-rd-sidebar-nav]");
    if (!nav) return;

    nav.querySelectorAll("[data-rd-nav-group-toggle]").forEach(function (toggle) {
      toggle.addEventListener("click", function () {
        var group = toggle.closest("[data-rd-nav-group]");
        if (!group) return;
        setGroupOpen(group, !group.classList.contains("is-open"));
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
