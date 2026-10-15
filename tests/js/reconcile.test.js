// Plain-node tests for static/js/reconcile.js (tabs + number search on the reconcile pages).
// Run from anywhere:
//   node tests/js/reconcile.test.js
// No dependencies; a tiny DOM stub stands in for the page. Exits non-zero on failure.
const path = require("path");
const { normalizeQuery, initReconcile } = require(path.join(__dirname, "..", "..", "static", "js", "reconcile.js"));

let fail = 0;
const check = (name, cond, extra) => {
  console.log((cond ? "PASS " : "FAIL ") + name, cond ? "" : JSON.stringify(extra));
  if (!cond) fail++;
};

// ---- minimal DOM stub ------------------------------------------------------
class El {
  constructor(attrs, children) {
    this.attrs = Object.assign({}, attrs || {});
    this.children = children || [];
    this.hidden = "hidden" in this.attrs;
    delete this.attrs.hidden;
    this.value = "";
    this.textContent = "";
    this.listeners = {};
    this.listenerCalls = 0;
    this.focused = 0;
    this.ownerDocument = { documentElement: { getAttribute: () => "ltr" } };
    const set = new Set();
    this.classList = {
      toggle: (c, on) => {
        const want = on === undefined ? !set.has(c) : !!on;
        if (want) set.add(c); else set.delete(c);
      },
      contains: (c) => set.has(c),
    };
  }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
  hasAttribute(k) { return k in this.attrs; }
  addEventListener(type, fn) { this.listenerCalls++; (this.listeners[type] = this.listeners[type] || []).push(fn); }
  focus() { this.focused++; }
  fire(type, evt) {
    const e = Object.assign({ target: this, preventDefault() { this.prevented = true; } }, evt || {});
    (this.listeners[type] || []).slice().forEach((fn) => fn.call(this, e));
    return e;
  }
  descendants() {
    const out = [];
    const walk = (n) => n.children.forEach((c) => { out.push(c); walk(c); });
    walk(this);
    return out;
  }
  matches(sel) {
    const m = /^(?:([a-z]+))?\[([\w-]+)(?:="([^"]*)")?\]$/.exec(sel.trim());
    if (!m) throw new Error("stub selector not supported: " + sel);
    if (m[1] && this.tag !== m[1]) return false;
    if (!(m[2] in this.attrs)) return false;
    return m[3] === undefined || this.attrs[m[2]] === m[3];
  }
  querySelectorAll(sel) {
    const parts = sel.split(",");
    return this.descendants().filter((n) => parts.some((p) => n.matches(p)));
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
const el = (tag, attrs, children) => { const n = new El(attrs, children); n.tag = tag; return n; };
const row = (phone) => el("li", { "data-rd-rec-row": "", "data-phone": phone });
const group = (rows, attrs) => el("section", Object.assign({ "data-rd-rec-group": "" }, attrs || {}), rows);

const PHONES = {
  missing: "0521000001", other: "0521000007", extra: "0522000002",
  higher: "0531000003", lower: "0531000004", settlement: "0541000005", matched: "0541000006",
};

function makeRoot() {
  const input = el("input", { "data-rd-rec-search": "" });
  const count = el("p", { "data-rd-rec-count": "", "data-one": "One number matches", "data-many": "__n__ numbers match" });
  const empty = el("div", { "data-rd-rec-empty": "", hidden: "" });
  const rows = {};
  for (const k of Object.keys(PHONES)) rows[k] = row(PHONES[k]);
  const missing = group([rows.missing, group([rows.other])], { "data-rd-rec-section": "missing" });
  const extra = group([rows.extra], { "data-rd-rec-section": "extra" });
  const tabs = ["mismatch", "settlement", "matched"].map((id, i) =>
    el("button", {
      "data-rd-rec-tab": id, role: "tab", "aria-controls": "p-" + id,
      "aria-selected": i === 0 ? "true" : "false", tabindex: i === 0 ? "0" : "-1",
    }));
  const panels = {
    mismatch: el("div", { "data-rd-rec-panel": "mismatch", role: "tabpanel", id: "p-mismatch" }, [group([rows.higher, rows.lower])]),
    settlement: el("div", { "data-rd-rec-panel": "settlement", role: "tabpanel", id: "p-settlement", hidden: "" }, [group([rows.settlement])]),
    matched: el("div", { "data-rd-rec-panel": "matched", role: "tabpanel", id: "p-matched", hidden: "" }, [group([rows.matched])]),
  };
  const root = el("div", { "data-rd-reconcile": "" },
    [input, count, empty, missing, extra].concat(tabs, Object.values(panels)));
  return { root, input, count, empty, rows, missing, extra, tabs, panels };
}

const visibleRows = (page) => Object.keys(page.rows).filter((k) => !page.rows[k].hidden);
const selected = (page) => page.tabs.filter((t) => t.getAttribute("aria-selected") === "true").map((t) => t.getAttribute("data-rd-rec-tab"));
const shownPanels = (page) => Object.keys(page.panels).filter((k) => !page.panels[k].hidden);
const type = (page, text) => { page.input.value = text; page.input.fire("input"); };

// ---- normalizeQuery ---------------------------------------------------------
check("normalize strips spaces", normalizeQuery("599 123") === "599123", normalizeQuery("599 123"));
check("normalize 972 -> 0", normalizeQuery("972599123456") === "0599123456", normalizeQuery("972599123456"));
check("normalize +972 with separators", normalizeQuery("+972 59-912-3456") === "0599123456", normalizeQuery("+972 59-912-3456"));
check("normalize 00972", normalizeQuery("00972599123456") === "0599123456", normalizeQuery("00972599123456"));
check("normalize 9-digit 5.. -> 05..", normalizeQuery("599123456") === "0599123456", normalizeQuery("599123456"));
check("normalize non-digits -> empty", normalizeQuery("abc") === "", normalizeQuery("abc"));
check("normalize partial stays", normalizeQuery("0599") === "0599" && normalizeQuery("599") === "599" && normalizeQuery("97") === "97", [normalizeQuery("0599"), normalizeQuery("599"), normalizeQuery("97")]);
check("normalize bare 972 stays", normalizeQuery("972") === "972", normalizeQuery("972"));
check("normalize arabic-indic digits", normalizeQuery("٠٥٢١٠٠٠٠٠١") === "0521000001", normalizeQuery("٠٥٢١٠٠٠٠٠١"));
check("normalize null-safe", normalizeQuery(null) === "" && normalizeQuery(undefined) === "", 0);

// ---- init -------------------------------------------------------------------
{
  const page = makeRoot();
  initReconcile(page.root);
  check("init marks root ready", page.root.hasAttribute("data-rd-rec-ready"), page.root.attrs);
  check("nothing hidden before searching", visibleRows(page).length === 7 && shownPanels(page).join() === "mismatch", [visibleRows(page), shownPanels(page)]);
}

// ---- search normalises input ------------------------------------------------
{
  const page = makeRoot();
  initReconcile(page.root);
  type(page, "972521000001");
  check("search normalises input (972…)", visibleRows(page).join() === "missing", visibleRows(page));
  type(page, "521000001");
  check("search finds 9-digit input", visibleRows(page).join() === "missing", visibleRows(page));
  type(page, "521 000-001");
  check("search ignores spaces and dashes", visibleRows(page).join() === "missing", visibleRows(page));
  check("count says one number", page.count.textContent === "One number matches", page.count.textContent);
}

// ---- matches across buckets, empty groups hidden -----------------------------
{
  const page = makeRoot();
  initReconcile(page.root);
  type(page, "0541");
  check("search shows rows from every bucket", visibleRows(page).join() === "settlement,matched", visibleRows(page));
  check("search reveals hidden panels", shownPanels(page).join() === "settlement,matched", shownPanels(page));
  check("search hides groups without a match", page.missing.hidden && page.extra.hidden && page.panels.mismatch.hidden, [page.missing.hidden, page.extra.hidden, page.panels.mismatch.hidden]);
  check("count says many", page.count.textContent === "2 numbers match", page.count.textContent);
  check("empty block stays hidden with matches", page.empty.hidden === true, page.empty.hidden);
  type(page, "0521");
  check("action sections visible when they match", !page.missing.hidden && page.extra.hidden === true, [page.missing.hidden, page.extra.hidden]);
  check("nested group follows its rows", visibleRows(page).join() === "missing,other", visibleRows(page));
  check("search without 0 finds a phone with 0", (type(page, "52100"), visibleRows(page).join() === "missing,other"), visibleRows(page));
  check("tabs inert while searching", page.tabs.every((t) => t.getAttribute("aria-disabled") === "true"), page.tabs.map((t) => t.attrs));
  check("root marks searching", page.root.hasAttribute("data-rd-rec-searching"), page.root.attrs);
}

// ---- clearing restores the previous tab --------------------------------------
{
  const page = makeRoot();
  initReconcile(page.root);
  page.tabs[2].fire("click");
  check("click switches panel", shownPanels(page).join() === "matched" && selected(page).join() === "matched", [shownPanels(page), selected(page)]);
  type(page, "0521");
  type(page, "");
  check("clearing restores the tab that was active", shownPanels(page).join() === "matched" && selected(page).join() === "matched", [shownPanels(page), selected(page)]);
  check("clearing shows every row and group again", visibleRows(page).length === 7 && !page.missing.hidden && !page.extra.hidden, visibleRows(page));
  check("clearing re-enables tabs", page.tabs.every((t) => !t.hasAttribute("aria-disabled")) && !page.root.hasAttribute("data-rd-rec-searching"), page.tabs.map((t) => t.attrs));
  check("clearing empties the live count", page.count.textContent === "", page.count.textContent);
  check("roving tabindex follows the selection", page.tabs.map((t) => t.getAttribute("tabindex")).join() === "-1,-1,0", page.tabs.map((t) => t.getAttribute("tabindex")));
}

// ---- search 'search' event (the clear button of type=search) ------------------
{
  const page = makeRoot();
  initReconcile(page.root);
  type(page, "0522");
  page.input.value = "";
  page.input.fire("search");
  check("search event clears the filter", visibleRows(page).length === 7 && shownPanels(page).join() === "mismatch", [visibleRows(page), shownPanels(page)]);
}

// ---- no match ----------------------------------------------------------------
{
  const page = makeRoot();
  initReconcile(page.root);
  type(page, "999999");
  check("no match shows the empty block", page.empty.hidden === false, page.empty.hidden);
  check("no match hides every row", visibleRows(page).length === 0, visibleRows(page));
  check("no match clears the count", page.count.textContent === "", page.count.textContent);
  type(page, "0522");
  check("empty block hides again on a match", page.empty.hidden === true, page.empty.hidden);
  type(page, "abc");
  check("non-digit query counts as empty search", visibleRows(page).length === 7 && page.empty.hidden === true, visibleRows(page));
}

// ---- tabs ---------------------------------------------------------------------
{
  const page = makeRoot();
  initReconcile(page.root);
  const down = (i, key) => page.tabs[i].fire("keydown", { key });
  down(0, "ArrowRight");
  check("arrow keys move tabs (ltr: right = next)", selected(page).join() === "settlement" && shownPanels(page).join() === "settlement", [selected(page), shownPanels(page)]);
  check("arrow keys move focus", page.tabs[1].focused === 1, page.tabs[1].focused);
  down(1, "ArrowLeft");
  check("arrow left goes back", selected(page).join() === "mismatch", selected(page));
  down(0, "ArrowLeft");
  check("arrow keys wrap around", selected(page).join() === "matched", selected(page));
  down(2, "End");
  down(2, "Home");
  check("Home and End jump", selected(page).join() === "mismatch", selected(page));
  const before = selected(page).join();
  type(page, "0521");
  down(0, "ArrowRight");
  page.tabs[2].fire("click");
  check("tabs ignore input while searching", selected(page).join() === before, selected(page));
}
{
  const page = makeRoot();
  page.root.ownerDocument = { documentElement: { getAttribute: () => "rtl" } };
  initReconcile(page.root);
  page.tabs[0].fire("keydown", { key: "ArrowLeft" });
  check("rtl: left arrow = next tab", selected(page).join() === "settlement", selected(page));
  page.tabs[1].fire("keydown", { key: "ArrowRight" });
  check("rtl: right arrow = previous tab", selected(page).join() === "mismatch", selected(page));
}

// ---- init twice ---------------------------------------------------------------
{
  const page = makeRoot();
  initReconcile(page.root);
  const calls = () => page.input.listenerCalls + page.tabs.reduce((n, t) => n + t.listenerCalls, 0) + page.root.listenerCalls;
  const first = calls();
  initReconcile(page.root);
  check("init twice binds once per root", calls() === first && first > 0, [first, calls()]);
  type(page, "0522");
  check("a second init does not double the handlers", visibleRows(page).join() === "extra", visibleRows(page));
  const other = makeRoot();
  initReconcile(other.root);
  check("another root still binds", other.root.hasAttribute("data-rd-rec-ready") && other.input.listenerCalls > 0, other.root.attrs);
}

// ---- degraded pages -------------------------------------------------------------
{
  const bare = el("div", { "data-rd-reconcile": "" });
  let ok = true;
  try { initReconcile(bare); } catch (e) { ok = false; }
  check("a root without search or tabs does not throw", ok, 0);
}

process.exit(fail ? 1 : 0);
