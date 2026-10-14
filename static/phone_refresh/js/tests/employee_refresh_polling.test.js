// Plain-node tests for the polling logic in static/js/employee-refresh-number.js
// (stubbed DOM/fetch/timers). Run from anywhere:
//   node static/phone_refresh/js/tests/employee_refresh_polling.test.js
// No dependencies; exits non-zero on failure.
const fs = require("fs"), vm = require("vm"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "..", "..", "js", "employee-refresh-number.js"), "utf8");

const ATTRS = {
  "data-refresh-url": "/employee/sales/api/refresh-phone/",
  "data-status-url": "/employee/refresh/status/00000000-0000-0000-0000-000000000000/",
  "data-wait-seconds": "90",
  "data-msg-error-title": "خطأ",
  "data-msg-error-body": "حدث خطأ ما",
  "data-msg-working": "جارٍ التحديث…",
};

function makeEnv({ fetchImpl }) {
  const shown = [];
  const timers = [];
  const listeners = {};
  let now = 1_000_000;
  const els = {};
  const el = (id) => (els[id] = els[id] || {
    id, value: "", disabled: false, _text: "", attrs: {}, classList: { add() {}, remove() {} },
    set textContent(v) { this._text = v; if (id === "rd-refresh-result-body") shown.push({ body: v }); },
    get textContent() { return this._text; },
    setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k] || null; },
    addEventListener(type, fn) { (listeners[id + ":" + type] = listeners[id + ":" + type] || []).push(fn); },
  });
  const root = el("rd-refresh-page");
  root.getAttribute = (k) => (ATTRS[k] !== undefined ? ATTRS[k] : null);
  el("rd-refresh-phone-input"); el("rd-refresh-submit"); el("rd-refresh-result");
  el("rd-refresh-result-title"); el("rd-refresh-result-body");
  const document = {
    getElementById: (id) => (id.startsWith("rd-refresh") ? el(id) : null),
    querySelector: (s) => (s.includes("csrfmiddlewaretoken") ? { value: "tok" } : null),
  };
  const fetchCalls = [];
  const ctx = {
    document, window: { alert() {} }, console, URLSearchParams, Promise, Number, Math, encodeURIComponent, String, Date: { now: () => now },
    setTimeout: (fn, ms) => { timers.push(ms); now += ms; return setImmediate(fn); },
    fetch: (url, opts) => { fetchCalls.push({ url, method: opts.method, t: now }); return fetchImpl(url, opts, fetchCalls.length); },
  };
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  const input = el("rd-refresh-phone-input");
  const button = el("rd-refresh-submit");
  const bodyEl = el("rd-refresh-result-body");
  const titleEl = el("rd-refresh-result-title");
  // Start a refresh like a click would; resolves when the run's UI has settled.
  const click = (phone = "0529000000") => {
    input.value = phone;
    listeners["rd-refresh-submit:click"][0]();
  };
  const settle = async () => { for (let i = 0; i < 200; i++) await new Promise((r) => setImmediate(r)); };
  return { shown, timers, fetchCalls, click, settle, button, bodyEl, titleEl, input };
}

const resp = (status, body) => Promise.resolve({ status, ok: status >= 200 && status < 300, json: async () => body });
let fail = 0;
const check = (name, cond, extra) => { console.log((cond ? "PASS " : "FAIL ") + name, cond ? "" : JSON.stringify(extra)); if (!cond) fail++; };
const post = (o) => o.method === "POST";

(async () => {
  // 1. 202 -> processing x2 -> refreshed; button restored; poll spacing >= 2s
  let polls = 0;
  let e = makeEnv({ fetchImpl: (url, o) => post(o) ? resp(202, { status: "processing", job: "abc" })
    : (++polls < 3 ? resp(200, { status: "processing", job: "abc" }) : resp(200, { status: "refreshed", message: { title: "t", body: "تم" } })) });
  e.click(); await e.settle();
  check("polls until refreshed", e.bodyEl.textContent === "تم", e.shown);
  check("status url uses job id", e.fetchCalls[1].url === "/employee/refresh/status/abc/", e.fetchCalls);
  check(">=2000ms between polls", e.timers.every((t) => t >= 2000), e.timers);
  check("button re-enabled after result", e.button.disabled === false, e.button.disabled);

  // 2. processing + final:true stops after one poll
  e = makeEnv({ fetchImpl: (url, o) => post(o) ? resp(202, { status: "processing", job: "abc" })
    : resp(200, { status: "processing", final: true, message: { title: "x", body: "قيد المعالجة" } }) });
  e.click(); await e.settle();
  check("final processing message, one poll", e.bodyEl.textContent === "قيد المعالجة" && e.fetchCalls.length === 2, [e.shown, e.fetchCalls.length]);

  // 3. 3 consecutive network failures -> generic error
  e = makeEnv({ fetchImpl: (url, o) => post(o) ? resp(202, { status: "processing", job: "abc" }) : Promise.reject(new Error("net")) });
  e.click(); await e.settle();
  check("3 failures stop with generic error", e.bodyEl.textContent === "حدث خطأ ما" && e.titleEl.textContent === "خطأ" && e.fetchCalls.length === 4, [e.shown, e.fetchCalls.length]);

  // 4. failures reset by a success
  let n = 0;
  e = makeEnv({ fetchImpl: (url, o) => { if (post(o)) return resp(202, { status: "processing", job: "a" }); n++;
    if (n === 1 || n === 2 || n === 4 || n === 5) return Promise.reject(new Error("x")); if (n === 3) return resp(200, { status: "processing", job: "a" });
    return resp(200, { status: "busy", message: { title: "b", body: "مشغولة" } }); } });
  e.click(); await e.settle();
  check("success resets failure counter", e.bodyEl.textContent === "مشغولة", e.shown);

  // 5. deadline = waitSeconds + 10
  e = makeEnv({ fetchImpl: (url, o) => post(o) ? resp(202, { status: "processing", job: "a" }) : resp(200, { status: "processing", job: "a" }) });
  e.click(); await e.settle();
  check("gives up after wait+10s with generic error", e.bodyEl.textContent === "حدث خطأ ما" && e.fetchCalls.length <= 52, [e.shown, e.fetchCalls.length]);

  // 6. a new number submitted while the first is polling supersedes it
  let postN = 0; let release;
  e = makeEnv({ fetchImpl: (url, o) => { if (post(o)) { postN++; return resp(202, { status: "processing", job: "j" + postN }); }
    if (url.includes("/j1/")) return new Promise((r) => { release = () => r({ status: 200, ok: true, json: async () => ({ status: "refreshed", message: { body: "stale" } }) }); });
    return resp(200, { status: "refreshed", message: { body: "fresh" } }); } });
  e.click("0529000000");
  for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r));
  e.click("0529000001");
  await e.settle();
  release();
  await e.settle();
  check("stale poll dropped, new run shows its own result", !e.shown.some((s) => s.body === "stale") && e.bodyEl.textContent === "fresh", e.shown);
  check("button enabled once the latest run finished", e.button.disabled === false, e.button.disabled);

  // 7. plain 200 payload (cooldown etc.) is rendered without polling
  e = makeEnv({ fetchImpl: () => resp(200, { status: "wait", message: { title: "w", body: "انتظر" } }) });
  e.click(); await e.settle();
  check("200 payload rendered, no polling", e.bodyEl.textContent === "انتظر" && e.fetchCalls.length === 1, [e.shown, e.fetchCalls.length]);

  // 8. 429 on status: back off 2,4,8,10 cap, not counted as failure
  let n429 = 0;
  e = makeEnv({ fetchImpl: (url, o) => post(o) ? resp(202, { status: "processing", job: "a" })
    : (++n429 <= 5 ? resp(429, {}) : resp(200, { status: "refreshed", message: { body: "بعد التهدئة" } })) });
  e.click(); await e.settle();
  check("429 backs off and then shows the result", e.bodyEl.textContent === "بعد التهدئة", e.shown);
  check("backoff doubles up to 10s", JSON.stringify(e.timers.slice(0, 6)) === JSON.stringify([2000, 4000, 8000, 10000, 10000, 10000]), e.timers);

  // 9. 202 without a job id -> generic error, no polling
  e = makeEnv({ fetchImpl: () => resp(202, { status: "processing" }) });
  e.click(); await e.settle();
  check("202 without job shows generic error", e.bodyEl.textContent === "حدث خطأ ما" && e.fetchCalls.length === 1, [e.shown, e.fetchCalls.length]);

  // 10. POST network error -> generic error and button restored
  e = makeEnv({ fetchImpl: () => Promise.reject(new Error("down")) });
  e.click(); await e.settle();
  check("POST failure shows generic error, button restored", e.bodyEl.textContent === "حدث خطأ ما" && e.button.disabled === false, [e.shown, e.button.disabled]);

  console.log(fail ? "FAILED" : "ALL OK"); process.exit(fail ? 1 : 0);
})();
