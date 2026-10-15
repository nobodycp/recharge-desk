// Plain-node test for the always-visible savebar in static/js/admin-ui.js (fake DOM, loaded in a vm).
//   node tests/js/savebar.test.js
// Calm: message "no changes", Save + Discard disabled. Dirty: message "unsaved", both enabled.
// Reset returns to calm and still fires rd:reset. Server errors start dirty.
const fs = require("fs"), vm = require("vm"), path = require("path"), assert = require("assert");
const src = fs.readFileSync(path.join(__dirname, "..", "..", "static", "js", "admin-ui.js"), "utf8");

function classList() {
  const set = new Set();
  return {
    toggle(name, on) { if (on) set.add(name); else set.delete(name); },
    contains: (name) => set.has(name),
  };
}

function setup({ forced = false } = {}) {
  const field = { name: "flag", type: "checkbox", checked: false };
  const listeners = {};
  const msg = { textContent: "", getAttribute: (a) => ({ "data-rd-msg-calm": "No unsaved changes", "data-rd-msg-dirty": "You have unsaved changes" })[a] };
  const save = { disabled: true };
  const discard = { disabled: true };
  const bar = {
    classList: classList(),
    hasAttribute: (a) => forced && a === "data-rd-savebar-force",
    querySelector: (s) => ({ "[data-rd-savebar-msg]": msg, "[data-rd-savebar-save]": save, "[data-rd-savebar-discard]": discard })[s] || null,
  };
  const events = [];
  const form = {
    elements: [field],
    classList: classList(),
    querySelector: (s) => (s === "[data-rd-savebar]" ? bar : null),
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    dispatchEvent(e) { events.push(e.type); },
    setAttribute() {},
    matches: () => false,
  };
  const document = {
    readyState: "complete",
    addEventListener() {},
    querySelectorAll: (s) => (s === "form[data-rd-dirty-form]" ? [form] : []),
    querySelector: () => null,
  };
  const window = {};
  const timers = [];
  const context = vm.createContext({
    document, window, console, CustomEvent: function (type) { this.type = type; },
    setTimeout: (fn) => timers.push(fn),
  });
  document.querySelectorAll = (s) => (s === "form[data-rd-dirty-form]" ? [form] : []);
  vm.runInContext(src, context);
  const fire = (type) => (listeners[type] || []).forEach((fn) => fn({}));
  return { field, form, bar, msg, save, discard, events, fire, flush: () => timers.splice(0).forEach((fn) => fn()) };
}

const tests = {
  "calm state: Save and Discard disabled, calm message"() {
    const t = setup();
    assert.strictEqual(t.save.disabled, true);
    assert.strictEqual(t.discard.disabled, true);
    assert.strictEqual(t.msg.textContent, "No unsaved changes");
    assert.strictEqual(t.bar.classList.contains("is-dirty"), false);
  },
  "input makes the bar dirty and enables both buttons"() {
    const t = setup();
    t.field.checked = true;
    t.fire("input");
    assert.strictEqual(t.save.disabled, false);
    assert.strictEqual(t.discard.disabled, false);
    assert.strictEqual(t.msg.textContent, "You have unsaved changes");
    assert.strictEqual(t.bar.classList.contains("is-dirty"), true);
    assert.strictEqual(t.form.classList.contains("is-dirty"), true);
  },
  "undoing the edit returns to calm"() {
    const t = setup();
    t.field.checked = true; t.fire("change");
    t.field.checked = false; t.fire("change");
    assert.strictEqual(t.save.disabled, true);
    assert.strictEqual(t.msg.textContent, "No unsaved changes");
  },
  "reset returns to calm and fires rd:reset"() {
    const t = setup();
    t.field.checked = true; t.fire("input");
    t.field.checked = false; t.fire("reset"); t.flush();
    assert.deepStrictEqual(t.events, ["rd:reset"]);
    assert.strictEqual(t.save.disabled, true);
    assert.strictEqual(t.discard.disabled, true);
    assert.strictEqual(t.bar.classList.contains("is-dirty"), false);
  },
  "server errors start dirty"() {
    const t = setup({ forced: true });
    assert.strictEqual(t.save.disabled, false);
    assert.strictEqual(t.msg.textContent, "You have unsaved changes");
  },
  "forced bar returns to calm after a reset"() {
    const t = setup({ forced: true });
    t.fire("reset"); t.flush();
    assert.strictEqual(t.save.disabled, true);
    assert.strictEqual(t.msg.textContent, "No unsaved changes");
  },
};

let failed = 0;
for (const [name, fn] of Object.entries(tests)) {
  try { fn(); console.log("ok  - " + name); } catch (e) { failed++; console.log("FAIL - " + name + "\n" + e.stack); }
}
process.exit(failed ? 1 : 0);
