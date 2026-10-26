// Touch taps on the confirm dialog. A cancelled pointerdown does not emit a
// click, so Cancel used to leave the dialog up and the page underneath frozen.
//   node tests/js/rd_confirm.test.js
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const assert = require("assert");

const src = fs.readFileSync(path.join(__dirname, "..", "..", "static", "js", "rd-confirm.js"), "utf8");

function makeEl(tag) {
  const node = {
    tagName: tag.toUpperCase(),
    children: [],
    parent: null,
    attrs: {},
    listeners: {},
    classes: new Set(),
    hidden: false,
    textContent: "",
    style: {},
    open: false,
    isConnected: false,
  };
  node.classList = {
    add(c) { node.classes.add(c); },
    remove() { Array.prototype.forEach.call(arguments, (c) => node.classes.delete(c)); },
    toggle(c, force) {
      const on = force === undefined ? !node.classes.has(c) : !!force;
      if (on) node.classes.add(c); else node.classes.delete(c);
      return on;
    },
    contains(c) { return node.classes.has(c); },
  };
  Object.defineProperty(node, "className", {
    get() { return Array.from(node.classes).join(" "); },
    set(v) { node.classes = new Set(String(v || "").split(/\s+/).filter(Boolean)); },
  });
  node.setAttribute = (k, v) => { node.attrs[k] = String(v); };
  node.getAttribute = (k) => (k in node.attrs ? node.attrs[k] : null);
  node.removeAttribute = (k) => { delete node.attrs[k]; };
  node.hasAttribute = (k) => k in node.attrs;
  node.addEventListener = (type, fn) => {
    (node.listeners[type] = node.listeners[type] || []).push(fn);
  };
  node.dispatchEvent = (ev) => {
    ev.target = node;
    let prevented = false;
    const prev = ev.preventDefault;
    ev.preventDefault = () => { prevented = true; if (prev) prev.call(ev); };
    ev.stopPropagation = () => {};
    (node.listeners[ev.type] || []).slice().forEach((fn) => fn.call(node, ev));
    return !prevented;
  };
  node.querySelector = (sel) => {
    const all = [];
    (function walk(n) { all.push(n); n.children.forEach(walk); })(node);
    return all.find((n) => {
      if (sel.charAt(0) === ".") return n.classes.has(sel.slice(1));
      if (sel.charAt(0) === "[") return n.hasAttribute(sel.slice(1, -1));
      return false;
    }) || null;
  };
  node.focus = () => {};
  node.appendChild = (child) => {
    child.parent = node;
    node.children.push(child);
    const mark = (n, on) => { n.isConnected = on; n.children.forEach((c) => mark(c, on)); };
    mark(child, node.isConnected);
    return child;
  };
  node.remove = () => {
    if (!node.parent) return;
    node.parent.children = node.parent.children.filter((c) => c !== node);
    node.parent = null;
    node.isConnected = false;
  };
  Object.defineProperty(node, "parentNode", { get() { return node.parent; } });
  Object.defineProperty(node, "innerHTML", {
    set(value) {
      if (tag !== "dialog") return;
      node.children = [];
      const icon = makeEl("div");
      icon.className = "rd-confirm__icon";
      icon.setAttribute("data-rd-confirm-icon", "");
      const text = makeEl("p");
      text.className = "rd-confirm__text";
      const actions = makeEl("div");
      actions.className = "rd-confirm__actions";
      const cancel = makeEl("button");
      cancel.setAttribute("data-rd-confirm-cancel", "");
      const ok = makeEl("button");
      ok.setAttribute("data-rd-confirm-ok", "");
      ok.className = "btn btn-primary";
      actions.appendChild(cancel);
      actions.appendChild(ok);
      node.appendChild(icon);
      node.appendChild(text);
      node.appendChild(actions);
    },
  });
  node.showModal = () => { node.open = true; };
  node.close = () => {
    node.open = false;
    (node.listeners.close || []).forEach((fn) => fn.call(node, { type: "close" }));
  };
  return node;
}

function boot() {
  const body = makeEl("body");
  body.isConnected = true;
  const docListeners = {};
  const document = {
    body,
    activeElement: null,
    scrollingElement: { scrollTop: 0 },
    documentElement: { scrollTop: 0 },
    createElement: (tag) => makeEl(tag),
    addEventListener(type, fn) {
      (docListeners[type] = docListeners[type] || []).push(fn);
    },
    dispatchEvent(ev) {
      (docListeners[ev.type] || []).slice().forEach((fn) => fn(ev));
    },
  };
  const window = {
    document,
    confirm() { return true; },
    setTimeout(fn) { fn(); return 0; },
    requestAnimationFrame(fn) { fn(); },
  };
  vm.runInContext(src, vm.createContext({
    document,
    window,
    setTimeout: window.setTimeout,
    requestAnimationFrame: window.requestAnimationFrame,
  }));
  return { window, document };
}

function pointer(type, pointerType, id) {
  return { type, pointerType, pointerId: id, button: 0, bubbles: true, cancelable: true };
}

function openDialog(window) {
  let settled = null;
  window.rdConfirm("شحن؟").then((ok) => { settled = ok; });
  const dialog = documentBody(window).children.find((n) => n.tagName === "DIALOG");
  assert.ok(dialog && dialog.open, "dialog opens");
  return { dialog, result: () => settled };
}

function documentBody(window) {
  return window.document.body;
}

function fireBeforeSwap(window, detail) {
  window.document.dispatchEvent({ type: "htmx:beforeSwap", detail: detail });
}

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("a touch tap on Cancel closes the dialog without a click", () => {
  const { window } = boot();
  const { dialog } = openDialog(window);
  const cancel = dialog.querySelector("[data-rd-confirm-cancel]");
  cancel.dispatchEvent(pointer("pointerdown", "touch", 1));
  cancel.dispatchEvent(pointer("pointerup", "touch", 1));
  assert.strictEqual(dialog.open, false);
});

test("a mouse press does not confirm until click", () => {
  const { window } = boot();
  const { dialog } = openDialog(window);
  const ok = dialog.querySelector("[data-rd-confirm-ok]");
  ok.dispatchEvent(pointer("pointerdown", "mouse", 2));
  ok.dispatchEvent(pointer("pointerup", "mouse", 2));
  assert.strictEqual(dialog.open, true);
  ok.dispatchEvent({ type: "click", button: 0, bubbles: true, cancelable: true });
  assert.strictEqual(dialog.open, false);
});

test("lifting a touch outside Cancel leaves the dialog open", () => {
  const { window } = boot();
  const { dialog } = openDialog(window);
  const cancel = dialog.querySelector("[data-rd-confirm-cancel]");
  cancel.dispatchEvent(pointer("pointerdown", "touch", 3));
  cancel.dispatchEvent(pointer("pointercancel", "touch", 3));
  cancel.dispatchEvent(pointer("pointerup", "touch", 9));
  assert.strictEqual(dialog.open, true);
});

// A "loading" notice (e.g. the sales charge poll) has no Cancel and is only
// ever dismissed by its own page's code. Leaving that page (back/forward)
// before it resolves used to leave `pending` set forever, so every later
// rdConfirm() on every later page just queued silently — the next Charge
// button looked dead: no confirm, no cancel, nothing.
test("a stuck loading notice does not block confirms on the next page", () => {
  const { window } = boot();
  window.rdConfirm("Charging…", { notice: true, loading: true });
  const stuck = documentBody(window).children.find((n) => n.tagName === "DIALOG");
  assert.strictEqual(stuck.open, true, "the loading notice opened with no way to dismiss itself");

  fireBeforeSwap(window, { shouldSwap: true, target: documentBody(window) });
  assert.strictEqual(stuck.open, false, "leaving the page force-closes the orphaned notice");

  window.rdConfirm("Charge this number?");
  assert.strictEqual(stuck.open, true, "the next page's confirm still opens instead of queuing silently");
  const ok = stuck.querySelector("[data-rd-confirm-ok]");
  ok.dispatchEvent({ type: "click", button: 0, bubbles: true, cancelable: true });
  assert.strictEqual(stuck.open, false);
});

test("a partial (non-body) swap does not cancel an open confirm", () => {
  const { window } = boot();
  const { dialog, result } = openDialog(window);
  var otherTarget = { tagName: "DIV" };
  fireBeforeSwap(window, { shouldSwap: true, target: otherTarget });
  assert.strictEqual(dialog.open, true);
  assert.strictEqual(result(), null);
});

let failed = 0;
tests.forEach(([name, fn]) => {
  try {
    fn();
    console.log("ok", name);
  } catch (err) {
    failed += 1;
    console.error("FAIL", name);
    console.error(err);
  }
});
process.exit(failed ? 1 : 0);
