/**
 * Optional SIM serial paste dialog — syncs to hidden form textarea on input/paste.
 */
(function () {
  var OPEN_CLASS = "is-open";
  var BODY_CLASS = "rd-inv-dialog-open";
  var MISMATCH_CLASS = "is-mismatch";

  function parseSerialList(text) {
    if (!text || !String(text).trim()) return [];
    var serials = [];
    String(text)
      .replace(/,/g, "\n")
      .split("\n")
      .forEach(function (raw) {
        var value = raw.trim();
        if (value) serials.push(value);
      });
    return serials;
  }

  function normalizeText(serials) {
    return serials.length ? serials.join("\n") : "";
  }

  function initRoot(root) {
    var hiddenInput = root.querySelector(".rd-inv-serials-input");
    var trigger = root.querySelector("[data-rd-inv-serials-open]");
    var countEl = root.querySelector("[data-rd-inv-serials-count]");
    var dialog = root.querySelector("[data-rd-inv-serials-dialog]");
    var pasteArea = root.querySelector("[data-rd-inv-serials-paste]");
    var mismatchEl = root.querySelector("[data-rd-inv-serials-mismatch]");
    var qtySelector = root.getAttribute("data-rd-inv-qty-input") || "";
    var qtyInput = qtySelector ? document.querySelector(qtySelector) : null;

    if (!hiddenInput || !trigger || !dialog || !pasteArea) return;

    function readQty() {
      if (!qtyInput) return null;
      var n = parseInt(qtyInput.value, 10);
      return Number.isFinite(n) && n > 0 ? n : null;
    }

    function updateUi() {
      var serials = parseSerialList(hiddenInput.value);
      var count = serials.length;

      if (countEl) {
        if (count > 0) {
          countEl.textContent = String(count);
          countEl.hidden = false;
        } else {
          countEl.textContent = "";
          countEl.hidden = true;
        }
      }

      var qty = readQty();
      var mismatch = count > 0 && qty !== null && count !== qty;
      root.classList.toggle(MISMATCH_CLASS, mismatch);
      if (mismatchEl) mismatchEl.hidden = !mismatch;
    }

    function syncFromPaste() {
      var serials = parseSerialList(pasteArea.value);
      var normalized = normalizeText(serials);
      hiddenInput.value = normalized;
      pasteArea.value = normalized;
      updateUi();
    }

    function openDialog() {
      pasteArea.value = hiddenInput.value || "";
      dialog.hidden = false;
      dialog.setAttribute("aria-hidden", "false");
      document.body.classList.add(BODY_CLASS);
      requestAnimationFrame(function () {
        dialog.classList.add(OPEN_CLASS);
        pasteArea.focus();
      });
    }

    function closeDialog() {
      if (dialog.hidden) return;
      dialog.classList.remove(OPEN_CLASS);
      document.body.classList.remove(BODY_CLASS);
      dialog.setAttribute("aria-hidden", "true");
      setTimeout(function () {
        dialog.hidden = true;
      }, 180);
    }

    function isOpen() {
      return dialog && !dialog.hidden;
    }

    trigger.addEventListener("click", function () {
      openDialog();
    });

    dialog.querySelectorAll("[data-rd-inv-serials-close]").forEach(function (el) {
      el.addEventListener("click", closeDialog);
    });

    dialog.addEventListener("click", function (e) {
      if (e.target.classList && e.target.classList.contains("rd-inv-dialog__backdrop")) {
        closeDialog();
      }
    });

    pasteArea.addEventListener("input", syncFromPaste);
    pasteArea.addEventListener("paste", function () {
      window.setTimeout(syncFromPaste, 0);
    });

    if (qtyInput) {
      qtyInput.addEventListener("input", updateUi);
      qtyInput.addEventListener("change", updateUi);
    }

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && isOpen()) {
        e.stopPropagation();
        closeDialog();
      }
    });

    hiddenInput.value = normalizeText(parseSerialList(hiddenInput.value));
    updateUi();
  }

  function init() {
    document.querySelectorAll("[data-rd-inv-serials-root]").forEach(initRoot);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
