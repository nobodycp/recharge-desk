/**
 * SIM inventory row actions — fixed popover menu + modal forms.
 *
 * Re-wires the dialog after every boosted navigation (htmx.onLoad): the shell
 * keeps this script's closure alive while <body>'s content (and the dialog)
 * is replaced. Click delegation is bound to `document` (never swapped), not
 * `document.body` (its content is swapped) — see bindDocument(). The actual
 * htmx.onLoad(init) registration is deferred (see the bottom of the file) so
 * page-scope.js doesn't tear it — and everything it transitively registers,
 * including the click delegate — down after the first navigation.
 *
 * This <script src> tag is itself part of the swapped content (main_stock.html
 * / customer_detail.html ship it conditionally), so htmx re-inserts — and
 * therefore re-executes — it on every boosted navigation back to one of these
 * pages. Without the guard below, each swap would spin up a brand new,
 * independent copy of all the module state (dialog/popover/activeToggle) and
 * register a second full set of document-level listeners that never gets
 * torn down, so two (or more) copies would race on every click afterward —
 * the open/close toggle desyncs between them, which is what made every row
 * action look "stuck" until a hard refresh. Only the very first execution
 * matters; htmx.onLoad(init) below already re-wires that one instance's
 * dialog/popover element references on every later swap.
 */
(function () {
  if (window.__rdInventoryActionsLoaded) return;
  window.__rdInventoryActionsLoaded = true;

  var dialog = null;
  var form = null;
  var titleEl = null;
  var popover = null;
  var popoverPanel = null;
  var activeToggle = null;

  var fieldSetQty = null;
  var fieldDelta = null;
  var fieldDamagedQty = null;
  var fieldReason = null;
  var fieldNotes = null;
  var inputSetQty = null;
  var inputDelta = null;
  var inputDamagedQty = null;
  var inputReason = null;
  var inputNotes = null;
  var inputNext = null;
  var documentBound = false;

  function qs(sel) {
    return dialog ? dialog.querySelector(sel) : null;
  }

  function csrfToken() {
    var el = document.querySelector("[name=csrfmiddlewaretoken]");
    return el ? el.value : "";
  }

  function setFieldActive(input, on, required) {
    if (!input) return;
    input.disabled = !on;
    input.required = !!(on && required);
    if (!on) input.value = "";
  }

  function wireUi() {
    dialog = document.getElementById("rd-inv-action-dialog");
    popover = document.getElementById("rd-inv-actions-popover");
    popoverPanel = popover ? popover.querySelector(".rd-inv-actions-popover__panel") : null;
    form = null;
    titleEl = null;
    fieldSetQty = fieldDelta = fieldDamagedQty = fieldReason = fieldNotes = null;
    inputSetQty = inputDelta = inputDamagedQty = inputReason = inputNotes = inputNext = null;
    activeToggle = null;

    if (!dialog) return;

    form = dialog.querySelector("#rd-inv-action-form");
    titleEl = dialog.querySelector("[data-inv-dialog-title]");
    fieldSetQty = qs('[data-inv-field="set-qty"]');
    fieldDelta = qs('[data-inv-field="delta"]');
    fieldDamagedQty = qs('[data-inv-field="damaged-qty"]');
    fieldReason = qs('[data-inv-field="reason"]');
    fieldNotes = qs('[data-inv-field="notes"]');
    inputSetQty = dialog.querySelector("#rd-inv-set-qty");
    inputDelta = dialog.querySelector("#rd-inv-delta");
    inputDamagedQty = dialog.querySelector("#rd-inv-damaged-qty");
    inputReason = dialog.querySelector("#rd-inv-reason");
    inputNotes = dialog.querySelector("#rd-inv-notes");
    inputNext = dialog.querySelector("#rd-inv-action-next");

    // wireUi() re-runs on every openAction()/openPopover() call, not only
    // after a swap — guard so repeated opens on the same (unswapped) dialog
    // node don't pile up duplicate close listeners.
    if (!dialog.dataset.invWired) {
      dialog.dataset.invWired = "1";
      dialog.querySelectorAll("[data-inv-dialog-close]").forEach(function (el) {
        el.addEventListener("click", closeDialog);
      });

      dialog.addEventListener("click", function (e) {
        if (e.target.classList && e.target.classList.contains("rd-inv-dialog__backdrop")) {
          closeDialog();
        }
      });
    }
  }

  function openDialog() {
    if (!dialog) return;
    dialog.hidden = false;
    document.body.classList.add("rd-inv-dialog-open");
    requestAnimationFrame(function () {
      dialog.classList.add("is-open");
    });
  }

  function closeDialog() {
    if (!dialog) return;
    dialog.classList.remove("is-open");
    document.body.classList.remove("rd-inv-dialog-open");
    setTimeout(function () {
      if (dialog) dialog.hidden = true;
    }, 180);
  }

  function closePopover() {
    if (!popover) return;
    popover.hidden = true;
    if (popoverPanel) popoverPanel.innerHTML = "";
    activeToggle = null;
  }

  function showFields(action) {
    var showSetQty = action === "set" || action === "edit";
    var showAdjust = action === "adjust";
    var showDamagedQty = action === "damaged" || action === "manual_sale";
    var showReason = action === "set" || action === "adjust";
    var showNotes = action === "damaged" || action === "manual_sale" || action === "edit";
    if (fieldSetQty) fieldSetQty.hidden = !showSetQty;
    if (fieldDelta) fieldDelta.hidden = !showAdjust;
    if (fieldDamagedQty) fieldDamagedQty.hidden = !showDamagedQty;
    if (fieldReason) fieldReason.hidden = !showReason;
    if (fieldNotes) fieldNotes.hidden = !showNotes;
    // Disable unused inputs so they are not validated or posted (two fields
    // share name="quantity"; a stale hidden value would steal the submit).
    setFieldActive(inputSetQty, showSetQty, true);
    setFieldActive(inputDelta, showAdjust, true);
    setFieldActive(inputDamagedQty, showDamagedQty, true);
    setFieldActive(inputReason, showReason, true);
    setFieldActive(inputNotes, showNotes, false);
  }

  function openAction(btn) {
    wireUi();
    if (!form || !dialog) return;
    closePopover();
    var action = btn.getAttribute("data-inv-action") || "";
    var url = btn.getAttribute("data-inv-url") || "";
    var title = btn.getAttribute("data-inv-title") || "";
    var qty = btn.getAttribute("data-inv-qty") || "";
    var maxQty = btn.getAttribute("data-inv-max") || "";
    var next = btn.getAttribute("data-inv-next") || "";

    // max="0" makes the browser block submit with no toast — refuse up front.
    if (action === "manual_sale" && maxQty !== "" && Number(maxQty) < 1) {
      if (window.rdToast) {
        window.rdToast(
          btn.getAttribute("data-inv-error-no-stock") ||
            "No customer stock to sell.",
          "error"
        );
      }
      return;
    }

    showFields(action);

    form.action = url;
    // hx-boost reads `action` once when it first wires an element's submit
    // listener, then reuses that path forever — changing `.action` here is
    // invisible to it until the element is reprocessed. Without this, every
    // dialog submission (any row, any action) would silently POST to
    // whatever path was captured at the last page load/swap instead of the
    // endpoint the user actually picked.
    if (window.htmx) window.htmx.process(form);
    if (titleEl) titleEl.textContent = title;
    if (inputNext) inputNext.value = next;
    if (inputSetQty && !inputSetQty.disabled) inputSetQty.value = qty;
    if (inputDamagedQty && !inputDamagedQty.disabled) {
      inputDamagedQty.value = "1";
      if (maxQty !== "") inputDamagedQty.max = maxQty;
      else inputDamagedQty.removeAttribute("max");
    }
    if (inputDelta && !inputDelta.disabled) inputDelta.value = "";
    if (inputReason && !inputReason.disabled) inputReason.value = "";
    if (inputNotes && !inputNotes.disabled) inputNotes.value = "";

    openDialog();

    if ((action === "set" || action === "edit") && inputSetQty) inputSetQty.focus();
    else if (action === "adjust" && inputDelta) inputDelta.focus();
    else if ((action === "damaged" || action === "manual_sale") && inputDamagedQty) {
      inputDamagedQty.focus();
    }
  }

  function placePopover(toggle) {
    if (!popover || !popoverPanel) return;
    var r = toggle.getBoundingClientRect();
    var margin = 6;
    popover.hidden = false;
    requestAnimationFrame(function () {
      var pw = popoverPanel.offsetWidth || 168;
      var ph = popoverPanel.offsetHeight || 120;
      var isRtl = getComputedStyle(document.documentElement).direction === "rtl";
      var left = isRtl ? r.left : r.right - pw;
      left = Math.max(margin, Math.min(left, window.innerWidth - pw - margin));
      var top = r.bottom + margin;
      if (top + ph > window.innerHeight - margin && r.top - ph - margin > margin) {
        top = r.top - ph - margin;
      }
      popover.style.top = top + "px";
      popover.style.left = left + "px";
    });
  }

  function buildPopover(toggle) {
    var d = toggle.dataset;
    var next = d.invNext || "";
    var qty = d.invQty || "";
    var maxQty = d.invMax || "";
    var token = csrfToken();
    var html = "";

    function actionBtn(action, url, title, label) {
      var extra =
        action === "manual_sale" && d.invErrorNoStock
          ? ' data-inv-error-no-stock="' + escapeAttr(d.invErrorNoStock) + '"'
          : "";
      return (
        '<button type="button" class="rd-inv-actions-popover__item rd-inv-action-btn" data-inv-action="' +
        action +
        '" data-inv-url="' +
        url +
        '" data-inv-title="' +
        title +
        '" data-inv-qty="' +
        qty +
        '" data-inv-max="' +
        maxQty +
        '" data-inv-next="' +
        next +
        '"' +
        extra +
        ">" +
        label +
        "</button>"
      );
    }

    if (d.invEditUrl) {
      html += actionBtn("edit", d.invEditUrl, d.invTitleEdit || "", d.invLabelEdit || "");
    }
    if (d.invManualSaleUrl) {
      html += actionBtn(
        "manual_sale",
        d.invManualSaleUrl,
        d.invTitleManualSale || "",
        d.invLabelManualSale || ""
      );
    }
    if (d.invSetUrl) {
      html += actionBtn("set", d.invSetUrl, d.invTitleSet || "", d.invLabelSet || "");
    }
    if (d.invAdjustUrl) {
      html += actionBtn("adjust", d.invAdjustUrl, d.invTitleAdjust || "", d.invLabelAdjust || "");
    }
    if (d.invDamagedUrl) {
      html += actionBtn(
        "damaged",
        d.invDamagedUrl,
        d.invTitleDamaged || "",
        d.invLabelDamaged || ""
      );
    }
    if (d.invClearUrl) {
      html +=
        '<form method="post" action="' +
        d.invClearUrl +
        '" hx-confirm="' +
        escapeAttr(d.invConfirmClear || "") +
        '">' +
        '<input type="hidden" name="csrfmiddlewaretoken" value="' +
        token +
        '">' +
        (next ? '<input type="hidden" name="next" value="' + next + '">' : "") +
        '<input type="hidden" name="reason" value="' +
        (d.invClearReason || "") +
        '">' +
        '<button type="submit" class="rd-inv-actions-popover__item">' +
        (d.invLabelClear || "") +
        "</button></form>";
    }
    if (d.invDeleteUrl) {
      html +=
        '<form method="post" action="' +
        d.invDeleteUrl +
        '" hx-confirm="' +
        escapeAttr(d.invConfirmDelete || "") +
        '">' +
        '<input type="hidden" name="csrfmiddlewaretoken" value="' +
        token +
        '">' +
        (next ? '<input type="hidden" name="next" value="' + next + '">' : "") +
        '<button type="submit" class="rd-inv-actions-popover__item rd-inv-actions-popover__item--danger">' +
        (d.invLabelDelete || "") +
        "</button></form>";
    }

    popoverPanel.innerHTML = html;
    if (window.htmx) window.htmx.process(popoverPanel);
  }

  function escapeAttr(s) {
    return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
  }

  function openPopover(toggle) {
    wireUi();
    if (!popover || !popoverPanel) return;
    if (activeToggle === toggle && !popover.hidden) {
      closePopover();
      return;
    }
    activeToggle = toggle;
    buildPopover(toggle);
    placePopover(toggle);
  }

  function bindDocument() {
    if (documentBound) return;
    documentBound = true;

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        if (dialog && !dialog.hidden) closeDialog();
        closePopover();
      }
    });

    document.addEventListener(
      "scroll",
      function () {
        closePopover();
      },
      true
    );

    window.addEventListener("resize", closePopover);

    // Bind to `document`, not `document.body`: a boosted swap can leave the
    // body-bound delegate unreachable (every row action button goes dead
    // until a hard refresh), while `document` itself is never replaced.
    document.addEventListener("click", function (e) {
      var toggle = e.target.closest(".rd-inv-actions-toggle");
      if (toggle) {
        e.preventDefault();
        e.stopPropagation();
        openPopover(toggle);
        return;
      }

      var actionBtn = e.target.closest(".rd-inv-action-btn");
      if (actionBtn) {
        e.preventDefault();
        openAction(actionBtn);
        return;
      }

      if (popover && !popover.hidden) {
        if (popover.contains(e.target)) return;
        if (activeToggle && activeToggle.contains(e.target)) return;
        closePopover();
      }
    });
  }

  function init(root) {
    var scope = root && root.querySelector ? root : document;
    if (scope === document || (scope.querySelector && scope.querySelector("#rd-inv-action-dialog"))) {
      wireUi();
    } else if (document.getElementById("rd-inv-action-dialog")) {
      // Partial swap (customer filter) — keep dialog wiring, refresh popover handle.
      wireUi();
    }
    bindDocument();
  }

  // page-scope.js tears down anything a page (body-resident) script
  // registers — synchronously, or transitively from inside one of its own
  // callbacks — right before the next boosted swap, so a plain page is
  // never left leaking listeners. This script is shipped conditionally
  // (only on pages that need it), so by that rule it IS a page script, but
  // its htmx.onLoad(init) re-wiring hook — and the click delegate init()
  // installs the first time it runs — must survive every navigation, not
  // just the one that follows the first page load. Deferring past the
  // current task means `document.currentScript` is already null (and we're
  // not nested inside any tracked call) when this actually registers, so
  // page-scope.js treats it as shell-owned and leaves it alone.
  setTimeout(function () {
    if (window.htmx && window.htmx.onLoad) {
      window.htmx.onLoad(init);
    } else if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () {
        init(document.body);
      });
    } else {
      init(document.body);
    }
  }, 0);
})();
