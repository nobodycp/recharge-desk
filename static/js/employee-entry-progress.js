/**
 * Sale entry: progress hairline, step dots and the "number looks valid" tick.
 *
 * Display only. It reads the form's current state and toggles classes, the
 * `hidden` attribute and one CSS variable; it never writes a field value and
 * never touches validation or submission.
 *
 * Five steps: number, payment, company, product, price. The hairline fills
 * done/5; the four numbered dots (data-step pay | co | pk | pr) get `is-done`.
 *
 * Bound to the form element (replaced on every visit), so each page visit and
 * each history restore binds afresh. init() runs when the script runs and again
 * from htmx.onLoad; the per-form marker makes the repeats cheap no-ops. Listeners are on the form, not on
 * document, so there is nothing to clean up when the page is left.
 */
(function () {
  var STEPS = 5;

  /* Same normalisation and pattern as the submit handler in employee_entry.html. */
  function numberLooksValid(raw) {
    var digits = String(raw || "").replace(/\D/g, "");
    if (digits.indexOf("972") === 0) digits = "0" + digits.slice(3);
    if (digits.length === 9 && digits.charAt(0) === "5") digits = "0" + digits;
    return /^05[0-5]\d{7}$/.test(digits);
  }

  function hasValue(id) {
    var el = document.getElementById(id);
    return !!(el && String(el.value || "").trim() !== "");
  }

  function paymentChosen() {
    return !!(
      document.querySelector(".employee-payment-tile.is-active") ||
      hasValue("id_on_account") ||
      hasValue("id_paid_via_employee") ||
      hasValue("id_payment_method")
    );
  }

  function companyChosen() {
    return !!(document.querySelector(".employee-company-tile.is-active") || hasValue("id_company"));
  }

  function update(form) {
    if (!form.isConnected) return;
    var refInput = document.getElementById("id_reference_number");
    var valid = !!refInput && numberLooksValid(refInput.value);
    var done = {
      pay: paymentChosen(),
      co: companyChosen(),
      pk: hasValue("id_product"),
      pr: hasValue("id_sell_price_actual"),
    };

    var count = valid ? 1 : 0;
    Object.keys(done).forEach(function (key) {
      if (done[key]) count++;
      document.querySelectorAll('.rd-entry-step[data-step="' + key + '"]').forEach(function (dot) {
        dot.classList.toggle("is-done", done[key]);
      });
    });

    var bar = document.querySelector(".rd-entry-progress");
    if (bar) bar.style.setProperty("--rd-entry-progress", String(count / STEPS));

    /* The tick is an SVG: `hidden` is an attribute there, not a property. */
    var tick = form.querySelector(".rd-entry-valid");
    if (tick) tick.toggleAttribute("hidden", !valid);
  }

  /* Page handlers (tile clicks, pills, product swap) run after ours in the
     bubble order, so read the state on the next tick. */
  function later(form) {
    window.setTimeout(function () {
      update(form);
    }, 0);
  }

  function init() {
    var form = document.getElementById("employee-sale-form");
    if (!form) return;
    if (!form.__rdEntryProgress) {
      form.__rdEntryProgress = true;
      form.addEventListener("input", function () { later(form); });
      form.addEventListener("change", function () { later(form); });
      form.addEventListener("click", function () { later(form); });
      form.addEventListener("htmx:afterSwap", function (evt) {
        var target = evt.detail && evt.detail.target;
        if (target && target.id === "product-tiles-container") later(form);
      });
    }
    later(form);
  }

  /* Run now: the form is above this script. After a boosted visit the script
     is re-inserted and loads after the body's htmx:load already fired, so
     onLoad alone would miss it. The per-form marker keeps this idempotent. */
  init();
  /* Later htmx loads (product partial swap, another boosted visit). */
  if (window.htmx) {
    window.htmx.onLoad(init);
  } else {
    document.addEventListener("DOMContentLoaded", init);
  }
})();
