/**
 * Employee sale form: payer name from reference_number lookup + autocomplete.
 * Depends on #rd-payer-assist-root data-* URLs (same-origin session auth).
 */
(function () {
  var DEBOUNCE_REF_MS = 380;
  var DEBOUNCE_NAME_MS = 280;
  var MIN_REF_LEN = 3;
  var MIN_NAME_QUERY = 2;

  function init() {
    var root = document.getElementById("rd-payer-assist-root");
    var refInput = document.getElementById("id_reference_number");
    var payerInput = document.getElementById("id_payer_name");
    if (!root || !refInput || !payerInput) return;

    var apiRef = root.dataset.apiPayerByNumber;
    var apiSuggest = root.dataset.apiPayerNameSuggestions;
    var hintText = root.dataset.hintPrefilled || "";
    var usedLabel = root.dataset.labelUsed || "";

    var refTimer = null;
    var nameTimer = null;
    var lastNLRef;
    var lastNLName;
    var refReqId = 0;
    var nameReqId = 0;
    var lastRefAbort = null;
    var lastNameAbort = null;

    var hintEl = root.querySelector(".rd-payer-prefill-hint");
    var listEl = root.querySelector(".rd-payer-ac-list");
    var suggestions = [];
    var activeIdx = -1;
    var listOpen = false;

    function setHint(visible) {
      if (!hintEl) return;
      hintEl.textContent = visible ? hintText : "";
      hintEl.classList.toggle("is-visible", visible);
      hintEl.hidden = !visible;
    }

    function shouldApplyNumberLookup(dTrim, n) {
      if (!n) return false;
      var p = payerInput.value.trim();
      var refChanged = lastNLRef === undefined || dTrim !== lastNLRef;
      if (refChanged) return true;
      if (p === "") return true;
      if (lastNLName !== undefined && p === lastNLName) return true;
      return false;
    }

    function closeList() {
      listOpen = false;
      activeIdx = -1;
      if (!listEl) return;
      listEl.innerHTML = "";
      listEl.hidden = true;
    }

    function updateActiveHighlight() {
      if (!listEl) return;
      var btns = listEl.querySelectorAll(".rd-payer-ac-item");
      btns.forEach(function (b, j) {
        b.classList.toggle("is-active", j === activeIdx);
        b.setAttribute("aria-selected", j === activeIdx ? "true" : "false");
      });
    }

    function selectSuggestion(idx) {
      var item = suggestions[idx];
      if (!item) return;
      payerInput.value = item.name;
      var d = refInput.value.replace(/\u200e|\u200f/g, "").trim();
      lastNLRef = d;
      lastNLName = item.name;
      setHint(false);
      closeList();
      payerInput.focus();
      payerInput.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function renderList(items) {
      if (!listEl || !items.length) {
        closeList();
        return;
      }
      listOpen = true;
      listEl.hidden = false;
      listEl.innerHTML = "";
      items.forEach(function (item, i) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "rd-payer-ac-item";
        btn.setAttribute("role", "option");
        var main = document.createElement("span");
        main.className = "rd-payer-ac-item-name";
        main.textContent = item.name;
        btn.appendChild(main);
        if (usedLabel && item.count > 0) {
          var meta = document.createElement("span");
          meta.className = "rd-payer-ac-item-meta";
          meta.textContent = item.count > 1 ? usedLabel + " (" + item.count + ")" : usedLabel;
          btn.appendChild(meta);
        }
        btn.addEventListener("mousedown", function (ev) {
          ev.preventDefault();
        });
        btn.addEventListener("click", function () {
          selectSuggestion(i);
        });
        listEl.appendChild(btn);
      });
      activeIdx = 0;
      updateActiveHighlight();
    }

    function runRefLookup() {
      var d = refInput.value.replace(/\u200e|\u200f/g, "").trim();
      refReqId += 1;
      var myId = refReqId;
      if (d.length < MIN_REF_LEN) {
        setHint(false);
        return;
      }
      if (lastRefAbort) lastRefAbort.abort();
      lastRefAbort = new AbortController();
      var u = new URL(apiRef, window.location.origin);
      u.searchParams.set("number", d);
      fetch(u.toString(), {
        signal: lastRefAbort.signal,
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      })
        .then(function (res) {
          if (myId !== refReqId) return null;
          if (!res.ok) return null;
          return res.json();
        })
        .then(function (data) {
          if (!data || myId !== refReqId) return;
          var n = data.payer_name ? String(data.payer_name).trim() : "";
          if (!n) {
            setHint(false);
            return;
          }
          if (shouldApplyNumberLookup(d, n)) {
            payerInput.value = n;
            lastNLRef = d;
            lastNLName = n;
            setHint(true);
            payerInput.dispatchEvent(new Event("input", { bubbles: true }));
          }
        })
        .catch(function (e) {
          if (e.name === "AbortError") return;
        });
    }

    function runNameSuggest() {
      var q = payerInput.value.trim();
      nameReqId += 1;
      var myId = nameReqId;
      if (q.length < MIN_NAME_QUERY) {
        closeList();
        return;
      }
      if (lastNameAbort) lastNameAbort.abort();
      lastNameAbort = new AbortController();
      var u = new URL(apiSuggest, window.location.origin);
      u.searchParams.set("q", q);
      fetch(u.toString(), {
        signal: lastNameAbort.signal,
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      })
        .then(function (res) {
          if (myId !== nameReqId) return null;
          if (!res.ok) return null;
          return res.json();
        })
        .then(function (data) {
          if (!data || myId !== nameReqId) return;
          suggestions = (data.suggestions || [])
            .map(function (s) {
              return { name: s.name, count: s.count };
            })
            .slice(0, 10);
          renderList(suggestions);
        })
        .catch(function (e) {
          if (e.name === "AbortError") return;
        });
    }

    refInput.addEventListener("input", function () {
      if (refTimer) window.clearTimeout(refTimer);
      refTimer = window.setTimeout(runRefLookup, DEBOUNCE_REF_MS);
    });

    payerInput.addEventListener("input", function () {
      if (nameTimer) window.clearTimeout(nameTimer);
      var p = payerInput.value.trim();
      if (lastNLName !== undefined && p !== lastNLName) {
        setHint(false);
      }
      if (p.length < MIN_NAME_QUERY) {
        closeList();
      } else {
        nameTimer = window.setTimeout(runNameSuggest, DEBOUNCE_NAME_MS);
      }
    });

    payerInput.addEventListener("keydown", function (ev) {
      if (!listOpen || !suggestions.length) return;
      if (ev.key === "ArrowDown") {
        ev.preventDefault();
        activeIdx = (activeIdx + 1) % suggestions.length;
        updateActiveHighlight();
      } else if (ev.key === "ArrowUp") {
        ev.preventDefault();
        activeIdx = (activeIdx - 1 + suggestions.length) % suggestions.length;
        updateActiveHighlight();
      } else if (ev.key === "Enter" && activeIdx >= 0) {
        ev.preventDefault();
        selectSuggestion(activeIdx);
      } else if (ev.key === "Escape") {
        closeList();
      }
    });

    payerInput.addEventListener("blur", function () {
      window.setTimeout(closeList, 200);
    });

    document.addEventListener("click", function (ev) {
      if (!listEl || listEl.hidden) return;
      if (listEl.contains(ev.target)) return;
      if (ev.target === payerInput || payerInput.contains(ev.target)) return;
      closeList();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
