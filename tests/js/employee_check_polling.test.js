// Plain-node tests for the number-check flow in static/js/employee-check-number.js
// (stubbed DOM/fetch/timers). Run from anywhere:
//   node tests/js/employee_check_polling.test.js
// No dependencies; exits non-zero on failure.
const fs = require("fs"), vm = require("vm"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "..", "static", "js", "employee-check-number.js"), "utf8");

const ATTRS = {
  "data-check-url": "/employee/sales/api/check-phone/",
  "data-check-status-url": "/employee/check/status/00000000-0000-0000-0000-000000000000/",
  "data-wait-seconds": "90",
  "data-msg-phone": "أدخل رقم الهاتف",
  "data-msg-invalid-phone": "رقم غير صحيح",
};
const GENERIC = "حدث خطأ، حاول مرة أخرى";
const WORKING = "جارٍ الفحص…";

function makeEnv({ fetchImpl, withRoot = true }) {
  const timers = [];
  const listeners = {};
  const toasts = [];
  let now = 1_000_000;
  const els = {};
  const el = (id) => (els[id] = els[id] || {
    id, value: "", disabled: false, _text: "", innerHTML: "", attrs: {}, hidden: true,
    classList: {
      add(c) { if (c === "d-none") els[id].hidden = true; },
      remove(c) { if (c === "d-none") els[id].hidden = false; },
    },
    set textContent(v) { this._text = v; this.innerHTML = ""; this.textSet = true; },
    get textContent() { return this._text; },
    setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k] !== undefined ? this.attrs[k] : null; },
    addEventListener(type, fn) { (listeners[id + ":" + type] = listeners[id + ":" + type] || []).push(fn); },
  });
  const root = el("rd-refresh-page");
  root.getAttribute = (k) => (ATTRS[k] !== undefined ? ATTRS[k] : null);
  const input = el("rd-refresh-phone-input");
  const button = el("rd-check-submit");
  button._text = "فحص الرقم";
  const box = el("rd-check-result");
  const document = {
    getElementById: (id) => (id === "rd-refresh-page" && !withRoot ? null : (/^rd-(refresh|check)/.test(id) ? el(id) : null)),
    querySelector: (s) => (s.includes("csrfmiddlewaretoken") ? { value: "tok" } : null),
  };
  const fetchCalls = [];
  const ctx = {
    document, window: { rdToast: (m, l) => toasts.push({ m, l }), alert() {} }, console, URLSearchParams, Promise, Number, Math,
    encodeURIComponent, String, Date: { now: () => now },
    setTimeout: (fn, ms) => { timers.push(ms); now += ms; return setImmediate(fn); },
    fetch: (url, opts) => { fetchCalls.push({ url, method: opts.method, headers: opts.headers, body: opts.body }); return fetchImpl(url, opts, fetchCalls.length); },
  };
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  const click = (phone = "0529000000") => {
    input.value = phone;
    listeners["rd-check-submit:click"][0]();
  };
  const settle = async () => { for (let i = 0; i < 200; i++) await new Promise((r) => setImmediate(r)); };
  return { timers, fetchCalls, toasts, click, settle, button, box, input, listeners };
}

const resp = (status, body) => Promise.resolve({ status, ok: status >= 200 && status < 300, json: async () => body });
let fail = 0;
const check = (name, cond, extra) => { console.log((cond ? "PASS " : "FAIL ") + name, cond ? "" : JSON.stringify(extra)); if (!cond) fail++; };
const post = (o) => o.method === "POST";

(async () => {
  // 0. no root -> no-op, no throw
  makeEnv({ fetchImpl: () => resp(200, {}), withRoot: false });
  check("no root element: script is a no-op", true);

  // 1. invalid phone -> toast, no fetch, button untouched
  let e = makeEnv({ fetchImpl: () => resp(200, {}) });
  e.click("12345"); await e.settle();
  check("invalid phone: no fetch, invalid-phone toast", e.fetchCalls.length === 0 && e.toasts.length === 1 && e.toasts[0].m === "رقم غير صحيح" && e.toasts[0].l === "error", [e.fetchCalls.length, e.toasts]);
  e.click(""); await e.settle();
  check("empty phone: phone toast", e.fetchCalls.length === 0 && e.toasts[1].m === "أدخل رقم الهاتف", e.toasts);
  check("validation does not disable the button", e.button.disabled === false, e.button.disabled);

  // 2. 202 -> processing x2 -> ok: html set, button re-enabled, label restored, poll spacing
  let polls = 0;
  e = makeEnv({ fetchImpl: (url, o) => post(o) ? resp(202, { status: "processing", job: "abc" })
    : (++polls < 3 ? resp(200, { status: "processing" }) : resp(200, { status: "ok", html: "<dl><dt>الحالة</dt></dl>" })) });
  e.click();
  await new Promise((r) => setImmediate(r));
  check("button disabled with working label while running", e.button.disabled === true && e.button.textContent === WORKING, [e.button.disabled, e.button.textContent]);
  await e.settle();
  check("ok payload html goes to the container", e.box.innerHTML === "<dl><dt>الحالة</dt></dl>" && e.box.hidden === false, [e.box.innerHTML, e.box.hidden]);
  check("POST carries csrf + form-encoded phone", e.fetchCalls[0].headers["X-CSRFToken"] === "tok" && e.fetchCalls[0].body === "phone=0529000000" && e.fetchCalls[0].url === "/employee/sales/api/check-phone/", e.fetchCalls[0]);
  check("status url uses job id", e.fetchCalls[1].url === "/employee/check/status/abc/" && e.fetchCalls[1].method === "GET", e.fetchCalls);
  check("three polls, >=2000ms apart", e.fetchCalls.length === 4 && e.timers.every((t) => t >= 2000), [e.fetchCalls.length, e.timers]);
  check("button re-enabled and label restored", e.button.disabled === false && e.button.textContent === "فحص الرقم", [e.button.disabled, e.button.textContent]);

  // 3. status error (final or not) -> message via textContent, stop
  e = makeEnv({ fetchImpl: (url, o) => post(o) ? resp(202, { status: "processing", job: "abc" })
    : resp(200, { status: "error", message: "<img src=x onerror=1> فشل فحص الرقم", final: true }) });
  e.click(); await e.settle();
  check("error message shown as text (never HTML)", e.box.textContent === "<img src=x onerror=1> فشل فحص الرقم" && e.box.innerHTML === "" && e.box.hidden === false, [e.box.textContent, e.box.innerHTML]);
  check("error stops polling after one poll, button re-enabled", e.fetchCalls.length === 2 && e.button.disabled === false, [e.fetchCalls.length, e.button.disabled]);

  // 4. non-202 POST payload (e.g. unsupported) shown as text, no polling
  e = makeEnv({ fetchImpl: () => resp(200, { status: "error", message: "فحص الرقم غير متاح لهذا المزوّد" }) });
  e.click(); await e.settle();
  check("200 error payload rendered, no polling", e.box.textContent === "فحص الرقم غير متاح لهذا المزوّد" && e.fetchCalls.length === 1 && e.button.disabled === false, [e.box.textContent, e.fetchCalls.length]);

  // 5. 403 on POST uses the JSON message; 403 / 500 on status stop polling
  e = makeEnv({ fetchImpl: () => resp(403, { status: "error", message: "الخدمة موقوفة" }) });
  e.click(); await e.settle();
  check("POST 403 shows its message", e.box.textContent === "الخدمة موقوفة" && e.fetchCalls.length === 1 && e.button.disabled === false, [e.box.textContent, e.fetchCalls.length]);
  e = makeEnv({ fetchImpl: (url, o) => post(o) ? resp(202, { status: "processing", job: "a" }) : resp(403, { status: "error", message: "الخدمة موقوفة" }) });
  e.click(); await e.settle();
  check("status 403 stops polling with its message", e.box.textContent === "الخدمة موقوفة" && e.fetchCalls.length === 2 && e.button.disabled === false, [e.box.textContent, e.fetchCalls.length]);
  e = makeEnv({ fetchImpl: (url, o) => post(o) ? resp(202, { status: "processing", job: "a" }) : Promise.resolve({ status: 500, ok: false, json: async () => { throw new Error("html"); } }) });
  e.click(); await e.settle();
  check("status 500 (non-JSON) stops with generic error", e.box.textContent === GENERIC && e.fetchCalls.length === 2 && e.button.disabled === false, [e.box.textContent, e.fetchCalls.length]);

  // 6. network errors -> generic text, button restored
  e = makeEnv({ fetchImpl: () => Promise.reject(new Error("down")) });
  e.click(); await e.settle();
  check("POST network error: generic text, button restored", e.box.textContent === GENERIC && e.button.disabled === false && e.box.hidden === false, [e.box.textContent, e.button.disabled]);
  e = makeEnv({ fetchImpl: (url, o) => post(o) ? resp(202, { status: "processing", job: "a" }) : Promise.reject(new Error("x")) });
  e.click(); await e.settle();
  check("poll network error: generic text, stops", e.box.textContent === GENERIC && e.fetchCalls.length === 2 && e.button.disabled === false, [e.box.textContent, e.fetchCalls.length]);

  // 7. wait elapsed (wait_seconds + grace) -> generic error, stops
  e = makeEnv({ fetchImpl: (url, o) => post(o) ? resp(202, { status: "processing", job: "a" }) : resp(200, { status: "processing" }) });
  e.click(); await e.settle();
  check("gives up after wait+grace", e.box.textContent === GENERIC && e.fetchCalls.length > 3 && e.fetchCalls.length <= 52 && e.button.disabled === false, [e.box.textContent, e.fetchCalls.length]);

  // 8. 202 without job -> generic error, no polling
  e = makeEnv({ fetchImpl: () => resp(202, { status: "processing" }) });
  e.click(); await e.settle();
  check("202 without job: generic error, no polling", e.box.textContent === GENERIC && e.fetchCalls.length === 1 && e.button.disabled === false, [e.box.textContent, e.fetchCalls.length]);

  // 9. a second press cancels the first poll (stale result never shown)
  let postN = 0; let release;
  e = makeEnv({ fetchImpl: (url, o) => { if (post(o)) { postN++; return resp(202, { status: "processing", job: "j" + postN }); }
    if (url.includes("/j1/")) return new Promise((r) => { release = () => r({ status: 200, ok: true, json: async () => ({ status: "ok", html: "<p>stale</p>" }) }); });
    return resp(200, { status: "ok", html: "<p>fresh</p>" }); } });
  e.click("0529000000");
  for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r));
  e.click("0529000001");
  await e.settle();
  release();
  await e.settle();
  check("stale poll dropped, new run shows its own card", e.box.innerHTML === "<p>fresh</p>", e.box.innerHTML);
  check("button enabled once the latest run finished", e.button.disabled === false && e.button.textContent === "فحص الرقم", [e.button.disabled, e.button.textContent]);

  // 10. number normalisation matches refresh (972 prefix, 9-digit)
  e = makeEnv({ fetchImpl: () => resp(200, { status: "error", message: "x" }) });
  e.click("972529000000"); await e.settle();
  check("972 prefix normalised to 0…", e.fetchCalls[0].body === "phone=0529000000", e.fetchCalls[0]);

  // 11. result area is hidden again nowhere: a new run clears the old card while working
  let n = 0;
  e = makeEnv({ fetchImpl: (url, o) => post(o) ? resp(202, { status: "processing", job: "j" }) : (++n === 1 ? resp(200, { status: "ok", html: "<p>one</p>" }) : resp(200, { status: "processing" })) });
  e.click(); await e.settle();
  e.click(); await new Promise((r) => setImmediate(r));
  check("previous card cleared when a new check starts", e.box.innerHTML === "" && e.box.textContent === "", [e.box.innerHTML, e.box.textContent]);
  await e.settle();

  console.log(fail ? "FAILED" : "ALL OK"); process.exit(fail ? 1 : 0);
})();
