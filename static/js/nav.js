/**
 * In-app navigation on top of hx-boost (set on <body> in both shells).
 *
 * Boosted links and forms swap the whole <body>. This file restores what a
 * full page load used to guarantee:
 *   - only pages of the same shell are swapped in; anything else (login,
 *     forbidden, the other shell, downloads, error pages) gets a real load;
 *   - preventDefault() from other click/submit handlers is honoured — htmx
 *     1.9 ignores it on boosted elements, so a rejected confirm() or a failed
 *     client-side check would otherwise still send the request;
 *   - one request per boosted form, unsaved-changes prompt, progress bar,
 *     <body> attributes, scroll and filter-card state on same-page swaps.
 *
 * window.rdNavigate(url) performs the same navigation from scripts.
 */
(function () {
  "use strict";
  if (!window.htmx) return;

  var shell = document.body.getAttribute("data-rd-shell");
  var OWN_VERBS = "[hx-get],[hx-post],[hx-put],[hx-patch],[hx-delete],[data-hx-get],[data-hx-post]";

  function isBoosted(elt) {
    if (!elt || !elt.matches || !elt.matches("a[href], form") || elt.matches(OWN_VERBS)) return false;
    var scope = elt.closest("[hx-boost]");
    return !!scope && scope.getAttribute("hx-boost") === "true";
  }

  function hardNavigate(url) {
    window.location.assign(url);
  }

  window.rdNavigate = function (url) {
    var a = document.createElement("a");
    a.href = url;
    a.hidden = true;
    document.body.appendChild(a);
    htmx.process(a);
    a.click();
  };

  // Links inside a results grid inherit the grid's hx-target / hx-vals, so
  // ones that open another page opt out (hx-boost="false" data-rd-page) and
  // are sent through a fresh body-level link instead.
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("a[data-rd-page][href]");
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a.target && a.target !== "_self") return;
    e.preventDefault();
    window.rdNavigate(a.href);
  });

  /* ---- preventDefault bookkeeping ------------------------------------- */

  // An inline `onsubmit="return confirm(…)"` / `onclick="return false"` cancels
  // without calling preventDefault(), so also note whether the event was
  // already cancelled when the first counted call (htmx's own) arrived.
  function track(e) {
    var original = e.preventDefault;
    e.__rdPrevented = 0;
    e.__rdCancelledEarly = false;
    e.preventDefault = function () {
      if (e.__rdPrevented === 0 && e.defaultPrevented) e.__rdCancelledEarly = true;
      e.__rdPrevented++;
      return original.call(e);
    };
  }

  function wasCancelled(e) {
    return e.__rdPrevented > 1 || e.__rdCancelledEarly;
  }

  // Files are fetched by the browser itself, never swapped into the page.
  var FILE_PATH = /(\.(csv|xlsx?|pdf|zip|gz|json|txt|sqlite3?)$)|(\/export\/?$)|(^\/media\/)/i;

  function linkAction(elt, target) {
    if (elt.tagName !== "A") return null;
    var url = new URL(elt.href, location.href);
    if (elt.hasAttribute("download") || (url.origin === location.origin && FILE_PATH.test(url.pathname))) {
      return function () {
        window.location.assign(url.href);
      };
    }
    // Links inside a results grid inherit its hx-target; one that opens
    // another page must replace the whole page instead of nesting in the grid.
    if (target !== document.body && url.origin === location.origin && url.pathname !== location.pathname) {
      return function () {
        window.rdNavigate(url.href);
      };
    }
    return null;
  }
  document.addEventListener("click", track, true);
  document.addEventListener("submit", track, true);

  function leaveConfirmed(elt) {
    var dirty = document.querySelector("form.is-dirty:not([data-rd-submitting])");
    if (!dirty || dirty.contains(elt)) return true;
    return window.confirm(document.body.getAttribute("data-rd-leave-message") || "Leave this page?");
  }

  // htmx's own listener has already called preventDefault() once by the time
  // htmx:confirm fires; any further call came from someone else. The request
  // is held until the event has reached every listener (window, bubble), and
  // is still issued synchronously so a form's clicked submit button is kept.
  document.addEventListener("htmx:confirm", function (evt) {
    var d = evt.detail;
    if (!isBoosted(d.elt)) return;
    evt.preventDefault();
    if (d.elt.tagName === "FORM" && d.elt.classList.contains("htmx-request")) return;
    var special = linkAction(d.elt, d.target);
    var run = special || function () {
      if (leaveConfirmed(d.elt)) d.issueRequest();
    };
    var trigger = d.triggeringEvent;
    if (!trigger || !("__rdPrevented" in trigger)) {
      run();
      return;
    }
    trigger.__rdPending = function () {
      trigger.__rdPending = null;
      if (wasCancelled(trigger)) return;
      run();
    };
    setTimeout(function () {
      if (trigger.__rdPending) trigger.__rdPending();
    }, 0);
  });

  function flushPending(e) {
    if (e.__rdPending) e.__rdPending();
  }
  window.addEventListener("click", flushPending);
  window.addEventListener("submit", flushPending);

  /* ---- request lifecycle ---------------------------------------------- */

  var progress = document.createElement("div");
  progress.className = "rd-nav-progress";
  document.documentElement.appendChild(progress);
  var doneTimer = null;
  var inflight = [];
  var nav = null;

  // The bar starts and the current content dims in the same tick the request
  // is issued; the loading state is cleared by stopProgress() from every
  // terminal event (see the listeners below).
  function startProgress() {
    clearTimeout(doneTimer);
    if (progress.classList.contains("is-done")) {
      // Snap a still-fading bar back to zero before the new run starts.
      progress.classList.remove("is-done");
      void progress.offsetWidth;
    }
    progress.classList.add("is-active");
    var live = document.querySelector("[data-rd-live]");
    if (live) live.classList.add("is-live-loading");
  }

  function stopProgress() {
    if (progress.classList.contains("is-active")) {
      progress.classList.remove("is-active");
      progress.classList.add("is-done");
      clearTimeout(doneTimer);
      doneTimer = setTimeout(function () {
        progress.classList.remove("is-done");
      }, 500);
    }
    document.querySelectorAll("[data-rd-live].is-live-loading").forEach(function (live) {
      live.classList.remove("is-live-loading");
    });
  }

  // A body request that finished may be followed by another one already in
  // flight (HX-Redirect is followed in place; a second click). Keep the bar
  // until the last one is done. State is read from the XHRs themselves
  // (OPENED..LOADING), so a missed event can never leave this stuck.
  function stopIfIdle() {
    inflight = inflight.filter(function (xhr) {
      return xhr.readyState > 0 && xhr.readyState < 4;
    });
    if (!inflight.length) stopProgress();
  }

  // Safety net: `loadend` fires for load, error, abort and timeout, after
  // htmx's own handlers, even when one of them threw or (history fetch) was
  // never set. stopIfIdle() is idempotent and only stops once no tracked
  // request is still in flight, so an older request ending never cuts short
  // a newer one.
  function trackRequest(xhr) {
    if (!xhr) return;
    inflight.push(xhr);
    xhr.addEventListener("loadend", stopIfIdle);
  }

  function targetsBody(detail) {
    return detail && detail.target === document.body;
  }

  // Pages with their own double-submit guard mark the form data-submitting
  // and disable every submit button; undo that too when the form stays.
  function setFormBusy(form, busy) {
    if (!form || form.tagName !== "FORM") return;
    var ownGuard = form.hasAttribute("data-submitting");
    form.querySelectorAll('button[type="submit"], input[type="submit"], button:not([type])').forEach(function (b) {
      if (busy) {
        if (!b.disabled) {
          b.disabled = true;
          b.setAttribute("data-rd-nav-disabled", "");
        }
      } else if (ownGuard || b.hasAttribute("data-rd-nav-disabled")) {
        b.disabled = false;
        b.removeAttribute("data-rd-nav-disabled");
      }
    });
    if (busy) form.setAttribute("data-rd-submitting", "");
    else {
      form.removeAttribute("data-rd-submitting");
      form.removeAttribute("data-submitting");
    }
  }

  document.addEventListener("htmx:beforeRequest", function (evt) {
    var d = evt.detail;
    if (!targetsBody(d)) return;
    var elt = d.elt;
    var url = new URL(d.requestConfig.path, location.href);
    nav = {
      elt: elt,
      verb: d.requestConfig.verb,
      fromPath: location.pathname,
      samePath: url.pathname === location.pathname,
      scrollY: window.scrollY,
      openCards: Array.prototype.map.call(document.querySelectorAll("details.rd-filter-card"), function (c) {
        return c.open;
      }),
    };
    trackRequest(d.xhr);
    startProgress();
    if (elt && elt.tagName === "FORM") {
      setTimeout(function () {
        setFormBusy(elt, true);
      }, 0);
    }
  });

  document.addEventListener("htmx:afterRequest", function (evt) {
    if (!targetsBody(evt.detail)) return;
    stopIfIdle();
    var elt = evt.detail.elt;
    if (elt && elt.isConnected) setFormBusy(elt, false);
  });

  /* ---- deciding whether to swap ---------------------------------------- */

  function shellOf(html) {
    var m = /<body[^>]*\sdata-rd-shell="([^"]+)"/i.exec(html || "");
    return m ? m[1] : null;
  }

  var pendingBodyAttrs = null;

  function bodyAttributes(html) {
    var m = /<body([^>]*)>/i.exec(html || "");
    if (!m) return null;
    var tpl = document.createElement("template");
    tpl.innerHTML = "<div" + m[1] + "></div>";
    return tpl.content.firstChild.attributes;
  }

  // HX-Redirect would do a full load; follow it in place instead.
  document.addEventListener("htmx:beforeOnLoad", function (evt) {
    var xhr = evt.detail.xhr;
    var to = xhr && xhr.getResponseHeader("HX-Redirect");
    if (!to) return;
    var url = new URL(to, location.href);
    if (url.origin !== location.origin) return;
    evt.preventDefault();
    window.rdNavigate(url.href);
  });

  document.addEventListener("htmx:beforeSwap", function (evt) {
    var d = evt.detail;
    if (!targetsBody(d) || !d.shouldSwap) return;
    var xhr = d.xhr;
    var type = xhr.getResponseHeader("Content-Type") || "";
    var url = xhr.responseURL || d.pathInfo.requestPath;
    if (type.indexOf("text/html") === -1 || !shell || shellOf(d.serverResponse) !== shell) {
      d.shouldSwap = false;
      hardNavigate(url);
      return;
    }
    pendingBodyAttrs = bodyAttributes(d.serverResponse);
  });

  document.addEventListener("htmx:afterSwap", function (evt) {
    if (!targetsBody(evt.detail) || !pendingBodyAttrs) return;
    var body = document.body;
    var next = pendingBodyAttrs;
    pendingBodyAttrs = null;
    Array.prototype.slice.call(body.attributes).forEach(function (attr) {
      if (!next.getNamedItem(attr.name)) body.removeAttribute(attr.name);
    });
    Array.prototype.forEach.call(next, function (attr) {
      if (body.getAttribute(attr.name) !== attr.value) body.setAttribute(attr.name, attr.value);
    });
  });

  document.addEventListener("htmx:afterSettle", function (evt) {
    if (!targetsBody(evt.detail) || !nav) return;
    var state = nav;
    nav = null;
    var samePage = state.samePath && location.pathname === state.fromPath;

    var invalid = document.querySelector(".is-invalid, .has-error");
    if (invalid && state.verb !== "get") {
      invalid.scrollIntoView({ block: "center" });
      return;
    }
    if (!samePage) {
      window.scrollTo(0, 0);
      return;
    }
    document.querySelectorAll("details.rd-filter-card").forEach(function (card, i) {
      if (state.openCards[i]) card.open = true;
    });
    window.scrollTo(0, state.scrollY);
    var live = document.querySelector("[data-rd-live]");
    if (state.elt && state.elt.closest && state.elt.closest(".pagination") && live && live.getBoundingClientRect().top < 0) {
      live.scrollIntoView({ block: "start" });
    }
  });

  /* ---- failures --------------------------------------------------------- */

  function failureMessage(key, fallback) {
    return document.body.getAttribute(key) || fallback;
  }

  document.addEventListener("htmx:responseError", function (evt) {
    var d = evt.detail;
    if (!targetsBody(d)) return;
    stopIfIdle();
    if (d.requestConfig && d.requestConfig.verb === "get") {
      hardNavigate(d.xhr.responseURL || d.pathInfo.requestPath);
      return;
    }
    if (window.rdToast) window.rdToast(failureMessage("data-rd-server-error", "Server error."), "error");
  });

  document.addEventListener("htmx:sendError", function (evt) {
    if (!targetsBody(evt.detail)) return;
    stopIfIdle();
    if (window.rdToast) window.rdToast(failureMessage("data-rd-network-error", "Network error."), "error");
  });

  // Failures that never reach afterRequest (a throwing swap) or never go
  // through a request at all (history restore) must not leave the bar or
  // the dimmed content behind.
  ["htmx:onLoadError", "htmx:swapError", "htmx:sendAbort", "htmx:timeout"].forEach(function (name) {
    document.addEventListener(name, function (evt) {
      if (targetsBody(evt.detail)) stopIfIdle();
    });
  });

  // Back/forward: htmx restores a cached snapshot or fetches it itself
  // (no htmx:beforeRequest), so the history events drive the bar here.
  document.addEventListener("htmx:historyCacheMiss", function (evt) {
    trackRequest(evt.detail && evt.detail.xhr);
    startProgress();
  });
  ["htmx:historyRestore", "htmx:historyCacheMissError", "htmx:historyCacheMissLoadError"].forEach(function (name) {
    document.addEventListener(name, stopProgress);
  });

  // The snapshot htmx stores for back/forward is taken just before the swap;
  // it must not contain the dimmed state.
  document.addEventListener("htmx:beforeHistorySave", function () {
    document.querySelectorAll("[data-rd-live].is-live-loading").forEach(function (live) {
      live.classList.remove("is-live-loading");
    });
  });

  // Restored from the browser's back/forward cache: the frozen DOM may still
  // carry a running bar / dimmed content.
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    inflight = [];
    stopProgress();
  });

  window.addEventListener("beforeunload", function (e) {
    if (!document.querySelector("form.is-dirty:not([data-rd-submitting])")) return;
    e.preventDefault();
    e.returnValue = "";
  });
})();
