(function () {
  document.querySelectorAll(".employee-clipboard-paste-btn").forEach(function (pasteBtn) {
    var targetId = pasteBtn.getAttribute("data-paste-target");
    if (!targetId) return;
    pasteBtn.addEventListener("click", function () {
      var ref = document.getElementById(targetId);
      if (!ref) return;
      var failMsg = pasteBtn.getAttribute("data-paste-fail") || "";
      function applyText(text) {
        if (text == null || text === "") return;
        var raw = String(text).replace(/\u200e|\u200f/g, "").trim();
        var firstLine =
          raw
            .split(/\r?\n/)
            .map(function (l) {
              return l.trim();
            })
            .filter(Boolean)[0] || raw;
        var maxAttr = ref.getAttribute("maxlength");
        var maxLen = maxAttr ? parseInt(maxAttr, 10) : 2000;
        ref.value = firstLine.replace(/\s+/g, " ").slice(0, maxLen);
        ref.dispatchEvent(new Event("input", { bubbles: true }));
        ref.focus();
      }
      if (navigator.clipboard && typeof navigator.clipboard.readText === "function") {
        navigator.clipboard.readText().then(applyText).catch(function () {
          if (failMsg) window.rdToast(failMsg, "error");
          ref.focus();
        });
      } else {
        if (failMsg) window.rdToast(failMsg, "error");
        ref.focus();
      }
    });
  });
})();
