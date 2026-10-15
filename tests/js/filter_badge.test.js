// Plain-node test for the filter badge in static/js/data-ui.js (fake DOM, loaded in a vm).
//   node tests/js/filter_badge.test.js
// The badge must count the form's own fields only: sort/page noise and inputs
// marked data-rd-filter-keep (re-posted by rd_filter_card keep="...") never count.
const fs = require("fs"), vm = require("vm"), path = require("path"), assert = require("assert");
const src = fs.readFileSync(path.join(__dirname, "..", "..", "static", "js", "data-ui.js"), "utf8");

function input(name, value, { type = "text", keep = false, checked = false } = {}) {
  return { name, value, type, checked, hasAttribute: (a) => keep && a === "data-rd-filter-keep" };
}

function badgeFor(inputs) {
  const badge = { textContent: "", style: { display: "none" } };
  const form = { querySelectorAll: () => inputs };
  const card = {
    hasAttribute: () => false,
    querySelector: (s) => (s === "form" ? form : s === "[data-rd-filter-count]" ? badge : null),
  };
  const body = {
    addEventListener() {},
    querySelectorAll: (s) => (s === ".rd-filter-card" ? [card] : []),
  };
  const document = {
    body, readyState: "complete",
    addEventListener() {}, querySelector: () => null, querySelectorAll: () => [],
  };
  const window = { addEventListener() {} };
  vm.runInContext(src, vm.createContext({ document, window, console }));
  return badge;
}

const tests = {
  "counts only filled form fields"() {
    const b = badgeFor([input("q", "ab"), input("status", ""), input("sort", "name"), input("page", "2")]);
    assert.strictEqual(b.textContent, "1");
    assert.strictEqual(b.style.display, "");
  },
  "kept hidden inputs are ignored"() {
    const b = badgeFor([
      input("q", "ab"),
      input("tab", "payments", { type: "hidden", keep: true }),
      input("payments_sort", "ref", { type: "hidden", keep: true }),
    ]);
    assert.strictEqual(b.textContent, "1");
  },
  "only kept inputs leaves the badge hidden"() {
    const b = badgeFor([input("tab", "payments", { type: "hidden", keep: true }), input("q", "")]);
    assert.strictEqual(b.textContent, "0");
    assert.strictEqual(b.style.display, "none");
  },
  "an unmarked hidden input still counts"() {
    const b = badgeFor([input("tab", "payments", { type: "hidden" })]);
    assert.strictEqual(b.textContent, "1");
  },
  "checked checkboxes count, unchecked do not"() {
    const b = badgeFor([input("paid", "1", { type: "checkbox", checked: true }), input("late", "1", { type: "checkbox" })]);
    assert.strictEqual(b.textContent, "1");
  },
};

let failed = 0;
for (const [name, fn] of Object.entries(tests)) {
  try { fn(); console.log("ok -", name); } catch (e) { failed++; console.error("FAIL -", name, "\n", e.message); }
}
process.exit(failed ? 1 : 0);
