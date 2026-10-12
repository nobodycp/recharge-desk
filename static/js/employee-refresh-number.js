(function () {
  var root = document.getElementById("rd-refresh-page");
  if (!root) return;

  var refreshUrl = root.getAttribute("data-refresh-url") || "";
  var phoneInp = document.getElementById("rd-refresh-phone-input");
  var submitBtn = document.getElementById("rd-refresh-submit");
  var resultBox = document.getElementById("rd-refresh-result");
  var resultTitle = document.getElementById("rd-refresh-result-title");
  var resultBody = document.getElementById("rd-refresh-result-body");

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

  function buildRefreshBody(data) {
    var msg = data && data.message ? data.message : {};
    var body = msg.body || "";
    var status = (data && data.status) || "";
    var secs = data && data.seconds_since_last_refresh;
    if (status === "wait" && typeof secs === "number" && secs > 0 && body) {
      var tpl = root.getAttribute("data-msg-wait-with-time") || "{body} (last refresh {time} ago)";
      return tpl.replace("{body}", body).replace("{time}", formatElapsedSeconds(secs));
    }
    return body;
  }

  function showResult(title, body) {
    if (!resultBox) return;
    resultBox.classList.remove("d-none");
    if (resultTitle) resultTitle.textContent = title || "";
    if (resultBody) resultBody.textContent = body || "";
  }

  if (submitBtn) submitBtn.setAttribute("data-default-label", submitBtn.textContent || "");

  function isValidRefreshPhone(value) {
    return /^05[0123458]\d{7}$/.test(value);
  }

  if (phoneInp) {
    phoneInp.addEventListener("input", function () {
      phoneInp.value = phoneInp.value.replace(/\D/g, "").slice(0, 10);
    });
  }

  function runRefresh() {
    if (!phoneInp) return;
    var phone = String(phoneInp.value || "").replace(/\D/g, "");
    if (phone.indexOf("972") === 0) phone = "0" + phone.slice(3);
    if (phone.length === 9 && phone.charAt(0) === "5") phone = "0" + phone;
    phoneInp.value = phone;
    if (!phone) {
      window.alert(root.getAttribute("data-msg-phone") || "");
      return;
    }
    if (!isValidRefreshPhone(phone)) {
      window.alert(root.getAttribute("data-msg-invalid-phone") || "");
      return;
    }
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
          return { ok: r.ok, data: data };
        });
      })
      .then(function (res) {
        var data = res.data || {};
        var msg = data.message || {};
        showResult(msg.title || "", buildRefreshBody(data));
      })
      .catch(function () {
        showResult("", "");
      })
      .finally(function () {
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
