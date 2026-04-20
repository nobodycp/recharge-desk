/**
 * Data UI: copy-to-clipboard, HTMX grid loading, lightweight feedback.
 */
(function () {
  function showToast(message) {
    var el = document.createElement("div");
    el.className = "rd-toast shadow-sm";
    el.setAttribute("role", "status");
    el.textContent = message;
    document.body.appendChild(el);
    requestAnimationFrame(function () {
      el.classList.add("is-visible");
    });
    setTimeout(function () {
      el.classList.remove("is-visible");
      setTimeout(function () {
        el.remove();
      }, 220);
    }, 1600);
  }

  document.body.addEventListener("click", function (e) {
    var btn = e.target.closest(".rd-copy-ref");
    if (!btn) return;
    var text = btn.getAttribute("data-copy");
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      e.preventDefault();
      navigator.clipboard.writeText(text).then(
        function () {
          var msg = btn.getAttribute("data-copy-done");
          showToast(msg || "Copied");
        },
        function () {}
      );
    }
  });

  document.body.addEventListener("htmx:beforeRequest", function (evt) {
    var t = evt.detail.target;
    if (t && t.classList && t.classList.contains("rd-datagrid")) {
      t.classList.add("is-loading");
    }
  });

  document.body.addEventListener("htmx:afterSwap", function (evt) {
    var t = evt.detail.target;
    if (t && t.classList && t.classList.contains("rd-datagrid")) {
      t.classList.remove("is-loading");
    }
  });

  document.body.addEventListener("htmx:responseError", function (evt) {
    var t = evt.detail.target;
    if (t && t.classList && t.classList.contains("rd-datagrid")) {
      t.classList.remove("is-loading");
    }
  });

  /* Row actions <details> panel: fixed position so it is not clipped by scroll shells */
  var panelPosProps = ["position", "top", "left", "right", "max-height", "overflow-y", "z-index"];

  function clearActionsPanelPosition(panel) {
    if (!panel) return;
    panelPosProps.forEach(function (p) {
      panel.style.removeProperty(p);
    });
  }

  function placeActionsPanel(details) {
    var panel = details.querySelector(".rd-actions-details__panel");
    var summary = details.querySelector("summary");
    if (!panel || !summary) return;
    if (!details.open) {
      clearActionsPanelPosition(panel);
      return;
    }
    document.querySelectorAll("details.rd-actions-details[open]").forEach(function (other) {
      if (other !== details) {
        other.removeAttribute("open");
        var op = other.querySelector(".rd-actions-details__panel");
        clearActionsPanelPosition(op);
      }
    });

    var r = summary.getBoundingClientRect();
    var margin = 8;
    var vw = window.innerWidth;
    var vh = window.innerHeight;

    panel.style.position = "fixed";
    panel.style.zIndex = "1060";
    panel.style.top = r.bottom + margin + "px";
    panel.style.right = "auto";

    requestAnimationFrame(function () {
      var w = panel.getBoundingClientRect().width || 176;
      var left = r.right - w;
      left = Math.max(margin, Math.min(left, vw - w - margin));
      panel.style.left = left + "px";

      var spaceBelow = vh - r.bottom - margin * 2;
      var ph = panel.getBoundingClientRect().height;
      if (spaceBelow < 140 && ph < r.top - margin * 2) {
        panel.style.top = Math.max(margin, r.top - ph - margin) + "px";
        panel.style.maxHeight = Math.min(320, r.top - margin * 2) + "px";
      } else {
        panel.style.maxHeight = Math.min(320, Math.max(120, spaceBelow)) + "px";
      }
      panel.style.overflowY = "auto";
    });
  }

  document.body.addEventListener(
    "toggle",
    function (evt) {
      var el = evt.target;
      if (!el || !el.classList || !el.classList.contains("rd-actions-details")) return;
      placeActionsPanel(el);
    },
    true
  );

  function closeAllActionsPanels() {
    document.querySelectorAll("details.rd-actions-details[open]").forEach(function (d) {
      d.removeAttribute("open");
      clearActionsPanelPosition(d.querySelector(".rd-actions-details__panel"));
    });
  }

  document.addEventListener(
    "scroll",
    function () {
      closeAllActionsPanels();
    },
    true
  );

  window.addEventListener("resize", function () {
    closeAllActionsPanels();
  });

  document.addEventListener(
    "click",
    function (e) {
      var d = e.target.closest("details.rd-actions-details");
      document.querySelectorAll("details.rd-actions-details[open]").forEach(function (openDet) {
        if (openDet === d || openDet.contains(e.target)) return;
        openDet.removeAttribute("open");
        clearActionsPanelPosition(openDet.querySelector(".rd-actions-details__panel"));
      });

      /* Same outside-click contract for the topbar profile menu. The user
         menu is positioned with plain CSS so we just need to flip [open]. */
      var um = e.target.closest("details.rd-user-menu");
      document.querySelectorAll("details.rd-user-menu[open]").forEach(function (openMenu) {
        if (openMenu === um || openMenu.contains(e.target)) return;
        openMenu.removeAttribute("open");
      });
    },
    true
  );

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    document.querySelectorAll("details.rd-user-menu[open]").forEach(function (m) {
      m.removeAttribute("open");
    });
  });

  /* Collapsible filter cards: keep the "N active" badge in sync with the
     current form state after any htmx-driven submit, so the count stays
     accurate even though we never reload the page. */
  var FILTER_NOISE = { sort: 1, order: 1, page: 1, csrfmiddlewaretoken: 1 };

  function countActiveFilters(form) {
    if (!form) return 0;
    var seen = {};
    var count = 0;
    var fields = form.querySelectorAll("input, select, textarea");
    for (var i = 0; i < fields.length; i++) {
      var el = fields[i];
      var name = el.name;
      if (!name || FILTER_NOISE[name] || seen[name]) continue;
      var t = (el.type || "").toLowerCase();
      if (t === "checkbox" || t === "radio") {
        if (!el.checked) continue;
      } else if (el.value === "" || el.value == null) {
        continue;
      }
      seen[name] = 1;
      count++;
    }
    return count;
  }

  function refreshFilterBadge(card) {
    if (!card) return;
    var form = card.querySelector("form");
    var badge = card.querySelector("[data-rd-filter-count]");
    if (!form || !badge) return;
    var n = countActiveFilters(form);
    badge.textContent = String(n);
    badge.style.display = n > 0 ? "" : "none";
  }

  document.body.addEventListener("htmx:afterRequest", function (evt) {
    var src = evt.detail && evt.detail.elt;
    if (!src) return;
    var card = src.closest && src.closest(".rd-filter-card");
    if (card) refreshFilterBadge(card);
  });

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".rd-filter-card").forEach(refreshFilterBadge);
  });

  // ===== Notifications: live polling for the topbar bell ==============
  // Polls /notifications/poll/ every POLL_INTERVAL ms and updates the
  // badge count, the per-row counts inside the dropdown, and the
  // "all caught up" empty state in place — no full page reload needed
  // for a sale that just landed in awaiting / pending to surface.
  //
  // Pauses while the tab is hidden (Page Visibility API) so background
  // tabs don't pile up requests, and refreshes immediately whenever
  // the tab regains focus so management never sees stale numbers
  // when switching back from another tab.
  (function setupLiveNotifications() {
    var root = document.querySelector("[data-rd-notifications]");
    if (!root) return;
    var url = root.getAttribute("data-poll-url");
    if (!url) return;

    var POLL_INTERVAL = 15000; // ms — cheap two COUNTs per call
    var badge = root.querySelector("[data-rd-notif-badge]");
    var aria = root.querySelector("[data-rd-notif-aria]");
    var awaitingEl = root.querySelector("[data-rd-notif-awaiting]");
    var pendingEl = root.querySelector("[data-rd-notif-pending]");
    var emptyEl = root.querySelector("[data-rd-notif-empty]");
    var trigger = root.querySelector(".rd-notif-trigger");

    var timer = null;
    var inflight = null;
    var pollDebounce = null;

    function setCount(el, n) {
      if (!el) return;
      el.textContent = String(n);
      if (n > 0) el.classList.add("is-active");
      else el.classList.remove("is-active");
    }

    function applyPayload(p) {
      var total = Number(p.total || 0);
      var awaiting = Number(p.awaiting || 0);
      var pending = Number(p.pending || 0);

      setCount(awaitingEl, awaiting);
      setCount(pendingEl, pending);

      if (badge) {
        badge.textContent = String(total);
        if (total > 0) badge.removeAttribute("hidden");
        else badge.setAttribute("hidden", "hidden");
      }
      if (trigger) {
        if (total > 0) trigger.classList.add("is-active");
        else trigger.classList.remove("is-active");
      }
      if (aria) {
        // Best-effort plural string; the real translated copy is rendered
        // server-side on first paint, so this is just a fallback.
        aria.textContent =
          total === 1
            ? "1 item needs attention"
            : total + " items need attention";
      }
      if (emptyEl) {
        if (total > 0) emptyEl.setAttribute("hidden", "hidden");
        else emptyEl.removeAttribute("hidden");
      }
    }

    function poll() {
      if (document.visibilityState === "hidden") return;
      if (inflight && typeof inflight.abort === "function") {
        try { inflight.abort(); } catch (_) {}
      }
      var ctrl = typeof AbortController === "function" ? new AbortController() : null;
      inflight = ctrl;
      fetch(url, {
        credentials: "same-origin",
        headers: { "X-Requested-With": "fetch" },
        signal: ctrl ? ctrl.signal : undefined,
        cache: "no-store",
      })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (payload) { if (payload) applyPayload(payload); })
        .catch(function () { /* network blip / aborted — try again next tick */ });
    }

    function start() {
      stop();
      timer = setInterval(poll, POLL_INTERVAL);
    }

    function stop() {
      if (timer) {
        clearInterval(timer);
        timer = null;
      }
    }

    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") {
        poll();
        start();
      } else {
        stop();
      }
    });

    window.addEventListener("focus", poll);
    window.addEventListener("pageshow", poll);

    // Re-poll right after any successful HTMX mutation (approve a sale,
    // mark a payment paid, delete a row, etc.) so the badge updates the
    // instant the action lands instead of waiting for the next 15s tick.
    document.body.addEventListener("htmx:afterRequest", function (e) {
      var detail = e && e.detail;
      var xhr = detail && detail.xhr;
      if (!xhr || xhr.status < 200 || xhr.status >= 400) return;
      var verb = (detail.requestConfig && detail.requestConfig.verb) || "";
      if (String(verb).toLowerCase() === "get") return;
      if (pollDebounce) clearTimeout(pollDebounce);
      pollDebounce = setTimeout(poll, 150);
    });

    start();
  })();

  // ===== Print: blank document.title so the browser's print header
  // (which renders document.title in the top margin) shows nothing.
  // Combined with @page { margin: 0 } in print CSS, this gives a
  // header/footer-free printout in Chrome / Edge. We restore the
  // original title immediately after printing so the browser tab
  // and history don't lose their label.
  (function setupPrintTitleBlank() {
    var savedTitle = null;
    window.addEventListener("beforeprint", function () {
      savedTitle = document.title;
      document.title = "\u00a0"; // non-breaking space: empty visually
    });
    window.addEventListener("afterprint", function () {
      if (savedTitle !== null) {
        document.title = savedTitle;
        savedTitle = null;
      }
    });
  })();

  // ===== Global search: icon-toggled panel + live suggestions =========
  // The topbar shows just an icon by default. Clicking it (or pressing
  // "/" outside a form field) opens a floating panel with the input
  // and a debounced suggestion dropdown. Suggestions come from
  // /management/search/suggest/ and are grouped (Sales / Customers /
  // Payments). Keyboard: ArrowUp/Down moves selection, Enter activates
  // the highlighted item (or submits to the full results page if
  // nothing is highlighted), Escape closes.
  (function setupGlobalSearch() {
    var root = document.querySelector("[data-rd-search]");
    if (!root) return;
    var toggle = root.querySelector("[data-rd-search-toggle]");
    var panel = root.querySelector("[data-rd-search-panel]");
    var input = root.querySelector("#rd-global-search");
    var results = root.querySelector("[data-rd-search-results]");
    var suggestUrl = root.getAttribute("data-suggest-url");
    if (!toggle || !panel || !input || !results || !suggestUrl) return;

    var debounceTimer = null;
    var lastQuery = null;
    var inflight = null;
    var selectedIndex = -1;
    var renderedItems = [];

    function open() {
      root.setAttribute("data-rd-search-open", "true");
      toggle.setAttribute("aria-expanded", "true");
      panel.hidden = false;
      setTimeout(function () {
        input.focus();
        input.select();
      }, 0);
      if (input.value.trim().length >= 2 && input.value.trim() !== lastQuery) {
        fetchSuggestions(input.value.trim());
      }
    }

    function close() {
      root.removeAttribute("data-rd-search-open");
      toggle.setAttribute("aria-expanded", "false");
      panel.hidden = true;
      hideResults();
      selectedIndex = -1;
    }

    function hideResults() {
      results.hidden = true;
      results.innerHTML = "";
      renderedItems = [];
    }

    function escapeHtml(s) {
      return String(s == null ? "" : s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    function render(payload) {
      results.innerHTML = "";
      renderedItems = [];
      var groups = (payload && payload.groups) || [];
      if (!groups.length) {
        var empty = document.createElement("div");
        empty.className = "rd-search-empty";
        empty.textContent = payload.empty_label || "No results";
        results.appendChild(empty);
      } else {
        groups.forEach(function (group) {
          var section = document.createElement("div");
          section.className = "rd-search-group";
          section.setAttribute("role", "group");

          var label = document.createElement("div");
          label.className = "rd-search-group-label";
          label.textContent = group.label || group.key;
          section.appendChild(label);

          (group.items || []).forEach(function (item) {
            var a = document.createElement("a");
            a.className = "rd-search-item";
            a.setAttribute("role", "option");
            a.href = item.url;
            a.innerHTML =
              '<span class="rd-search-item-label">' +
              escapeHtml(item.label) +
              "</span>" +
              (item.sublabel
                ? '<span class="rd-search-item-sublabel">' +
                  escapeHtml(item.sublabel) +
                  "</span>"
                : "");
            section.appendChild(a);
            renderedItems.push(a);
          });
          results.appendChild(section);
        });
      }
      if (payload.more_url) {
        var more = document.createElement("a");
        more.className = "rd-search-more";
        more.href = payload.more_url;
        more.textContent = payload.more_label || "See all results";
        results.appendChild(more);
      }
      results.hidden = false;
      selectedIndex = -1;
    }

    function showHint(text) {
      results.innerHTML =
        '<div class="rd-search-hint">' + escapeHtml(text) + "</div>";
      results.hidden = false;
      renderedItems = [];
      selectedIndex = -1;
    }

    function fetchSuggestions(q) {
      lastQuery = q;
      if (inflight && typeof inflight.abort === "function") {
        try { inflight.abort(); } catch (_) {}
      }
      var url = suggestUrl + "?q=" + encodeURIComponent(q);
      var ctrl = typeof AbortController === "function" ? new AbortController() : null;
      inflight = ctrl;
      fetch(url, {
        credentials: "same-origin",
        headers: { "X-Requested-With": "fetch" },
        signal: ctrl ? ctrl.signal : undefined,
      })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (payload) {
          if (!payload || lastQuery !== q) return;
          render(payload);
        })
        .catch(function () { /* ignore aborts / network blips */ });
    }

    function onInput() {
      var q = input.value.trim();
      if (q.length < 2) {
        if (q.length === 0) {
          hideResults();
        } else {
          showHint(input.getAttribute("data-hint") || "Type at least 2 characters…");
        }
        lastQuery = q;
        return;
      }
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function () { fetchSuggestions(q); }, 180);
    }

    function highlight(idx) {
      renderedItems.forEach(function (el, i) {
        if (i === idx) {
          el.setAttribute("aria-selected", "true");
          el.scrollIntoView({ block: "nearest" });
        } else {
          el.removeAttribute("aria-selected");
        }
      });
      selectedIndex = idx;
    }

    toggle.addEventListener("click", function () {
      if (panel.hidden) open(); else close();
    });
    input.addEventListener("input", onInput);

    input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        e.preventDefault();
        close();
        toggle.focus();
      } else if (e.key === "ArrowDown") {
        if (!renderedItems.length) return;
        e.preventDefault();
        highlight((selectedIndex + 1) % renderedItems.length);
      } else if (e.key === "ArrowUp") {
        if (!renderedItems.length) return;
        e.preventDefault();
        highlight(selectedIndex <= 0 ? renderedItems.length - 1 : selectedIndex - 1);
      } else if (e.key === "Enter") {
        if (selectedIndex >= 0 && renderedItems[selectedIndex]) {
          e.preventDefault();
          window.location.href = renderedItems[selectedIndex].href;
        }
        // otherwise fall through and submit the form (full results page)
      }
    });

    document.addEventListener("click", function (e) {
      if (!panel.hidden && !root.contains(e.target)) close();
    });

    document.addEventListener("keydown", function (e) {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      var t = e.target;
      if (!t) return;
      var tag = (t.tagName || "").toUpperCase();
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t.isContentEditable) return;
      e.preventDefault();
      open();
    });
  })();
})();
