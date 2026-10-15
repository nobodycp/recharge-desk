/**
 * Reconcile pages (Layan + Sky): result tabs and number search.
 *
 * Markup contract (templates/companies/partials/reconcile_results.html):
 *   [data-rd-reconcile]                root of one result
 *   [data-rd-rec-tab="<id>"]           tab buttons (role="tab", aria-controls)
 *   [data-rd-rec-panel="<id>"]         panels (role="tabpanel", `hidden` unless active)
 *   [data-rd-rec-group]                a card or sub-list; hidden while searching if it has no hit
 *   [data-rd-rec-row][data-phone]      one number (normalised digits)
 *   input[data-rd-rec-search]          the search box
 *   [data-rd-rec-count]                aria-live count; data-one / data-many ("__n__" placeholder)
 *   [data-rd-rec-empty]                "no number matches" block
 *
 * Searching shows every panel and both action lists, narrowed to matching rows, and
 * switches the tabs off; clearing the box restores the tab that was active before.
 * State is per root element (`data-rd-rec-ready`), so a second onLoad on the same
 * root does not bind twice, while a fresh boosted visit (new root) binds again.
 */
(function () {
  "use strict";

  var SEL = {
    root: "[data-rd-reconcile]",
    tab: "[data-rd-rec-tab]",
    panel: "[data-rd-rec-panel]",
    group: "[data-rd-rec-group]",
    row: "[data-rd-rec-row]",
    search: "input[data-rd-rec-search]",
    count: "[data-rd-rec-count]",
    empty: "[data-rd-rec-empty]",
  };

  var ARABIC_DIGITS = /[\u0660-\u0669\u06F0-\u06F9]/g;

  function asciiDigits(text) {
    return String(text == null ? "" : text).replace(ARABIC_DIGITS, function (ch) {
      var code = ch.charCodeAt(0);
      return String(code >= 0x06f0 ? code - 0x06f0 : code - 0x0660);
    });
  }

  /**
   * What the user typed as comparable phone digits:
   * digits only; 972… / 00972… -> 0…; a full 9-digit 5… -> 05…; partial input stays as typed.
   */
  function normalizeQuery(text) {
    var digits = asciiDigits(text).replace(/\D+/g, "");
    if (digits.indexOf("00972") === 0) digits = digits.slice(2);
    if (digits.indexOf("972") === 0 && digits.length > 3) return "0" + digits.slice(3);
    if (digits.length === 9 && digits.charAt(0) === "5") return "0" + digits;
    return digits;
  }

  function phoneMatches(phone, query) {
    if (phone.indexOf(query) !== -1) return true;
    if (query.charAt(0) !== "0" && phone.indexOf("0" + query) !== -1) return true;
    // A source phone stored without its leading 0 still matches a typed 05….
    return query.charAt(0) === "0" && phone.charAt(0) !== "0" && phone.indexOf(query.slice(1)) === 0;
  }

  function isRtl(root) {
    var doc = root.ownerDocument;
    var html = doc && doc.documentElement;
    return !!html && html.getAttribute("dir") === "rtl";
  }

  function all(root, selector) {
    return Array.prototype.slice.call(root.querySelectorAll(selector));
  }

  function initReconcile(root) {
    if (!root || root.hasAttribute("data-rd-rec-ready")) return;
    root.setAttribute("data-rd-rec-ready", "");

    var tabs = all(root, SEL.tab);
    var panels = all(root, SEL.panel);
    var groups = all(root, SEL.group);
    var rows = all(root, SEL.row);
    var input = root.querySelector(SEL.search);
    var count = root.querySelector(SEL.count);
    var empty = root.querySelector(SEL.empty);

    var active = "";
    tabs.forEach(function (tab) {
      if (!active && tab.getAttribute("aria-selected") === "true") active = tab.getAttribute("data-rd-rec-tab");
    });
    if (!active && tabs.length) active = tabs[0].getAttribute("data-rd-rec-tab");

    function applyTab() {
      tabs.forEach(function (tab) {
        var on = tab.getAttribute("data-rd-rec-tab") === active;
        tab.setAttribute("aria-selected", on ? "true" : "false");
        tab.setAttribute("tabindex", on ? "0" : "-1");
        tab.classList.toggle("active", on);
      });
      panels.forEach(function (panel) {
        panel.hidden = panel.getAttribute("data-rd-rec-panel") !== active;
      });
    }

    function selectTab(id, focus) {
      active = id;
      applyTab();
      if (focus) {
        tabs.forEach(function (tab) {
          if (tab.getAttribute("data-rd-rec-tab") === id && typeof tab.focus === "function") tab.focus();
        });
      }
    }

    function searching() {
      return root.hasAttribute("data-rd-rec-searching");
    }

    function setCount(matches) {
      if (!count) return;
      if (!matches) {
        count.textContent = "";
        return;
      }
      var template = matches === 1 ? count.getAttribute("data-one") : count.getAttribute("data-many");
      count.textContent = String(template || "").replace("__n__", String(matches));
    }

    function clearSearch() {
      root.removeAttribute("data-rd-rec-searching");
      rows.forEach(function (row) { row.hidden = false; });
      groups.forEach(function (group) { group.hidden = false; });
      tabs.forEach(function (tab) { tab.removeAttribute("aria-disabled"); });
      if (empty) empty.hidden = true;
      setCount(0);
      applyTab();
    }

    function runSearch() {
      var query = normalizeQuery(input.value);
      if (!query) {
        if (searching()) clearSearch();
        return;
      }
      root.setAttribute("data-rd-rec-searching", "");
      tabs.forEach(function (tab) { tab.setAttribute("aria-disabled", "true"); });

      var seen = {};
      var matches = 0;
      rows.forEach(function (row) {
        var phone = asciiDigits(row.getAttribute("data-phone") || "");
        var hit = phoneMatches(phone, query);
        row.hidden = !hit;
        if (hit && !seen[phone]) {
          seen[phone] = true;
          matches += 1;
        }
      });
      groups.forEach(function (group) {
        var hasHit = all(group, SEL.row).some(function (row) { return !row.hidden; });
        group.hidden = !hasHit;
      });
      panels.forEach(function (panel) {
        panel.hidden = !all(panel, SEL.row).some(function (row) { return !row.hidden; });
      });
      if (empty) empty.hidden = matches > 0;
      setCount(matches);
    }

    function move(from, key) {
      var ids = tabs.map(function (tab) { return tab.getAttribute("data-rd-rec-tab"); });
      var at = ids.indexOf(from);
      if (at === -1 || !ids.length) return;
      var step = 0;
      if (key === "ArrowRight") step = isRtl(root) ? -1 : 1;
      else if (key === "ArrowLeft") step = isRtl(root) ? 1 : -1;
      var to;
      if (key === "Home") to = 0;
      else if (key === "End") to = ids.length - 1;
      else if (step) to = (at + step + ids.length) % ids.length;
      else return;
      selectTab(ids[to], true);
    }

    tabs.forEach(function (tab) {
      var id = tab.getAttribute("data-rd-rec-tab");
      tab.addEventListener("click", function (event) {
        if (searching()) return;
        if (event && event.preventDefault) event.preventDefault();
        selectTab(id, false);
      });
      tab.addEventListener("keydown", function (event) {
        if (searching()) return;
        var key = event && event.key;
        if (key === "ArrowRight" || key === "ArrowLeft" || key === "Home" || key === "End") {
          if (event.preventDefault) event.preventDefault();
          move(id, key);
        }
      });
    });

    if (input) {
      input.addEventListener("input", runSearch);
      // type="search" fires "search" when its clear (x) button is used.
      input.addEventListener("search", runSearch);
      // The search box is not a form: Enter would otherwise submit the page's run form.
      input.addEventListener("keydown", function (event) {
        if (event && event.key === "Enter" && event.preventDefault) event.preventDefault();
      });
      if (input.value) runSearch();
    }
  }

  function initAll(scope) {
    var node = scope || document;
    if (node.matches && node.matches(SEL.root)) initReconcile(node);
    if (node.querySelectorAll) all(node, SEL.root).forEach(initReconcile);
  }

  var api = { normalizeQuery: normalizeQuery, initReconcile: initReconcile, initAll: initAll };

  if (typeof module !== "undefined" && module.exports) module.exports = api;

  if (typeof window !== "undefined" && typeof document !== "undefined") {
    window.rdReconcile = api;
    /* Run now: the root is above this script. After a boosted visit the script is
       re-inserted after the body's htmx:load already fired, so onLoad alone would
       miss it. The per-root marker keeps this idempotent. */
    initAll(document);
    if (window.htmx && window.htmx.onLoad) {
      window.htmx.onLoad(initAll);
    } else {
      document.addEventListener("DOMContentLoaded", function () { initAll(document); });
    }
  }
})();
