/**
 * Page-scoped globals for boosted navigation.
 *
 * Boosted navigation replaces <body> content but keeps the document, so a
 * page script that binds document / window / <body> listeners or starts a
 * setInterval would otherwise leave them running on every page after it,
 * and add another copy on each visit. Anything such a script registers —
 * while it runs, or later from inside one of its own listeners — is
 * recorded here and removed right before the next page is swapped in.
 *
 * Shell scripts live in <head>, so their listeners are never touched.
 * Must load (not deferred) before any page content.
 */
(function () {
  "use strict";

  var proto = EventTarget.prototype;
  var nativeAdd = proto.addEventListener;
  var nativeRemove = proto.removeEventListener;
  var nativeSetInterval = window.setInterval;
  var nativeClearInterval = window.clearInterval;
  var listeners = [];
  var intervals = [];
  var depth = 0;

  function pageOwned() {
    if (depth > 0) return true;
    var script = document.currentScript;
    return !!(script && document.body && document.body.contains(script));
  }

  function isGlobal(target) {
    return target === window || target === document || target === document.body;
  }

  function captureOf(options) {
    return typeof options === "boolean" ? options : !!(options && options.capture);
  }

  function find(target, type, fn, capture) {
    for (var i = listeners.length - 1; i >= 0; i--) {
      var l = listeners[i];
      if (l.target === target && l.type === type && l.fn === fn && l.capture === capture) return i;
    }
    return -1;
  }

  function owned(fn) {
    return function () {
      depth++;
      try {
        return fn.apply(this, arguments);
      } finally {
        depth--;
      }
    };
  }

  proto.addEventListener = function (type, fn, options) {
    if (!fn || !isGlobal(this) || !pageOwned()) return nativeAdd.call(this, type, fn, options);
    var capture = captureOf(options);
    if (find(this, type, fn, capture) !== -1) return undefined;
    var wrapped = typeof fn === "function" ? owned(fn) : fn;
    listeners.push({ target: this, type: type, fn: fn, wrapped: wrapped, capture: capture });
    return nativeAdd.call(this, type, wrapped, options);
  };

  proto.removeEventListener = function (type, fn, options) {
    var i = find(this, type, fn, captureOf(options));
    if (i === -1) return nativeRemove.call(this, type, fn, options);
    var l = listeners.splice(i, 1)[0];
    return nativeRemove.call(this, type, l.wrapped, options);
  };

  window.setInterval = function () {
    var id = nativeSetInterval.apply(window, arguments);
    if (pageOwned()) intervals.push(id);
    return id;
  };

  // htmx.onLoad(fn) registers a wrapper on "htmx:load" that runs for every
  // later swap, so a page script's callback must go when its page does.
  // Ownership is decided at call time; head (shell) registrations are kept.
  var loadHooks = [];

  function patchHtmx(h) {
    if (!h || typeof h.onLoad !== "function" || h.onLoad.__rdPageScope) return;
    var nativeOnLoad = h.onLoad;
    var patched = function (fn) {
      if (!pageOwned() || typeof fn !== "function") return nativeOnLoad.apply(this, arguments);
      for (var i = 0; i < loadHooks.length; i++) {
        if (loadHooks[i].fn === fn) return loadHooks[i].wrapper;
      }
      var wrapper = nativeOnLoad.apply(this, arguments);
      if (typeof wrapper === "function") loadHooks.push({ fn: fn, wrapper: wrapper });
      return wrapper;
    };
    patched.__rdPageScope = true;
    h.onLoad = patched;
  }

  // page-scope.js loads before htmx; the htmx UMD does `window.htmx = window.htmx || factory()`,
  // so catch that assignment, patch the object, then leave a plain data property behind.
  if (window.htmx) {
    patchHtmx(window.htmx);
  } else {
    Object.defineProperty(window, "htmx", {
      configurable: true,
      enumerable: true,
      get: function () {
        return undefined;
      },
      set: function (value) {
        Object.defineProperty(window, "htmx", {
          configurable: true,
          enumerable: true,
          writable: true,
          value: value,
        });
        patchHtmx(value);
      },
    });
  }

  function leavePage() {
    var hooks = loadHooks.splice(0);
    if (hooks.length && window.htmx && typeof window.htmx.off === "function") {
      hooks.forEach(function (hook) {
        window.htmx.off("htmx:load", hook.wrapper);
      });
    }
    listeners.splice(0).forEach(function (l) {
      nativeRemove.call(l.target, l.type, l.wrapped, l.capture);
    });
    intervals.splice(0).forEach(function (id) {
      nativeClearInterval.call(window, id);
    });
  }

  // On window so nav.js (on document) has already decided whether to swap.
  nativeAdd.call(window, "htmx:beforeSwap", function (evt) {
    var d = evt.detail;
    if (d && d.shouldSwap && d.target === document.body) leavePage();
  });
  nativeAdd.call(window, "htmx:historyCacheMissLoad", leavePage);
})();
