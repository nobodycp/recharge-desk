/**
 * Pearl login page (templates/accounts/login.html).
 *
 * - Fields: `.is-focused` / `.is-filled` on `.rd-login-field`; while one is
 *   focused `body.is-typing` collapses the mobile hero.
 * - Password show/hide toggle, Caps Lock hint.
 * - Submit: empty fields shake (`.is-invalid`) instead of posting; otherwise
 *   the button shows a spinner and is disabled against double submits.
 */
(function () {
  "use strict";

  var MOBILE = "(max-width: 991.98px)";
  var BLUR_GRACE_MS = 120;
  var LOADING_RESET_MS = 12000;

  function init() {
    var form = document.querySelector("form[data-rd-login]");
    if (!form) return;

    var body = document.body;
    var submit = form.querySelector("button[data-rd-login-submit]");
    var toggle = form.querySelector("[data-rd-password-toggle]");
    var caps = form.querySelector("[data-rd-caps-hint]");
    var fields = Array.prototype.map.call(form.querySelectorAll(".rd-login-field"), function (field) {
      return { el: field, input: field.querySelector("input") };
    }).filter(function (f) { return f.input; });
    var password = form.querySelector('input[name="password"]');
    var loadingTimer = null;

    function isMobile() {
      return window.matchMedia ? window.matchMedia(MOBILE).matches : false;
    }

    // On phones the autofocused username would collapse the hero before the
    // user ever sees it. Autofocus can land after this runs, so drop any
    // focus that arrives before the first real interaction.
    var armed = false;
    function arm() { armed = true; }
    document.addEventListener("pointerdown", arm, true);
    document.addEventListener("keydown", arm, true);
    // Assistive tech can activate without pointer or key events.
    document.addEventListener("click", arm, true);
    if (isMobile() && fields.some(function (f) { return f.input === document.activeElement; })) {
      document.activeElement.blur();
    }

    function syncTyping() {
      var focused = fields.some(function (f) { return f.el.classList.contains("is-focused"); });
      body.classList.toggle("is-typing", focused);
    }

    function syncFilled(f) {
      f.el.classList.toggle("is-filled", !!f.input.value);
    }

    function syncFromActive() {
      fields.forEach(function (f) {
        f.el.classList.toggle("is-focused", f.input === document.activeElement);
        syncFilled(f);
      });
      syncTyping();
    }

    fields.forEach(function (f) {
      syncFilled(f);
      if (f.input === document.activeElement) f.el.classList.add("is-focused");

      f.input.addEventListener("focus", function () {
        if (!armed && isMobile()) {
          f.input.blur();
          return;
        }
        f.el.classList.add("is-focused");
        syncTyping();
      });
      f.input.addEventListener("blur", function () {
        f.el.classList.remove("is-focused");
        syncFilled(f);
        window.setTimeout(syncTyping, BLUR_GRACE_MS);
      });
      f.input.addEventListener("input", function () {
        syncFilled(f);
        f.el.classList.remove("is-invalid");
      });
      // Browser autofill fires `change` without `input` on some engines.
      f.input.addEventListener("change", function () { syncFilled(f); });
    });
    syncTyping();

    // Re-render after a failed sign-in keeps the username: go straight to the password.
    if (!isMobile() && password && !password.value && fields[0] && fields[0].input !== password && fields[0].input.value) {
      password.focus();
    }

    if (toggle && password) {
      // Keep focus (and the keyboard) on the password field.
      toggle.addEventListener("mousedown", function (e) { e.preventDefault(); });
      toggle.addEventListener("click", function () {
        var show = password.type === "password";
        password.type = show ? "text" : "password";
        toggle.setAttribute("aria-pressed", show ? "true" : "false");
        toggle.textContent = toggle.getAttribute(show ? "data-hide-label" : "data-show-label");
      });
    }

    if (caps && password) {
      var onKey = function (e) {
        if (typeof e.getModifierState !== "function") return;
        caps.hidden = !e.getModifierState("CapsLock");
      };
      password.addEventListener("keydown", onKey);
      password.addEventListener("keyup", onKey);
      password.addEventListener("blur", function () { caps.hidden = true; });
    }

    function shake(field) {
      field.classList.remove("is-invalid");
      void field.offsetWidth;
      field.classList.add("is-invalid");
    }

    function setLoading(on) {
      if (!submit) return;
      window.clearTimeout(loadingTimer);
      // An aborted navigation (Stop, flaky network) never unloads the page.
      if (on) loadingTimer = window.setTimeout(function () { setLoading(false); }, LOADING_RESET_MS);
      submit.classList.toggle("is-loading", on);
      submit.disabled = on;
      if (on) submit.setAttribute("aria-busy", "true");
      else submit.removeAttribute("aria-busy");
    }

    // Some browsers restore the disabled state on reload.
    setLoading(false);

    if (submit) {
      submit.addEventListener("pointerdown", function (e) {
        if (submit.disabled) return;
        var rect = submit.getBoundingClientRect();
        var size = Math.max(rect.width, rect.height);
        var ripple = document.createElement("span");
        ripple.className = "rd-login-ripple";
        ripple.style.width = ripple.style.height = size + "px";
        ripple.style.left = e.clientX - rect.left - size / 2 + "px";
        ripple.style.top = e.clientY - rect.top - size / 2 + "px";
        submit.appendChild(ripple);
        window.setTimeout(function () { ripple.remove(); }, 650);
      });
    }

    form.addEventListener("submit", function (e) {
      if (submit && submit.classList.contains("is-loading")) {
        e.preventDefault();
        return;
      }
      // Passwords may legitimately contain spaces; only the username is trimmed.
      var empty = fields.filter(function (f) {
        return f.input === password ? !f.input.value : !f.input.value.trim();
      });
      if (empty.length) {
        e.preventDefault();
        empty.forEach(function (f) { shake(f.el); });
        empty[0].input.focus();
        return;
      }
      setLoading(true);
    });

    // Back/forward cache restores the page with the spinner still on and can
    // leave the hero collapsed for a field that no longer has focus.
    window.addEventListener("pageshow", function (e) {
      if (e.persisted) setLoading(false);
      syncFromActive();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
