/**
 * Admin screens: unsaved-changes bar, image drop zones with live preview,
 * file pickers, confirm-gated forms, text bindings for the branding preview,
 * clickable table rows, and instant feedback on the dashboard period switch.
 */
(function () {
  function snapshot(form) {
    var parts = [];
    Array.prototype.forEach.call(form.elements, function (el) {
      if (!el.name || el.type === "hidden" || el.type === "submit") return;
      if (el.type === "checkbox" || el.type === "radio") {
        parts.push(el.name + "=" + el.checked);
      } else if (el.type === "file") {
        parts.push(el.name + "=" + (el.files && el.files.length ? el.files[0].name : ""));
      } else {
        parts.push(el.name + "=" + el.value);
      }
    });
    return parts.join("&");
  }

  // The leave-page prompt itself lives in nav.js and keys off .is-dirty.
  // The bar is always visible: calm ("no unsaved changes", Save and Discard disabled)
  // until the form differs from its initial values, then dirty with both enabled.
  function initDirtyForm(form) {
    var bar = form.querySelector("[data-rd-savebar]");
    if (!bar) return;
    var msg = bar.querySelector("[data-rd-savebar-msg]");
    var save = bar.querySelector("[data-rd-savebar-save]");
    var discard = bar.querySelector("[data-rd-savebar-discard]");
    var initial = snapshot(form);
    var forced = bar.hasAttribute("data-rd-savebar-force");

    function refresh() {
      var dirty = forced || snapshot(form) !== initial;
      bar.classList.toggle("is-dirty", dirty);
      form.classList.toggle("is-dirty", dirty);
      if (save) save.disabled = !dirty;
      if (discard) discard.disabled = !dirty;
      if (msg) {
        var text = msg.getAttribute(dirty ? "data-rd-msg-dirty" : "data-rd-msg-calm");
        if (text) msg.textContent = text;
      }
    }

    form.addEventListener("input", refresh);
    form.addEventListener("change", refresh);
    form.addEventListener("submit", function () {
      form.setAttribute("data-rd-submitting", "");
    });
    form.addEventListener("reset", function () {
      // Discard goes back to the form's own values. On a server-error page those values
      // are the invalid ones, so `forced` stays: never "No unsaved changes" beside errors.
      setTimeout(function () {
        form.dispatchEvent(new CustomEvent("rd:reset"));
        refresh();
      }, 0);
    });
    refresh();
  }

  // The dark copy of a saved logo keeps its own file until a new one is picked.
  function variantSrc(img, src) {
    var dark = img.getAttribute("data-rd-dark-src");
    return dark && src === img.getAttribute("data-rd-light-src") ? dark : src;
  }

  function setPreviewImages(key, src) {
    document.querySelectorAll('[data-rd-preview-img="' + key + '"]').forEach(function (img) {
      if (src) {
        img.src = variantSrc(img, src);
        img.hidden = false;
      } else {
        img.hidden = true;
      }
      var host = img.closest("[data-rd-preview-host]");
      if (host) host.classList.toggle("has-image", !!src);
    });
  }

  function initDrop(zone) {
    var input = zone.querySelector('input[type="file"]');
    var key = zone.getAttribute("data-rd-drop");
    var clear = zone.parentElement.querySelector("[data-rd-drop-clear]");
    var original = zone.getAttribute("data-rd-current") || "";
    if (!input) return;

    function show(src) {
      zone.classList.toggle("has-image", !!src);
      zone.querySelectorAll("[data-rd-drop-thumb]").forEach(function (thumb) {
        if (src) thumb.src = variantSrc(thumb, src);
        thumb.hidden = !src;
      });
      setPreviewImages(key, src);
    }

    input.addEventListener("change", function () {
      var file = input.files && input.files[0];
      if (!file) {
        show(clear && clear.checked ? "" : original);
        return;
      }
      if (clear) clear.checked = false;
      var reader = new FileReader();
      reader.onload = function () {
        show(reader.result);
      };
      reader.readAsDataURL(file);
    });

    if (clear) {
      clear.addEventListener("change", function () {
        if (clear.checked) {
          input.value = "";
          show("");
        } else {
          show(original);
        }
      });
    }

    ["dragenter", "dragover"].forEach(function (type) {
      zone.addEventListener(type, function (e) {
        e.preventDefault();
        zone.classList.add("is-dragging");
      });
    });
    ["dragleave", "drop"].forEach(function (type) {
      zone.addEventListener(type, function (e) {
        e.preventDefault();
        zone.classList.remove("is-dragging");
      });
    });
    zone.addEventListener("drop", function (e) {
      if (!e.dataTransfer || !e.dataTransfer.files.length) return;
      input.files = e.dataTransfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });

    var form = zone.closest("form");
    if (form) {
      form.addEventListener("rd:reset", function () {
        show(original);
      });
    }
  }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1048576).toFixed(1) + " MB";
  }

  function initFilePick(zone) {
    var input = zone.querySelector('input[type="file"]');
    var name = zone.querySelector("[data-rd-file-name]");
    if (!input || !name) return;
    var placeholder = name.textContent;

    function show() {
      var file = input.files && input.files[0];
      zone.classList.toggle("has-file", !!file);
      name.textContent = file ? file.name + " · " + formatSize(file.size) : placeholder;
    }

    input.addEventListener("change", show);
    ["dragenter", "dragover"].forEach(function (type) {
      zone.addEventListener(type, function (e) {
        e.preventDefault();
        zone.classList.add("is-dragging");
      });
    });
    ["dragleave", "drop"].forEach(function (type) {
      zone.addEventListener(type, function (e) {
        e.preventDefault();
        zone.classList.remove("is-dragging");
      });
    });
    zone.addEventListener("drop", function (e) {
      if (!e.dataTransfer || !e.dataTransfer.files.length) return;
      input.files = e.dataTransfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }

  function initConfirmGate(form) {
    var word = (form.getAttribute("data-rd-confirm-gate") || "").toUpperCase();
    var submit = form.querySelector('[type="submit"]');
    if (!submit) return;
    function refresh() {
      var ok = Array.prototype.every.call(form.elements, function (el) {
        if (!el.name || el.type === "hidden" || el.type === "submit") return true;
        if (el.type === "file") return !!(el.files && el.files.length);
        if (el.type === "checkbox") return el.checked;
        if (el.hasAttribute("data-rd-confirm-input")) return el.value.trim().toUpperCase() === word;
        return true;
      });
      submit.disabled = !ok;
    }
    form.addEventListener("input", refresh);
    form.addEventListener("change", refresh);
    refresh();
  }

  function initBinding(input) {
    var key = input.getAttribute("data-rd-bind");
    var fallback = input.getAttribute("data-rd-fallback") || "";
    function apply() {
      var text = input.value.trim() || fallback;
      document.querySelectorAll('[data-rd-bound="' + key + '"]').forEach(function (el) {
        el.textContent = text;
      });
      document.querySelectorAll('[data-rd-bound-first="' + key + '"]').forEach(function (el) {
        el.textContent = Array.from(text)[0] || "";
      });
    }
    input.addEventListener("input", apply);
    var form = input.closest("form");
    if (form) form.addEventListener("rd:reset", apply);
  }

  document.addEventListener("click", function (e) {
    var row = e.target.closest("[data-rd-href]");
    if (!row) return;
    if (e.target.closest("a, button, input, select, textarea, label, form, summary")) return;
    var href = row.getAttribute("data-rd-href");
    if (!href) return;
    if (e.metaKey || e.ctrlKey) {
      window.open(href, "_blank");
    } else if (window.rdNavigate) {
      window.rdNavigate(href);
    } else {
      window.location.href = href;
    }
  });

  document.addEventListener("htmx:beforeRequest", function (e) {
    var opt = e.target.closest && e.target.closest(".rd-period-switch__opt");
    if (!opt) return;
    opt.parentElement.querySelectorAll(".rd-period-switch__opt").forEach(function (o) {
      o.classList.toggle("is-active", o === opt);
    });
  });

  function each(root, selector, fn) {
    if (!root.querySelectorAll) return;
    var found = Array.prototype.slice.call(root.querySelectorAll(selector));
    if (root.matches && root.matches(selector)) found.unshift(root);
    found.forEach(function (el) {
      if (el.__rdAdminUi) return;
      el.__rdAdminUi = true;
      fn(el);
    });
  }

  function init(root) {
    each(root, "form[data-rd-dirty-form]", initDirtyForm);
    each(root, "[data-rd-drop]", initDrop);
    each(root, "[data-rd-bind]", initBinding);
    each(root, "[data-rd-file-pick]", initFilePick);
    each(root, "form[data-rd-confirm-gate]", initConfirmGate);
  }

  if (window.htmx) {
    window.htmx.onLoad(init);
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      init(document);
    });
  } else {
    init(document);
  }
})();
