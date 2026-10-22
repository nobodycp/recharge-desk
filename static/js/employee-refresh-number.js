(function () {
  var root = document.getElementById("rd-refresh-page");
  if (!root) return;

  var refreshUrl = root.getAttribute("data-refresh-url") || "";
  var statusUrlTemplate = root.getAttribute("data-status-url") || "";
  var waitSeconds = Number(root.getAttribute("data-wait-seconds")) || 90;
  var phoneInp = document.getElementById("rd-refresh-phone-input");
  var submitBtn = document.getElementById("rd-refresh-submit");

  function getCsrfToken() {
    var el = document.querySelector("#rd-refresh-page input[name='csrfmiddlewaretoken']");
    return el && el.value ? el.value : "";
  }

  function formatElapsedSeconds(seconds) {
    if (!Number.isFinite(seconds) || seconds < 60) {
      return root.getAttribute("data-msg-less-than-minute") || "";
    }
    var totalMinutes = Math.floor(seconds / 60);
    var hours = Math.floor(totalMinutes / 60);
    var minutes = totalMinutes % 60;
    var hourWord =
      hours === 1
        ? root.getAttribute("data-msg-hour") || "hour"
        : root.getAttribute("data-msg-hours") || "hours";
    var minuteWord =
      minutes === 1
        ? root.getAttribute("data-msg-minute") || "minute"
        : root.getAttribute("data-msg-minutes") || "minutes";
    var andWord = root.getAttribute("data-msg-time-and") || "and";
    if (hours > 0 && minutes > 0) {
      return hours + " " + hourWord + " " + andWord + " " + minutes + " " + minuteWord;
    }
    if (hours > 0) return hours + " " + hourWord;
    return minutes + " " + minuteWord;
  }

  // Localized bodies for statuses whose payload may arrive without a message.
  var FALLBACK_BODY_ATTR = { busy: "data-msg-busy", processing: "data-msg-processing" };

  function buildRefreshBody(data) {
    var msg = data && data.message ? data.message : {};
    var status = (data && data.status) || "";
    var fallbackAttr = FALLBACK_BODY_ATTR[status];
    var body = msg.body || (fallbackAttr && root.getAttribute(fallbackAttr)) || "";
    var secs = data && data.seconds_since_last_refresh;
    if (status === "wait" && typeof secs === "number" && secs > 0 && body) {
      var tpl = root.getAttribute("data-msg-wait-with-time") || "{body} (last refresh {time} ago)";
      return tpl.replace("{body}", body).replace("{time}", formatElapsedSeconds(secs));
    }
    return body;
  }

  var TOAST_LEVEL = {
    refreshed: "success",
    wait: "warning",
    cooldown: "warning",
    busy: "warning",
    not_found: "error",
    error: "error",
  };

  function noticeText(title, body) {
    title = String(title || "").trim();
    body = String(body || "").trim();
    if (title && body && body.indexOf(title) !== 0) return title + "\n" + body;
    return body || title;
  }

  function showResult(title, body, status) {
    var text = noticeText(title, body);
    if (!text || !window.rdToast) return;
    var level = TOAST_LEVEL[status] || "info";
    window.rdToast(text, level, level === "success" ? 8000 : 7000);
  }

  if (submitBtn) submitBtn.setAttribute("data-default-label", submitBtn.textContent || "");

  function isValidRefreshPhone(value) {
    return /^05[0123458]\d{7}$/.test(value);
  }

  if (phoneInp) {
    phoneInp.addEventListener("input", function () {
      // 22 covers a full ICCID. A 10-digit cap cut serials and matched the wrong line.
      phoneInp.value = phoneInp.value.replace(/\D/g, "").slice(0, 22);
    });
  }

  // Status polling (same rules as the public page): never faster than every
  // 2 s, back off (not a failure) on HTTP 429, give up after 3 failed
  // requests in a row, and stop as soon as another number is submitted.
  var JOB_PLACEHOLDER = "00000000-0000-0000-0000-000000000000";
  var POLL_INTERVAL_MS = 2000;
  var POLL_MAX_BACKOFF_MS = 10000;
  var POLL_MAX_FAILURES = 3;
  var POLL_GRACE_SECONDS = 10;
  var activeRun = 0;

  function sleep(ms) {
    return new Promise(function (resolve) {
      setTimeout(resolve, ms);
    });
  }

  function genericErrorPayload() {
    return {
      status: "error",
      message: {
        title: root.getAttribute("data-msg-error-title") || "",
        body: root.getAttribute("data-msg-error-body") || "",
      },
    };
  }

  function statusUrlFor(jobId) {
    return statusUrlTemplate.replace(JOB_PLACEHOLDER, encodeURIComponent(jobId));
  }

  function showPayload(data) {
    var msg = (data && data.message) || {};
    showResult(msg.title || "", buildRefreshBody(data), (data && data.status) || "");
  }

  // Resolves with the final payload, or `null` when the run was superseded.
  function pollJob(jobId, runId) {
    var deadline = Date.now() + (waitSeconds + POLL_GRACE_SECONDS) * 1000;
    var failures = 0;
    var delay = POLL_INTERVAL_MS;

    function step() {
      if (Date.now() > deadline) return Promise.resolve(genericErrorPayload());
      return sleep(delay).then(function () {
        if (runId !== activeRun) return null;
        return fetch(statusUrlFor(jobId), {
          method: "GET",
          credentials: "same-origin",
          headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
        })
          .then(function (r) {
            if (r.status === 429) return { throttled: true };
            if (!r.ok) return { data: null };
            return r.json().then(function (data) {
              return { data: data };
            });
          })
          .catch(function () {
            return { data: null };
          })
          .then(function (res) {
            if (runId !== activeRun) return null;
            if (res.throttled) {
              // Rate limited: wait longer, but this is not a failure.
              delay = Math.min(delay * 2, POLL_MAX_BACKOFF_MS);
              return step();
            }
            delay = POLL_INTERVAL_MS;
            var data = res.data;
            if (!data || typeof data !== "object") {
              failures += 1;
              if (failures >= POLL_MAX_FAILURES) return genericErrorPayload();
              return step();
            }
            failures = 0;
            if (data.status !== "processing" || data.final === true) return data;
            return step();
          });
      });
    }
    return step();
  }

  function runRefresh() {
    if (!phoneInp) return;
    var phone = String(phoneInp.value || "").replace(/\D/g, "");
    if (phone.indexOf("972") === 0) phone = "0" + phone.slice(3);
    if (phone.length === 9 && phone.charAt(0) === "5") phone = "0" + phone;
    phoneInp.value = phone;
    if (!phone) {
      window.rdToast(root.getAttribute("data-msg-phone") || "", "error");
      return;
    }
    if (!isValidRefreshPhone(phone)) {
      window.rdToast(root.getAttribute("data-msg-invalid-phone") || "", "error");
      return;
    }
    var runId = ++activeRun;
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = root.getAttribute("data-msg-working") || "…";
    }
    var body = new URLSearchParams();
    body.set("phone", phone);
    fetch(refreshUrl, {
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
        return r.json().then(function (data) {
          return { status: r.status, data: data };
        });
      })
      .then(function (res) {
        if (runId !== activeRun) return null;
        var data = res.data || {};
        if (res.status === 202) {
          // Without a job id or status URL we cannot poll: generic error.
          if (!(data.job && statusUrlTemplate)) return genericErrorPayload();
          // Keep the "refreshing" label up while the worker (re)logs in.
          return pollJob(data.job, runId);
        }
        return data;
      })
      .then(function (data) {
        if (data === null || runId !== activeRun) return;
        showPayload(data);
      })
      .catch(function () {
        if (runId === activeRun) showPayload(genericErrorPayload());
      })
      .finally(function () {
        if (runId !== activeRun) return;
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.textContent = submitBtn.getAttribute("data-default-label") || "";
        }
      });
  }

  if (submitBtn) submitBtn.addEventListener("click", runRefresh);
  if (phoneInp) {
    phoneInp.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        runRefresh();
      }
    });
  }
})();
