/**
 * SIM inventory row actions — fixed popover menu + modal forms.
 */
(function () {
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

  function qs(sel) {
    return dialog ? dialog.querySelector(sel) : null;
  }

  function csrfToken() {
    var el = document.querySelector("[name=csrfmiddlewaretoken]");
    return el ? el.value : "";
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
      dialog.hidden = true;
    }, 180);
  }

  function closePopover() {
    if (!popover) return;
    popover.hidden = true;
    popoverPanel.innerHTML = "";
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
    if (inputSetQty) inputSetQty.required = showSetQty;
    if (inputDelta) inputDelta.required = showAdjust;
    if (inputDamagedQty) inputDamagedQty.required = showDamagedQty;
    if (inputReason) inputReason.required = showReason;
  }

  function openAction(btn) {
    if (!form || !dialog) return;
    closePopover();
    var action = btn.getAttribute("data-inv-action") || "";
    var url = btn.getAttribute("data-inv-url") || "";
    var title = btn.getAttribute("data-inv-title") || "";
    var qty = btn.getAttribute("data-inv-qty") || "";
    var maxQty = btn.getAttribute("data-inv-max") || "";
    var next = btn.getAttribute("data-inv-next") || "";

    form.action = url;
    if (titleEl) titleEl.textContent = title;
    if (inputNext) inputNext.value = next;
    if (inputSetQty) inputSetQty.value = qty;
    if (inputDamagedQty) {
      inputDamagedQty.value = "1";
      if (maxQty) inputDamagedQty.max = maxQty;
      else inputDamagedQty.removeAttribute("max");
    }
    if (inputDelta) inputDelta.value = "";
    if (inputReason) inputReason.value = "";
    if (inputNotes) inputNotes.value = "";

    showFields(action);
    openDialog();

    if ((action === "set" || action === "edit") && inputSetQty) inputSetQty.focus();
    else if (action === "adjust" && inputDelta) inputDelta.focus();
    else if ((action === "damaged" || action === "manual_sale") && inputDamagedQty) inputDamagedQty.focus();
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
        '">' +
        label +
        "</button>"
      );
    }

    if (d.invEditUrl) {
      html += actionBtn("edit", d.invEditUrl, d.invTitleEdit || "", d.invLabelEdit || "");
    }
    if (d.invManualSaleUrl) {
      html += actionBtn("manual_sale", d.invManualSaleUrl, d.invTitleManualSale || "", d.invLabelManualSale || "");
    }
    if (d.invSetUrl) {
      html += actionBtn("set", d.invSetUrl, d.invTitleSet || "", d.invLabelSet || "");
    }
    if (d.invAdjustUrl) {
      html += actionBtn("adjust", d.invAdjustUrl, d.invTitleAdjust || "", d.invLabelAdjust || "");
    }
    if (d.invDamagedUrl) {
      html += actionBtn("damaged", d.invDamagedUrl, d.invTitleDamaged || "", d.invLabelDamaged || "");
    }
    if (d.invClearUrl) {
      html +=
        '<form method="post" action="' +
        d.invClearUrl +
        '" onsubmit="return confirm(\'' +
        (d.invConfirmClear || "") +
        "');\">" +
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
        '" onsubmit="return confirm(\'' +
        (d.invConfirmDelete || "") +
        "');\">" +
        '<input type="hidden" name="csrfmiddlewaretoken" value="' +
        token +
        '">' +
        (next ? '<input type="hidden" name="next" value="' + next + '">' : "") +
        '<button type="submit" class="rd-inv-actions-popover__item rd-inv-actions-popover__item--danger">' +
        (d.invLabelDelete || "") +
        "</button></form>";
    }

    popoverPanel.innerHTML = html;
  }

  function openPopover(toggle) {
    if (activeToggle === toggle && popover && !popover.hidden) {
      closePopover();
      return;
    }
    activeToggle = toggle;
    buildPopover(toggle);
    placePopover(toggle);
  }

  function init() {
    dialog = document.getElementById("rd-inv-action-dialog");
    popover = document.getElementById("rd-inv-actions-popover");
    popoverPanel = popover ? popover.querySelector(".rd-inv-actions-popover__panel") : null;

    if (dialog) {
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

      dialog.querySelectorAll("[data-inv-dialog-close]").forEach(function (el) {
        el.addEventListener("click", closeDialog);
      });

      dialog.addEventListener("click", function (e) {
        if (e.target.classList && e.target.classList.contains("rd-inv-dialog__backdrop")) {
          closeDialog();
        }
      });
    }

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

    document.body.addEventListener("click", function (e) {
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

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
