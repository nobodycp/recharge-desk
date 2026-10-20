/**
 * In-app confirm dialog. Replaces the browser confirm() used by hx-confirm,
 * onsubmit="return confirm(...)", and the unsaved-changes prompt.
 *
 * window.rdConfirm(message, { danger: true }) → Promise<boolean>
 * window.rdConfirm(message, { notice: true, loading: true }) → spinner, no buttons
 * window.rdConfirm(message, { notice: true, error: true }) → big X; morphs if loading
 */
(function () {
  "use strict";

  var host = null;
  var textEl = null;
  var iconEl = null;
  var okBtn = null;
  var cancelBtn = null;
  var pending = null;
  var lastFocus = null;
  var queue = [];
  var mode = "";
  var ERROR_ICON =
    '<svg viewBox="0 0 24 24" width="56" height="56" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true">' +
    '<circle cx="12" cy="12" r="10"></circle>' +
    '<path d="M15 9 9 15M9 9l6 6"></path>' +
    "</svg>";
  var SPINNER_ICON = '<span class="rd-confirm__spinner" aria-hidden="true"></span>';

  function label(name, fallback) {
    return (document.body && document.body.getAttribute(name)) || fallback;
  }

  function ensure() {
    // hx-boost replaces <body>, which detaches a dialog appended earlier.
    // Re-attach the same node (listeners stay) so later confirms still show.
    if (host) {
      if (!host.isConnected && document.body) document.body.appendChild(host);
      return;
    }
    host = document.createElement("dialog");
    host.className = "rd-confirm";
    host.innerHTML =
      '<div class="rd-confirm__icon" data-rd-confirm-icon hidden aria-hidden="true">' +
      ERROR_ICON +
      "</div>" +
      '<p class="rd-confirm__text" id="rd-confirm-text"></p>' +
      '<div class="rd-confirm__actions">' +
      '<button type="button" class="btn btn-outline-secondary" data-rd-confirm-cancel></button>' +
      '<button type="button" class="btn btn-primary" data-rd-confirm-ok></button>' +
      "</div>";
    document.body.appendChild(host);
    textEl = host.querySelector(".rd-confirm__text");
    iconEl = host.querySelector("[data-rd-confirm-icon]");
    okBtn = host.querySelector("[data-rd-confirm-ok]");
    cancelBtn = host.querySelector("[data-rd-confirm-cancel]");
    host.addEventListener("cancel", function (ev) {
      ev.preventDefault();
      if (mode === "loading") return;
      finish(false);
    });
    host.addEventListener("close", function () {
      host.classList.remove("is-open", "is-loading", "is-error", "is-notice");
      document.body.classList.remove("rd-confirm-open");
    });
    // Backdrop click (target is the <dialog> itself, not a child).
    host.addEventListener("click", function (ev) {
      if (ev.target === host) {
        if (mode === "loading") return;
        finish(false);
      }
    });
    // Bind the buttons directly. A delegated click on <dialog> used to close
    // synchronously, so the same gesture fell through to the Delete button
    // underneath (or Enter keyup activated it after focus returned) and ran
    // the confirm action after Cancel.
    function arm(btn, ok) {
      ["click", "mousedown", "pointerdown"].forEach(function (type) {
        btn.addEventListener(type, function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          if (type === "click") finish(ok);
        });
      });
    }
    arm(okBtn, true);
    arm(cancelBtn, false);
    document.addEventListener("keydown", function (ev) {
      if (!pending) return;
      if (ev.key === "Escape") {
        ev.preventDefault();
        if (mode === "loading") return;
        finish(false);
      } else if (ev.key === "Tab") {
        if (mode === "loading") {
          ev.preventDefault();
          return;
        }
        var nodes = [cancelBtn, okBtn].filter(function (b) { return b && !b.hidden; });
        if (!nodes.length) return;
        var index = nodes.indexOf(document.activeElement);
        ev.preventDefault();
        if (index === -1) {
          nodes[ev.shiftKey ? 0 : nodes.length - 1].focus();
          return;
        }
        nodes[(index + (ev.shiftKey ? -1 : 1) + nodes.length) % nodes.length].focus();
      }
    });
  }

  function finish(ok) {
    if (!pending) return;
    var resolve = pending;
    pending = null;
    mode = "";
    host.classList.remove("is-open", "is-loading", "is-error", "is-notice");
    document.body.classList.remove("rd-confirm-open");

    // Cover the page for one frame so the closing click / Enter keyup cannot
    // hit the control that opened the dialog (that looked like Cancel = OK).
    var shield = document.createElement("div");
    shield.setAttribute("aria-hidden", "true");
    shield.style.cssText = "position:fixed;inset:0;z-index:2147483647;";
    document.body.appendChild(shield);

    if (host.open) host.close();

    var focusEl = lastFocus;
    lastFocus = null;
    setTimeout(function () {
      if (shield.parentNode) shield.remove();
      // Only put focus back after Cancel. After Confirm the request navigates
      // or swaps the row; restoring focus to Delete would risk a second submit.
      if (!ok && focusEl && focusEl.isConnected && focusEl.focus) {
        try { focusEl.focus({ preventScroll: true }); } catch (e) { focusEl.focus(); }
      }
      resolve(ok);
      var next = queue.shift();
      if (next) open(next.message, next.options, next.resolve);
    }, 50);
  }

  function paint(message, options) {
    var notice = !!options.notice;
    var error = !!options.error;
    var loading = !!options.loading;
    mode = loading ? "loading" : error ? "error" : notice ? "notice" : "confirm";
    textEl.textContent = message;
    host.classList.toggle("is-notice", notice || loading);
    host.classList.toggle("is-error", error && !loading);
    host.classList.toggle("is-loading", loading);
    if (iconEl) {
      iconEl.hidden = !(error || loading);
      iconEl.innerHTML = loading ? SPINNER_ICON : ERROR_ICON;
    }
    cancelBtn.hidden = notice || loading;
    okBtn.hidden = loading;
    cancelBtn.textContent = label("data-rd-confirm-cancel", "Cancel");
    okBtn.textContent = label("data-rd-confirm-ok", "Confirm");
    okBtn.classList.remove("btn-primary", "btn-outline-danger");
    okBtn.classList.add(options.danger && !error ? "btn-outline-danger" : "btn-primary");
  }

  function open(message, options, resolve) {
    ensure();
    pending = resolve;
    lastFocus = document.activeElement;
    paint(message, options);
    try {
      if (!host.open) host.showModal();
    } catch (err) {
      host.classList.remove("is-open", "is-loading", "is-error", "is-notice");
      document.body.classList.remove("rd-confirm-open");
      pending = null;
      resolve(false);
      return;
    }
    document.body.classList.add("rd-confirm-open");
    requestAnimationFrame(function () {
      host.classList.add("is-open");
    });
    if (mode === "loading") return;
    (options.notice || !options.danger ? okBtn : cancelBtn).focus();
  }

  function morphLoadingToError(message, options, resolve) {
    // Drop the loading promise without navigating; the error dialog owns the next dismiss.
    var prev = pending;
    pending = resolve;
    paint(message, options);
    if (typeof prev === "function") {
      try { prev(false); } catch (err) { /* ignore */ }
    }
    okBtn.focus();
  }

  window.rdConfirm = function (message, options) {
    var text = String(message == null ? "" : message).trim();
    options = options || {};
    if (!text) return Promise.resolve(true);
    return new Promise(function (resolve) {
      // Loading → error in the same dialog (one surface, one dismiss).
      if (
        pending &&
        mode === "loading" &&
        options.error &&
        host &&
        (host.open || host.classList.contains("is-open"))
      ) {
        morphLoadingToError(text, options, resolve);
        return;
      }
      // Same error already open — attach to the existing dismiss, do not queue a twin.
      if (
        pending &&
        mode === "error" &&
        options.error &&
        host &&
        (host.open || host.classList.contains("is-open")) &&
        textEl &&
        textEl.textContent === text
      ) {
        var prev = pending;
        pending = function (ok) {
          try { prev(ok); } catch (err) { /* ignore */ }
          resolve(ok);
        };
        return;
      }
      if (pending) queue.push({ message: text, options: options, resolve: resolve });
      else open(text, options, resolve);
    });
  };

  function quotedConfirm(attr) {
    if (!attr) return "";
    var match = attr.match(/confirm\(\s*(['"])([\s\S]*?)\1\s*\)/);
    return match ? match[2] : "";
  }

  function isDanger(el) {
    return !!(el && el.classList && (
      el.classList.contains("btn-outline-danger") ||
      el.classList.contains("btn-danger") ||
      el.classList.contains("rd-icon-btn--danger")
    ));
  }

  // htmx calls window.confirm() itself when nothing cancels htmx:confirm.
  // Returning false stops that request; after the in-app dialog we submit
  // again with the question removed so the browser dialog cannot reappear.
  var nativeConfirm = window.confirm.bind(window);
  var pendingForm = null;
  var pendingSubmitter = null;
  document.addEventListener("submit", function (ev) {
    if (!ev.target || ev.target.nodeName !== "FORM") return;
    pendingForm = ev.target;
    pendingSubmitter = ev.submitter || null;
    var form = pendingForm;
    setTimeout(function () {
      if (pendingForm === form) {
        pendingForm = null;
        pendingSubmitter = null;
      }
    }, 0);
  }, true);

  function rememberConfirm(el, saved) {
    if (!el || !el.getAttribute) return;
    ["hx-confirm", "data-hx-confirm"].forEach(function (name) {
      if (!el.hasAttribute(name)) return;
      saved.push([el, name, el.getAttribute(name)]);
      el.removeAttribute(name);
    });
  }

  window.confirm = function (message) {
    var form = pendingForm;
    var submitter = pendingSubmitter;
    if (!form) return nativeConfirm(message);
    if (form.hasAttribute("data-rd-confirm-bypass")) return true;
    window.rdConfirm(message, { danger: isDanger(submitter) }).then(function (ok) {
      if (!ok || !form.isConnected) return;
      var saved = [];
      rememberConfirm(form, saved);
      rememberConfirm(submitter, saved);
      form.setAttribute("data-rd-confirm-bypass", "");
      if (submitter && submitter.focus) {
        try { submitter.focus({ preventScroll: true }); } catch (err) { submitter.focus(); }
      }
      if (form.requestSubmit) form.requestSubmit(submitter || undefined);
      else form.submit();
      saved.forEach(function (row) { row[0].setAttribute(row[1], row[2]); });
      form.removeAttribute("data-rd-confirm-bypass");
    });
    return false;
  };

  // Runs before an inline onsubmit="return confirm(...)" so the browser
  // dialog never opens. The confirmed submit is sent without that handler.
  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (!form || !form.getAttribute || form.hasAttribute("data-rd-confirm-bypass")) return;
    var message = quotedConfirm(form.getAttribute("onsubmit"));
    if (!message) return;
    ev.preventDefault();
    ev.stopPropagation();
    var danger = !!(ev.submitter && ev.submitter.classList && (
      ev.submitter.classList.contains("btn-outline-danger") ||
      ev.submitter.classList.contains("btn-danger") ||
      ev.submitter.classList.contains("rd-icon-btn--danger")
    ));
    var submitter = ev.submitter;
    window.rdConfirm(message, { danger: danger }).then(function (ok) {
      if (!ok || !form.isConnected) return;
      var saved = form.getAttribute("onsubmit");
      form.removeAttribute("onsubmit");
      form.setAttribute("data-rd-confirm-bypass", "");
      if (form.requestSubmit) form.requestSubmit(submitter || undefined);
      else form.submit();
      form.setAttribute("onsubmit", saved);
      form.removeAttribute("data-rd-confirm-bypass");
    });
  }, true);
})();
