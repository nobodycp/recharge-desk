(function () {
  // «فحص الرقم»: POST the number, poll the job every 2 s, show the server-rendered
  // card. Shares the phone field and page root with employee-refresh-number.js.
  var root = document.getElementById("rd-refresh-page");
  if (!root) return;

  var checkUrl = root.getAttribute("data-check-url") || "";
  var statusUrlTemplate = root.getAttribute("data-check-status-url") || "";
  var waitSeconds = Number(root.getAttribute("data-wait-seconds")) || 90;
  var phoneInp = document.getElementById("rd-refresh-phone-input");
  var checkBtn = document.getElementById("rd-check-submit");
  var resultBox = document.getElementById("rd-check-result");
  if (!checkBtn) return;

  var JOB_PLACEHOLDER = "00000000-0000-0000-0000-000000000000";
  var POLL_INTERVAL_MS = 2000;
  var POLL_GRACE_SECONDS = 10;
  var WORKING_LABEL = "جارٍ الفحص…";
  var GENERIC_ERROR = "حدث خطأ، حاول مرة أخرى";
  // Bumped on every press: anything started by an older press is dropped.
  var activeRun = 0;

  checkBtn.setAttribute("data-default-label", checkBtn.textContent || "");

  function getCsrfToken() {
    var el = document.querySelector("#rd-refresh-page input[name='csrfmiddlewaretoken']");
    return el && el.value ? el.value : "";
  }

  function notify(message) {
    if (window.rdToast) window.rdToast(message, "error");
    else if (window.alert) window.alert(message);
  }

  function isValidPhone(value) {
    return /^05[0123458]\d{7}$/.test(value);
  }

  function sleep(ms) {
    return new Promise(function (resolve) {
      setTimeout(resolve, ms);
    });
  }

  var SKELETON =
    '<div class="rd-ncheck-skel" aria-hidden="true"><div class="rd-ncheck-skel__hero"></div>' +
    '<div class="rd-ncheck-skel__row"><span></span><span></span><span></span></div>' +
    '<div class="rd-ncheck-skel__row rd-ncheck-skel__row--2"><span></span><span></span></div></div>' +
    '<span class="visually-hidden">' + WORKING_LABEL + "</span>";
  var COPY_FEEDBACK_MS = 1500;

  // Static markup only; the result itself replaces it.
  function showLoading() {
    if (!resultBox) return;
    resultBox.classList.remove("is-error");
    resultBox.innerHTML = SKELETON;
    resultBox.classList.remove("d-none");
  }

  // Only the server-rendered (auto-escaped) card is ever treated as HTML.
  function showCard(html) {
    if (!resultBox) return;
    resultBox.classList.remove("is-error");
    resultBox.innerHTML = html;
    resultBox.classList.remove("d-none");
  }

  function showText(message) {
    if (!resultBox) return;
    resultBox.classList.add("is-error");
    resultBox.textContent = typeof message === "string" && message ? message : GENERIC_ERROR;
    resultBox.classList.remove("d-none");
  }

  function copyIccid(btn) {
    var clipboard = window.navigator && window.navigator.clipboard;
    if (!clipboard || btn.classList.contains("is-done")) return;
    var label = btn.querySelector("span");
    clipboard.writeText(btn.getAttribute("data-copy") || "").then(function () {
      var original = label ? label.textContent : "";
      btn.classList.add("is-done");
      if (label) label.textContent = btn.getAttribute("data-done-label") || original;
      setTimeout(function () {
        btn.classList.remove("is-done");
        if (label) label.textContent = original;
      }, COPY_FEEDBACK_MS);
    });
  }

  function showPayload(data) {
    if (data && data.status === "ok" && typeof data.html === "string") showCard(data.html);
    else showText(data && data.message);
  }

  function genericError() {
    return { status: "error", message: GENERIC_ERROR };
  }

  function statusUrlFor(jobId) {
    return statusUrlTemplate.replace(JOB_PLACEHOLDER, encodeURIComponent(jobId));
  }

  // JSON body of a response, or the generic error when it isn't JSON.
  function readPayload(r) {
    return r.json().catch(function () {
      return genericError();
    });
  }

  // Resolves with the final payload, or `null` when the run was superseded.
  function pollJob(jobId, runId) {
    var deadline = Date.now() + (waitSeconds + POLL_GRACE_SECONDS) * 1000;

    function step() {
      if (Date.now() > deadline) return Promise.resolve(genericError());
      return sleep(POLL_INTERVAL_MS).then(function () {
        if (runId !== activeRun) return null;
        return fetch(statusUrlFor(jobId), {
          method: "GET",
          credentials: "same-origin",
          headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
        }).then(function (r) {
          // Any HTTP error (403 switched off, 4xx, 5xx) is terminal.
          if (!r.ok) {
            return readPayload(r).then(function (data) {
              if (runId !== activeRun) return null;
              return data && data.status === "error" ? data : genericError();
            });
          }
          return readPayload(r).then(function (data) {
            if (runId !== activeRun) return null;
            if (!data || typeof data !== "object") return genericError();
            if (data.status === "processing" && data.final !== true) return step();
            return data;
          });
        });
      });
    }
    return step();
  }

  function runCheck() {
    if (!phoneInp) return;
    var phone = String(phoneInp.value || "").replace(/\D/g, "");
    if (phone.indexOf("972") === 0) phone = "0" + phone.slice(3);
    if (phone.length === 9 && phone.charAt(0) === "5") phone = "0" + phone;
    phoneInp.value = phone;
    if (!phone) {
      notify(root.getAttribute("data-msg-phone") || "");
      return;
    }
    if (!isValidPhone(phone)) {
      notify(root.getAttribute("data-msg-invalid-phone") || "");
      return;
    }
    var runId = ++activeRun;
    checkBtn.disabled = true;
    checkBtn.textContent = WORKING_LABEL;
    showLoading();

    var body = new URLSearchParams();
    body.set("phone", phone);
    fetch(checkUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": getCsrfToken(),
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
      },
      body: body.toString(),
    })
      .then(function (r) {
        return readPayload(r).then(function (data) {
          if (runId !== activeRun) return null;
          if (r.status === 202) {
            // Without a job id or status URL we cannot poll.
            if (!(data && data.job && statusUrlTemplate)) return genericError();
            return pollJob(data.job, runId);
          }
          if (!r.ok) return data && data.status === "error" ? data : genericError();
          return data;
        });
      })
      .then(function (data) {
        if (data === null || runId !== activeRun) return;
        showPayload(data);
      })
      .catch(function () {
        if (runId === activeRun) showPayload(genericError());
      })
      .finally(function () {
        if (runId !== activeRun) return;
        checkBtn.disabled = false;
        checkBtn.textContent = checkBtn.getAttribute("data-default-label") || "";
      });
  }

  checkBtn.addEventListener("click", runCheck);
  if (resultBox) {
    resultBox.addEventListener("click", function (event) {
      var target = event.target;
      var btn = target && target.closest ? target.closest("[data-copy]") : null;
      if (btn) copyIccid(btn);
    });
  }
})();
