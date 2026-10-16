/**
 * App-wide toasts — the only place success / error feedback is drawn.
 *
 * Sources:
 *   - Django messages, rendered by partials/messages.html into hidden
 *     [data-rd-flash] queues (full loads and boosted swaps alike);
 *   - HX-Trigger "rdToast": {"message", "level"} or a list of them
 *     (core.middleware.HtmxNavigationMiddleware, sales.views._shared);
 *   - HX-Trigger "rdSaleActionError": "message";
 *   - window.rdToast(message, level) from page scripts (alert() maps here too);
 *   - default-rendered form.non_field_errors lists.
 */
(function () {
  "use strict";

  var DURATION = { success: 4000, info: 4500, warning: 6000, error: 7000 };
  var MAX_VISIBLE = 4;
  var ICONS = {
    success: '<path d="M20 6 9 17l-5-5"/>',
    error: '<circle cx="12" cy="12" r="9"/><path d="M15 9l-6 6M9 9l6 6"/>',
    warning: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 16v-4M12 8h.01"/>',
  };
  var recent = {};

  function region() {
    var el = document.getElementById("rd-toast-region");
    if (!el) {
      el = document.createElement("div");
      el.id = "rd-toast-region";
      el.className = "rd-toast-region";
      document.body.appendChild(el);
    }
    return el;
  }

  function normalizeLevel(level) {
    level = String(level || "").toLowerCase();
    if (level === "danger") level = "error";
    return DURATION[level] ? level : "info";
  }

  function dismiss(toast) {
    if (toast.__rdClosing) return;
    toast.__rdClosing = true;
    toast.classList.remove("is-visible");
    toast.classList.add("is-leaving");
    setTimeout(function () {
      toast.remove();
    }, 260);
  }

  function show(message, level) {
    var text = String(message == null ? "" : message).trim();
    if (!text) return null;
    level = normalizeLevel(level);

    var key = level + "|" + text;
    var now = Date.now();
    if (recent[key] && now - recent[key] < 1500) return null;
    recent[key] = now;

    var host = region();
    var toast = document.createElement("div");
    toast.className = "rd-toast rd-toast--" + level;
    toast.setAttribute("role", level === "error" ? "alert" : "status");
    toast.style.setProperty("--rd-toast-duration", DURATION[level] + "ms");
    toast.innerHTML =
      '<span class="rd-toast__icon" aria-hidden="true">' +
      '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">' +
      ICONS[level] +
      "</svg></span>" +
      '<p class="rd-toast__text"></p>' +
      '<button type="button" class="rd-toast__close">' +
      '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>' +
      "</button>" +
      '<span class="rd-toast__timer" aria-hidden="true"></span>';
    toast.querySelector(".rd-toast__text").textContent = text;

    var close = toast.querySelector(".rd-toast__close");
    close.setAttribute("aria-label", host.getAttribute("data-close-label") || "Close");
    close.addEventListener("click", function () {
      dismiss(toast);
    });
    toast.querySelector(".rd-toast__timer").addEventListener("animationend", function () {
      dismiss(toast);
    });

    host.insertBefore(toast, host.firstChild);
    var live = host.querySelectorAll(".rd-toast:not(.is-leaving)");
    for (var i = MAX_VISIBLE; i < live.length; i++) dismiss(live[i]);

    void toast.offsetWidth; // commit the hidden state so the enter transition runs
    toast.classList.add("is-visible");
    return toast;
  }

  function showPayload(payload, fallbackLevel) {
    if (payload == null) return;
    if (Array.isArray(payload)) {
      payload.forEach(function (p) {
        showPayload(p, fallbackLevel);
      });
    } else if (typeof payload === "object") {
      show(payload.message || payload.msg || "", payload.level || fallbackLevel);
    } else {
      show(payload, fallbackLevel);
    }
  }

  function eventPayload(evt) {
    var detail = (evt && evt.detail) || {};
    return detail.value !== undefined ? detail.value : detail;
  }

  function within(root, selector) {
    var found = Array.prototype.slice.call(root.querySelectorAll(selector));
    if (root.matches && root.matches(selector)) found.unshift(root);
    return found;
  }

  // Also picks up {{ form.non_field_errors }} rendered the default Django way
  // (<ul class="errorlist nonfield">), dropping a wrapper box it leaves empty.
  function consumeFlash(root) {
    if (!root || !root.querySelectorAll) return;
    within(root, "[data-rd-flash]").forEach(function (queue) {
      queue.querySelectorAll("[data-level]").forEach(function (item) {
        var toast = show(item.textContent, item.getAttribute("data-level"));
        if (toast && item.hasAttribute("data-rd-charge-pending")) {
          toast.setAttribute("data-rd-charge-pending", "");
        }
      });
      queue.remove();
    });
    within(root, "ul.errorlist.nonfield").forEach(function (list) {
      list.querySelectorAll("li").forEach(function (item) {
        show(item.textContent, "error");
      });
      var box = list.parentElement;
      list.remove();
      if (box && box.matches(".alert, [role=alert]") && !box.textContent.trim()) box.remove();
    });
  }

  document.addEventListener("rdToast", function (evt) {
    showPayload(eventPayload(evt), "info");
  });

  document.addEventListener("rdSaleActionError", function (evt) {
    var payload = eventPayload(evt);
    show(typeof payload === "string" && payload ? payload : region().getAttribute("data-action-failed"), "error");
  });

  document.addEventListener("htmx:load", function (evt) {
    consumeFlash(evt.detail && evt.detail.elt);
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      consumeFlash(document.body);
    });
  } else {
    consumeFlash(document.body);
  }

  window.rdToast = show;
  // Page scripts that still call alert() get a toast instead of a blocking dialog.
  window.alert = function (message) {
    show(message, "error");
  };
})();
