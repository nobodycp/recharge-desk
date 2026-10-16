/**
 * In-app confirm dialog. Replaces the browser confirm() used by hx-confirm,
 * onsubmit="return confirm(...)", and the unsaved-changes prompt.
 *
 * window.rdConfirm(message, { danger: true }) → Promise<boolean>
 */
(function () {
  "use strict";

  var host = null;
  var textEl = null;
  var okBtn = null;
  var cancelBtn = null;
  var pending = null;
  var lastFocus = null;
  var queue = [];

  function label(name, fallback) {
    return (document.body && document.body.getAttribute(name)) || fallback;
  }

  function ensure() {
    if (host) return;
    host = document.createElement("dialog");
    host.className = "rd-confirm";
    host.innerHTML =
      '<p class="rd-confirm__text" id="rd-confirm-text"></p>' +
      '<div class="rd-confirm__actions">' +
      '<button type="button" class="btn btn-outline-secondary" data-rd-confirm-cancel></button>' +
      '<button type="button" class="btn btn-primary" data-rd-confirm-ok></button>' +
      "</div>";
    document.body.appendChild(host);
    host.addEventListener("cancel", function (ev) {
      ev.preventDefault();
      finish(false);
    });
    host.addEventListener("click", function (ev) {
      if (ev.target === host) finish(false);
    });
    textEl = host.querySelector(".rd-confirm__text");
    okBtn = host.querySelector("[data-rd-confirm-ok]");
    cancelBtn = host.querySelector("[data-rd-confirm-cancel]");
    host.addEventListener("click", function (ev) {
      if (ev.target.closest("[data-rd-confirm-ok]")) finish(true);
      else if (ev.target.closest("[data-rd-confirm-cancel]")) finish(false);
    });
    document.addEventListener("keydown", function (ev) {
      if (!pending) return;
      if (ev.key === "Escape") {
        ev.preventDefault();
        finish(false);
      } else if (ev.key === "Tab") {
        var nodes = [cancelBtn, okBtn];
        var index = nodes.indexOf(document.activeElement);
        ev.preventDefault();
        if (index === -1) {
          nodes[ev.shiftKey ? 0 : 1].focus();
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
    host.classList.remove("is-open");
    if (host.open) host.close();
    document.body.classList.remove("rd-confirm-open");
    if (lastFocus && lastFocus.focus) {
      try { lastFocus.focus({ preventScroll: true }); } catch (e) { lastFocus.focus(); }
    }
    lastFocus = null;
    resolve(ok);
    var next = queue.shift();
    if (next) open(next.message, next.options, next.resolve);
  }

  function open(message, options, resolve) {
    ensure();
    pending = resolve;
    lastFocus = document.activeElement;
    textEl.textContent = message;
    cancelBtn.textContent = label("data-rd-confirm-cancel", "Cancel");
    okBtn.textContent = label("data-rd-confirm-ok", "Confirm");
    okBtn.classList.toggle("btn-primary", !options.danger);
    okBtn.classList.toggle("btn-outline-danger", !!options.danger);
    if (!host.open) host.showModal();
    document.body.classList.add("rd-confirm-open");
    requestAnimationFrame(function () {
      host.classList.add("is-open");
    });
    (options.danger ? cancelBtn : okBtn).focus();
  }

  window.rdConfirm = function (message, options) {
    var text = String(message == null ? "" : message).trim();
    options = options || {};
    if (!text) return Promise.resolve(true);
    return new Promise(function (resolve) {
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
