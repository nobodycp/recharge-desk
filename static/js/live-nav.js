/**
 * Live in-page navigation for management screens.
 *
 * Links and GET forms that point at the *current* path (filters, tabs,
 * sorting, pagination, period switches) fetch the page in the background
 * and swap only the main content, keeping the URL and back button in sync.
 *
 * Left alone (normal navigation): other paths, anything already driven by
 * htmx, downloads, new tabs, pages whose content runs inline scripts, and
 * screens with unsaved form changes (so the leave-page warning still fires).
 */
(function () {
  var root = document.querySelector("[data-rd-live]");
  if (!root || !window.fetch || !window.DOMParser) return;

  var HTMX_ATTRS = "[hx-get],[hx-post],[hx-put],[hx-delete],[hx-boost],[hx-target]";
  var inflight = null;

  function eligibleRoot() {
    return !root.querySelector("script") && !root.querySelector("form.is-dirty");
  }

  function samePage(url) {
    return url.origin === location.origin && url.pathname === location.pathname;
  }

  function focusKey(el) {
    if (!el || !root.contains(el)) return null;
    if (el.id) return "#" + CSS.escape(el.id);
    if (el.name) return '[name="' + CSS.escape(el.name) + '"]';
    return null;
  }

  function openFilterCards() {
    return Array.prototype.map.call(root.querySelectorAll("details.rd-filter-card"), function (d) {
      return d.open;
    });
  }

  function load(url, opts) {
    opts = opts || {};
    if (inflight) inflight.abort();
    var ctrl = new AbortController();
    inflight = ctrl;
    var keepOpen = openFilterCards();
    var refocus = focusKey(document.activeElement);
    root.classList.add("is-live-loading");

    return fetch(url.href, {
      credentials: "same-origin",
      headers: { "X-Requested-With": "rd-live" },
      signal: ctrl.signal,
    })
      .then(function (res) {
        var type = res.headers.get("Content-Type") || "";
        var landed = new URL(res.url);
        if (!res.ok || type.indexOf("text/html") === -1 || !samePage(landed)) {
          throw new Error("fallback");
        }
        return res.text();
      })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var next = doc.querySelector("[data-rd-live]");
        if (!next || next.querySelector("script")) throw new Error("fallback");

        root.innerHTML = next.innerHTML;
        if (doc.title) document.title = doc.title;
        if (opts.push !== false) history.pushState({ rdLive: true }, "", url.href);

        root.querySelectorAll("details.rd-filter-card").forEach(function (d, i) {
          if (keepOpen[i]) d.open = true;
        });
        if (window.htmx) window.htmx.process(root);
        document.dispatchEvent(new CustomEvent("rd:content-swapped", { detail: { root: root } }));

        if (refocus) {
          var el = root.querySelector(refocus);
          if (el && el.focus) {
            el.focus({ preventScroll: true });
            if (typeof el.value === "string" && el.setSelectionRange && /^(text|search)$/.test(el.type)) {
              el.setSelectionRange(el.value.length, el.value.length);
            }
          }
        }
        if (opts.scrollTop && root.getBoundingClientRect().top < 0) {
          root.scrollIntoView({ block: "start" });
        }
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") return;
        if (opts.push === false) window.location.reload();
        else window.location.assign(url.href);
      })
      .finally(function () {
        if (inflight === ctrl) {
          inflight = null;
          root.classList.remove("is-live-loading");
        }
      });
  }

  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest("a[href]");
    if (!a || !root.contains(a)) return;
    if (a.target || a.hasAttribute("download") || a.closest(HTMX_ATTRS) || a.closest("[data-rd-live-skip]")) return;
    var raw = a.getAttribute("href");
    if (!raw || raw.charAt(0) === "#" || /^(javascript|mailto|tel):/i.test(raw)) return;
    var url = new URL(a.href, location.href);
    if (!samePage(url) || url.hash) return;
    if (!eligibleRoot()) return;
    e.preventDefault();
    load(url, { scrollTop: !!a.closest(".pagination") });
  });

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (e.defaultPrevented || !root.contains(form)) return;
    if ((form.getAttribute("method") || "get").toLowerCase() !== "get") return;
    if (form.target || form.closest(HTMX_ATTRS) || form.closest("[data-rd-live-skip]")) return;
    var url = new URL(form.getAttribute("action") || location.pathname, location.href);
    if (!samePage(url)) return;
    if (!eligibleRoot()) return;

    var data = new FormData(form);
    if (e.submitter && e.submitter.name) data.append(e.submitter.name, e.submitter.value);
    var params = new URLSearchParams();
    data.forEach(function (value, key) {
      if (typeof value === "string") params.append(key, value);
    });
    url.search = params.toString();
    url.hash = "";
    e.preventDefault();
    load(url);
  });

  if (!history.state) history.replaceState({ rdLive: true }, "", location.href);

  window.addEventListener("popstate", function (e) {
    if (!e.state || !e.state.rdLive) return;
    load(new URL(location.href), { push: false });
  });
})();
