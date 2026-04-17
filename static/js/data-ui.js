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
    },
    true
  );
})();
