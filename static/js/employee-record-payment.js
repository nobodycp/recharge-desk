(function () {
  var root = document.getElementById("rd-pay-sub-page");
  if (!root) return;

  var lookupUrl = root.getAttribute("data-lookup-url") || "";
  var searchInp = document.getElementById("rd-pay-sub-search");
  var acBox = document.getElementById("rd-pay-sub-ac");
  var custHid = document.getElementById("id_pay_sub_customer");
  var balLine = document.getElementById("rd-pay-sub-balance-line");
  var balVal = document.getElementById("rd-pay-sub-balance-val");
  var pmHid = document.getElementById("id_pay_sub_payment_method");
  var empPayHid = document.getElementById("id_pay_sub_paid_via_employee");
  var empPayBtn = document.getElementById("rd-pay-sub-employee-toggle");
  var empPayHint = document.getElementById("rd-pay-sub-employee-hint");
  var payTiles = document.getElementById("rd-pay-sub-payment-tiles");
  var payNoteMethod = document.getElementById("rd-pay-sub-note-method");
  var payNoteEmployee = document.getElementById("rd-pay-sub-note-employee");
  var tmr;

  function isPaySubViaEmployee() {
    return !!(empPayHid && empPayHid.value);
  }
  function syncPaySubNote(viaEmployee) {
    if (payNoteMethod) payNoteMethod.hidden = !!viaEmployee;
    if (payNoteEmployee) payNoteEmployee.hidden = !viaEmployee;
  }
  function setPaySubViaEmployee(enabled) {
    if (!empPayHid || !empPayBtn) return;
    if (enabled) {
      empPayHid.value = "1";
      empPayBtn.classList.remove("btn-outline-info");
      empPayBtn.classList.add("btn-info");
      empPayBtn.setAttribute("aria-pressed", "true");
      empPayBtn.textContent = empPayBtn.getAttribute("data-on-label") || "ON";
      if (payTiles) payTiles.classList.add("opacity-50");
      if (pmHid) pmHid.value = "";
      document.querySelectorAll("#rd-pay-sub-payment-tiles .rd-pay-sub-pm-tile").forEach(function (t) {
        t.classList.remove("is-active");
      });
      if (empPayHint) empPayHint.hidden = false;
      syncPaySubNote(true);
    } else {
      empPayHid.value = "";
      empPayBtn.classList.remove("btn-info");
      empPayBtn.classList.add("btn-outline-info");
      empPayBtn.setAttribute("aria-pressed", "false");
      empPayBtn.textContent = empPayBtn.getAttribute("data-off-label") || "Employee";
      if (payTiles) payTiles.classList.remove("opacity-50");
      if (empPayHint) empPayHint.hidden = true;
      syncPaySubNote(false);
    }
  }
  function pickCustomer(id, name, balance) {
    if (custHid) custHid.value = String(id);
    if (searchInp) searchInp.value = name;
    if (acBox) acBox.hidden = true;
    if (balLine) balLine.hidden = false;
    if (balVal) balVal.textContent = balance;
  }
  function renderResults(items) {
    if (!acBox) return;
    acBox.innerHTML = "";
    items.forEach(function (it) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "list-group-item list-group-item-action py-2";
      btn.setAttribute("role", "option");
      btn.textContent = it.name + " · " + it.balance;
      btn.addEventListener("click", function () {
        pickCustomer(it.id, it.name, it.balance);
      });
      acBox.appendChild(btn);
    });
    acBox.hidden = !items.length;
  }
  if (searchInp) {
    searchInp.addEventListener("input", function () {
      var q = String(searchInp.value || "").trim();
      if (custHid) custHid.value = "";
      if (balLine) balLine.hidden = true;
      if (tmr) window.clearTimeout(tmr);
      if (q.length < 2 || !lookupUrl) {
        if (acBox) {
          acBox.innerHTML = "";
          acBox.hidden = true;
        }
        return;
      }
      tmr = window.setTimeout(function () {
        var url = lookupUrl + (lookupUrl.indexOf("?") >= 0 ? "&" : "?") + "q=" + encodeURIComponent(q);
        fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
          .then(function (r) {
            return r.ok ? r.json() : { results: [] };
          })
          .then(function (data) {
            renderResults((data && data.results) || []);
          })
          .catch(function () {
            if (acBox) {
              acBox.innerHTML = "";
              acBox.hidden = true;
            }
          });
      }, 180);
    });
  }
  document.querySelectorAll("#rd-pay-sub-payment-tiles .rd-pay-sub-pm-tile").forEach(function (tile) {
    tile.addEventListener("click", function () {
      if (isPaySubViaEmployee()) setPaySubViaEmployee(false);
      document.querySelectorAll("#rd-pay-sub-payment-tiles .rd-pay-sub-pm-tile").forEach(function (t) {
        t.classList.remove("is-active");
      });
      tile.classList.add("is-active");
      if (pmHid) pmHid.value = tile.getAttribute("data-payment-id") || "";
    });
  });
  if (empPayBtn) {
    empPayBtn.addEventListener("click", function () {
      setPaySubViaEmployee(!isPaySubViaEmployee());
    });
  }
  // Sync notes/hint on first paint (edit page may load with employee payment ON).
  syncPaySubNote(isPaySubViaEmployee());
  var subForm = document.getElementById("rd-pay-sub-form");
  if (subForm) {
    subForm.addEventListener("submit", function (e) {
      if (!custHid || !String(custHid.value || "").trim()) {
        e.preventDefault();
        window.alert(root.getAttribute("data-msg-customer") || "");
        return;
      }
      if (!isPaySubViaEmployee() && (!pmHid || !String(pmHid.value || "").trim())) {
        e.preventDefault();
        window.alert(root.getAttribute("data-msg-payment") || "");
      }
    });
  }
  document.addEventListener("click", function (e) {
    if (!acBox || acBox.hidden) return;
    if (root.contains(e.target)) return;
    acBox.hidden = true;
  });
})();
