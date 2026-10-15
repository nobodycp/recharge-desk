// Plain-node tests for the mobile drawer in static/js/nav-toggle.js
// (fake DOM + matchMedia, loaded in a vm). Run from anywhere:
//   node tests/js/nav_toggle.test.js
// No dependencies; exits non-zero on failure.
const fs = require("fs"), vm = require("vm"), path = require("path"), assert = require("assert");
const src = fs.readFileSync(path.join(__dirname, "..", "..", "static", "js", "nav-toggle.js"), "utf8");

function makeEl(name, matches) {
  const el = {
    name, attrs: {}, classes: new Set(), focused: false, parent: null, children: [],
    classList: {
      add: (c) => el.classes.add(c),
      remove: (c) => el.classes.delete(c),
      contains: (c) => el.classes.has(c),
      toggle(c, force) {
        const on = force === undefined ? !el.classes.has(c) : !!force;
        on ? el.classes.add(c) : el.classes.delete(c);
        return on;
      },
    },
    setAttribute(k, v) { el.attrs[k] = String(v); },
    getAttribute(k) { return k in el.attrs ? el.attrs[k] : null; },
    removeAttribute(k) { delete el.attrs[k]; },
    hasAttribute(k) { return k in el.attrs; },
    focus() { el.focused = true; env.focusLog.push(name); },
    closest(sel) {
      for (let n = el; n; n = n.parent) if (matches.some((m) => sel.includes(m) && n.matchSel === m)) return n;
      return null;
    },
  };
  return el;
}

let env;
function setup({ mobile = true, withToggle = true } = {}) {
  env = { focusLog: [], docListeners: {}, mqListeners: [], mq: { matches: mobile } };
  const sels = ["[data-rd-nav-toggle]", "[data-rd-nav-backdrop]", "[data-rd-nav-panel]"];
  const panel = makeEl("panel", sels); panel.matchSel = "[data-rd-nav-panel]";
  const backdrop = makeEl("backdrop", sels); backdrop.matchSel = "[data-rd-nav-backdrop]";
  const toggle = makeEl("toggle", sels); toggle.matchSel = "[data-rd-nav-toggle]";
  const inner = makeEl("inner-link", sels); inner.parent = panel;
  const body = {
    querySelector(s) {
      if (s === "[data-rd-nav-panel]") return panel;
      if (s === "[data-rd-nav-backdrop]") return backdrop;
      if (s === "[data-rd-nav-toggle]") return withToggle ? toggle : null;
      return null;
    },
  };
  const document = {
    querySelector: (s) => (s === "[data-rd-nav]" ? body : null),
    addEventListener(type, fn) { (env.docListeners[type] = env.docListeners[type] || []).push(fn); },
  };
  const window = {
    matchMedia: (q) => {
      env.query = q;
      return { get matches() { return env.mq.matches; }, addEventListener: (t, fn) => env.mqListeners.push({ t, fn }) };
    },
  };
  env.panel = panel; env.backdrop = backdrop; env.toggle = toggle; env.inner = inner;
  vm.runInContext(src, vm.createContext({ document, window }));
  return env;
}

function fire(type, ev) { (env.docListeners[type] || []).forEach((fn) => fn(ev)); }
const click = (target) => fire("click", { target });
const key = (k) => fire("keydown", { key: k });
function setMobile(v) {
  env.mq.matches = v;
  env.mqListeners.filter((l) => l.t === "change").forEach((l) => l.fn({ matches: v }));
}

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("breakpoint query matches the 992px CSS breakpoint", () => {
  setup();
  assert.strictEqual(env.query, "(max-width: 991.98px)");
});

test("closed panel is inert below 992px at load", () => {
  setup({ mobile: true });
  assert.ok(env.panel.hasAttribute("inert"));
});

test("closed panel is never inert at >= 992px", () => {
  setup({ mobile: false });
  assert.ok(!env.panel.hasAttribute("inert"));
  click(env.toggle);
  assert.ok(!env.panel.hasAttribute("inert"));
  click(env.backdrop);
  assert.ok(!env.panel.hasAttribute("inert"));
});

test("opening removes inert, closing restores it; aria-expanded follows", () => {
  setup({ mobile: true });
  click(env.toggle);
  assert.ok(env.panel.classes.has("is-open"));
  assert.ok(!env.panel.hasAttribute("inert"));
  assert.ok(env.backdrop.classes.has("is-visible"));
  assert.strictEqual(env.toggle.getAttribute("aria-expanded"), "true");
  click(env.toggle);
  assert.ok(!env.panel.classes.has("is-open"));
  assert.ok(env.panel.hasAttribute("inert"));
  assert.strictEqual(env.toggle.getAttribute("aria-expanded"), "false");
});

test("Esc closes an open panel and returns focus to the toggle", () => {
  setup({ mobile: true });
  click(env.toggle);
  env.toggle.focused = false;
  key("Escape");
  assert.ok(!env.panel.classes.has("is-open"));
  assert.ok(env.panel.hasAttribute("inert"));
  assert.ok(env.toggle.focused, "focus should return to the toggle");
});

test("Esc with the panel closed does nothing (no focus theft)", () => {
  setup({ mobile: true });
  key("Escape");
  assert.ok(!env.toggle.focused);
  assert.ok(env.panel.hasAttribute("inert"));
});

test("backdrop click closes the panel", () => {
  setup({ mobile: true });
  click(env.toggle);
  click(env.backdrop);
  assert.ok(!env.panel.classes.has("is-open"));
  assert.ok(!env.backdrop.classes.has("is-visible"));
  assert.ok(env.panel.hasAttribute("inert"));
});

test("crossing the breakpoint: desktop drops inert, back to mobile restores it", () => {
  setup({ mobile: true });
  assert.ok(env.panel.hasAttribute("inert"));
  setMobile(false);
  assert.ok(!env.panel.hasAttribute("inert"));
  setMobile(true);
  assert.ok(env.panel.hasAttribute("inert"));
});

test("an open drawer is closed when resizing up to desktop", () => {
  setup({ mobile: true });
  click(env.toggle);
  setMobile(false);
  assert.ok(!env.panel.classes.has("is-open"));
  assert.ok(!env.panel.hasAttribute("inert"));
  setMobile(true);
  assert.ok(env.panel.hasAttribute("inert"), "stays closed + inert after returning to mobile");
});

test("a boosted body swap (fresh closed panel) is re-synced on htmx:load", () => {
  setup({ mobile: true });
  env.panel.removeAttribute("inert"); // as a freshly swapped-in server panel would be
  fire("htmx:load", { target: env.panel });
  assert.ok(env.panel.hasAttribute("inert"));
  setup({ mobile: false });
  fire("htmx:load", { target: env.panel });
  assert.ok(!env.panel.hasAttribute("inert"));
});

test("missing toggle does not throw", () => {
  setup({ mobile: true, withToggle: false });
  click(env.backdrop);
  key("Escape");
});

let failed = 0;
for (const [name, fn] of tests) {
  try { fn(); console.log("ok   - " + name); }
  catch (e) { failed++; console.log("FAIL - " + name + "\n       " + (e && e.message)); }
}
console.log(`${tests.length - failed}/${tests.length} passed`);
process.exit(failed ? 1 : 0);
