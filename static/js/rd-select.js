/**
 * Page selection for data grids. Header checkbox = this page.
 * Selecting every row on the page reveals "select all results" when the
 * filtered total is larger. The action bar covers the header and does not
 * change the table size. Inside a scrolling .rd-data-shell it sticks to the
 * visible top, so selecting rows after a scroll still shows the actions.
 * Shell script: one document listener, kept across
 * boosted navigations.
 */
(function () {
  "use strict";

  function gridOf(node) {
    return node && node.closest ? node.closest("[data-rd-select]") : null;
  }

  function rows(grid) {
    var wide = window.matchMedia("(min-width: 768px)").matches;
    var scope = grid.querySelector(wide ? "tbody" : ".rd-mobile-stack");
    return scope ? scope.querySelectorAll("[data-rd-select-row]") : [];
  }

  function dock(grid) {
    var slot = grid.querySelector(".rd-select-slot");
    var shell = grid.querySelector(".rd-data-shell");
    if (!slot) return;
    var wide = window.matchMedia("(min-width: 768px)").matches && shell;
    if (wide) {
      if (slot.parentElement !== shell) shell.insertBefore(slot, shell.firstChild);
      return;
    }
    var dual = grid.querySelector(".rd-table-dual");
    var before = dual || grid.firstChild;
    if (slot.parentElement === grid && slot.nextElementSibling === before) return;
    if (dual) grid.insertBefore(slot, dual);
  }

  function place(grid) {
    var thead = grid.querySelector("thead");
    var check = grid.querySelector("thead .rd-select-check");
    if (!thead || !check) return;
    var gridBox = grid.getBoundingClientRect();
    var headBox = thead.getBoundingClientRect();
    var checkBox = check.getBoundingClientRect();
    if (!headBox.height) return;
    var rtl = getComputedStyle(grid).direction === "rtl";
    var start = rtl ? (gridBox.right - checkBox.left) : (checkBox.right - gridBox.left);
    grid.style.setProperty("--rd-select-head", headBox.height + "px");
    grid.style.setProperty("--rd-select-top", (headBox.top - gridBox.top) + "px");
    grid.style.setProperty("--rd-select-check", start + "px");
  }

  function sync(grid) {
    if (!grid) return;
    var checks = rows(grid);
    var n = 0;
    checks.forEach(function (input) {
      var on = input.checked;
      if (on) n += 1;
      var line = input.closest("[data-rd-select-item]");
      if (line) line.classList.toggle("is-selected", on);
    });
    var pageFull = n > 0 && n === checks.length;
    var matchCount = parseInt(grid.getAttribute("data-match-count") || "0", 10);
    var selectAll = grid.getAttribute("data-rd-select-mode") === "all" && pageFull;
    if (!selectAll) grid.removeAttribute("data-rd-select-mode");
    grid.classList.toggle("is-selecting", n > 0);
    if (n > 0) {
      dock(grid);
      place(grid);
    }
    grid.querySelectorAll("[data-rd-select-count]").forEach(function (label) {
      label.textContent = String(selectAll ? matchCount : n);
    });
    grid.querySelectorAll("[data-rd-select-partial]").forEach(function (el) {
      el.hidden = selectAll;
    });
    grid.querySelectorAll("[data-rd-select-all-note]").forEach(function (el) {
      el.hidden = !selectAll;
    });
    grid.querySelectorAll("[data-rd-select-match]").forEach(function (el) {
      el.hidden = !(pageFull && !selectAll && matchCount > checks.length);
    });
    grid.querySelectorAll("[data-rd-select-needs]").forEach(function (btn) {
      var flag = btn.getAttribute("data-rd-select-needs");
      if (selectAll) {
        btn.disabled = false;
        return;
      }
      var ok = false;
      checks.forEach(function (input) {
        if (input.checked && input.getAttribute("data-" + flag) === "1") ok = true;
      });
      btn.disabled = !ok;
    });
    grid.querySelectorAll("[data-rd-select-all]").forEach(function (all) {
      all.checked = pageFull;
      all.indeterminate = n > 0 && n < checks.length;
    });
  }

  function fill(form) {
    var grid = gridOf(form);
    var slot = form.querySelector("[data-rd-select-values]");
    if (!grid || !slot) return false;
    slot.replaceChildren();
    if (grid.getAttribute("data-rd-select-mode") === "all") {
      var flag = document.createElement("input");
      flag.type = "hidden";
      flag.name = form.getAttribute("data-match-param") || "match";
      flag.value = "all";
      slot.appendChild(flag);
      return true;
    }
    var name = form.getAttribute("data-value-name") || "refs";
    var n = 0;
    var posted = false;
    rows(grid).forEach(function (input) {
      if (!input.checked) return;
      n += 1;
      if (input.name && input.form === form) {
        posted = true;
        return;
      }
      var hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = name;
      hidden.value = input.value;
      slot.appendChild(hidden);
    });
    return n > 0 || posted;
  }

  window.rdSelectSync = function (node) {
    var grid = node && node.matches && node.matches("[data-rd-select]") ? node : gridOf(node);
    if (!grid && node && node.querySelector) grid = node.querySelector("[data-rd-select]");
    sync(grid);
  };

  document.addEventListener("change", function (ev) {
    var grid = gridOf(ev.target);
    if (!grid) return;
    if (ev.target.matches("[data-rd-select-all]")) {
      grid.removeAttribute("data-rd-select-mode");
      var on = ev.target.checked;
      rows(grid).forEach(function (input) { input.checked = on; });
    }
    if (ev.target.matches("[data-rd-select-row]")) {
      grid.removeAttribute("data-rd-select-mode");
    }
    if (ev.target.matches("[data-rd-select-row], [data-rd-select-all]")) sync(grid);
  });

  document.addEventListener("click", function (ev) {
    var grid = gridOf(ev.target);
    if (!grid) return;
    if (ev.target.closest("[data-rd-select-clear]")) {
      grid.removeAttribute("data-rd-select-mode");
      rows(grid).forEach(function (input) { input.checked = false; });
      sync(grid);
      return;
    }
    if (ev.target.closest("[data-rd-select-match]")) {
      grid.setAttribute("data-rd-select-mode", "all");
      rows(grid).forEach(function (input) { input.checked = true; });
      sync(grid);
    }
  });

  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (!form || !form.matches || !form.matches("[data-rd-select-form]")) return;
    if (!fill(form)) {
      ev.preventDefault();
      ev.stopPropagation();
    }
  }, true);

  window.addEventListener("resize", function () {
    document.querySelectorAll("[data-rd-select].is-selecting").forEach(function (grid) {
      dock(grid);
      place(grid);
    });
  });
})();
