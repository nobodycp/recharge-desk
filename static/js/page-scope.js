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

  function leavePage() {
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
