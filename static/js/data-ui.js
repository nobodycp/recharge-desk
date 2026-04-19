/**
 * Data UI: copy-to-clipboard, HTMX grid loading, lightweight feedback.
 */
(function () {
  function showToast(message) {
    var el = document.createElement("div");
    el.className = "rd-toast shadow-sm";
    el.setAttribute("role", "status");
    el.textContent = message;
    document.body.appendChild(el);
    requestAnimationFrame(function () {
      el.classList.add("is-visible");
    });
    setTimeout(function () {
      el.classList.remove("is-visible");
      setTimeout(function () {
        el.remove();
      }, 220);
    }, 1600);
  }

  document.body.addEventListener("click", function (e) {
    var btn = e.target.closest(".rd-copy-ref");
    if (!btn) return;
    var text = btn.getAttribute("data-copy");
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      e.preventDefault();
      navigator.clipboard.writeText(text).then(
        function () {
          var msg = btn.getAttribute("data-copy-done");
          showToast(msg || "Copied");
        },
        function () {}
      );
    }
  });

  document.body.addEventListener("htmx:beforeRequest", function (evt) {
    var t = evt.detail.target;
    if (t && t.classList && t.classList.contains("rd-datagrid")) {
      t.classList.add("is-loading");
    }
  });

  document.body.addEventListener("htmx:afterSwap", function (evt) {
    var t = evt.detail.target;
    if (t && t.classList && t.classList.contains("rd-datagrid")) {
      t.classList.remove("is-loading");
    }
  });

  document.body.addEventListener("htmx:responseError", function (evt) {
    var t = evt.detail.target;
    if (t && t.classList && t.classList.contains("rd-datagrid")) {
      t.classList.remove("is-loading");
    }
  });

  /* Row actions <details> panel: fixed position so it is not clipped by scroll shells */
  var panelPosProps = ["position", "top", "left", "right", "max-height", "overflow-y", "z-index"];

  function clearActionsPanelPosition(panel) {
    if (!panel) return;
    panelPosProps.forEach(function (p) {
      panel.style.removeProperty(p);
    });
  }

  function placeActionsPanel(details) {
    var panel = details.querySelector(".rd-actions-details__panel");
    var summary = details.querySelector("summary");
    if (!panel || !summary) return;
    if (!details.open) {
      clearActionsPanelPosition(panel);
      return;
    }
    document.querySelectorAll("details.rd-actions-details[open]").forEach(function (other) {
      if (other !== details) {
        other.removeAttribute("open");
        var op = other.querySelector(".rd-actions-details__panel");
        clearActionsPanelPosition(op);
      }
    });

    var r = summary.getBoundingClientRect();
    var margin = 8;
    var vw = window.innerWidth;
    var vh = window.innerHeight;

    panel.style.position = "fixed";
    panel.style.zIndex = "1060";
    panel.style.top = r.bottom + margin + "px";
    panel.style.right = "auto";

    requestAnimationFrame(function () {
      var w = panel.getBoundingClientRect().width || 176;
      var left = r.right - w;
      left = Math.max(margin, Math.min(left, vw - w - margin));
      panel.style.left = left + "px";

      var spaceBelow = vh - r.bottom - margin * 2;
      var ph = panel.getBoundingClientRect().height;
      if (spaceBelow < 140 && ph < r.top - margin * 2) {
        panel.style.top = Math.max(margin, r.top - ph - margin) + "px";
        panel.style.maxHeight = Math.min(320, r.top - margin * 2) + "px";
      } else {
        panel.style.maxHeight = Math.min(320, Math.max(120, spaceBelow)) + "px";
      }
      panel.style.overflowY = "auto";
    });
  }

  document.body.addEventListener(
    "toggle",
    function (evt) {
      var el = evt.target;
      if (!el || !el.classList || !el.classList.contains("rd-actions-details")) return;
      placeActionsPanel(el);
    },
    true
  );

  function closeAllActionsPanels() {
    document.querySelectorAll("details.rd-actions-details[open]").forEach(function (d) {
      d.removeAttribute("open");
      clearActionsPanelPosition(d.querySelector(".rd-actions-details__panel"));
    });
  }

  document.addEventListener(
    "scroll",
    function () {
      closeAllActionsPanels();
    },
    true
  );

  window.addEventListener("resize", function () {
    closeAllActionsPanels();
  });

  document.addEventListener(
    "click",
    function (e) {
      var d = e.target.closest("details.rd-actions-details");
      document.querySelectorAll("details.rd-actions-details[open]").forEach(function (openDet) {
        if (openDet === d || openDet.contains(e.target)) return;
        openDet.removeAttribute("open");
        clearActionsPanelPosition(openDet.querySelector(".rd-actions-details__panel"));
      });

      /* Same outside-click contract for the topbar profile menu. The user
         menu is positioned with plain CSS so we just need to flip [open]. */
      var um = e.target.closest("details.rd-user-menu");
      document.querySelectorAll("details.rd-user-menu[open]").forEach(function (openMenu) {
        if (openMenu === um || openMenu.contains(e.target)) return;
        openMenu.removeAttribute("open");
      });
    },
    true
  );

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    document.querySelectorAll("details.rd-user-menu[open]").forEach(function (m) {
      m.removeAttribute("open");
    });
  });

  /* Collapsible filter cards: keep the "N active" badge in sync with the
     current form state after any htmx-driven submit, so the count stays
     accurate even though we never reload the page. */
  var FILTER_NOISE = { sort: 1, order: 1, page: 1, csrfmiddlewaretoken: 1 };

  function countActiveFilters(form) {
    if (!form) return 0;
    var seen = {};
    var count = 0;
    var fields = form.querySelectorAll("input, select, textarea");
    for (var i = 0; i < fields.length; i++) {
      var el = fields[i];
      var name = el.name;
      if (!name || FILTER_NOISE[name] || seen[name]) continue;
      var t = (el.type || "").toLowerCase();
      if (t === "checkbox" || t === "radio") {
        if (!el.checked) continue;
      } else if (el.value === "" || el.value == null) {
        continue;
      }
      seen[name] = 1;
      count++;
    }
    return count;
  }

  function refreshFilterBadge(card) {
    if (!card) return;
    var form = card.querySelector("form");
    var badge = card.querySelector("[data-rd-filter-count]");
    if (!form || !badge) return;
    var n = countActiveFilters(form);
    badge.textContent = String(n);
    badge.style.display = n > 0 ? "" : "none";
  }

  document.body.addEventListener("htmx:afterRequest", function (evt) {
    var src = evt.detail && evt.detail.elt;
    if (!src) return;
    var card = src.closest && src.closest(".rd-filter-card");
    if (card) refreshFilterBadge(card);
  });

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".rd-filter-card").forEach(refreshFilterBadge);
  });
})();
