// Plain-node tests for the polling logic in static/phone_refresh/js/refresh.js (stubbed DOM/fetch/timers).
// Run from anywhere:  node tests/js/refresh_polling.test.js
// No dependencies; exits non-zero on failure.
const fs = require("fs"), vm = require("vm"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "..", "static", "phone_refresh", "js", "refresh.js"), "utf8");

function makeEnv({ fetchImpl }) {
  const alerts = [];
  const timers = [];
  let now = 1_000_000;
  class El {
    constructor(name) { this.name = name; this._text = ""; this.value = ""; this.disabled = false; this.className = ""; this.offsetWidth = 1;
      this.classList = { add: (...c) => { this.cls = (this.cls||[]).concat(c); }, remove(){}, toggle(){} }; this.content = ""; }
    set textContent(v) { this._text = v; if (this.name === ".alert-text") alerts.push(v); }
    get textContent() { return this._text; }
    setAttribute(){} getAttribute(){ return ""; } addEventListener(){} querySelector(s){ return new El(s); } querySelectorAll(){ return []; }
  }
  const metas = { "refresh-api": "/api/refresh/", "refresh-status-url": "/api/refresh/status/00000000-0000-0000-0000-000000000000/", "refresh-wait-seconds": "90" };
  const document = {
    documentElement: new El("html"), cookie: "", addEventListener() {},
    querySelector(s) { const m = s.match(/meta\[name="(.+)"\]/); if (m) return metas[m[1]] !== undefined ? { content: metas[m[1]] } : null; return new El(s); },
    querySelectorAll() { return []; }, getElementById() { return null; }, title: "",
  };
  const fetchCalls = [];
  const ctx = {
    document, localStorage: { getItem: () => "ar", setItem() {} }, window: { BG: null, location: { reload() {} } },
    requestAnimationFrame: (f) => f(), console,
    setTimeout: (fn, ms) => { timers.push(ms); now += ms; return setImmediate(fn); },
    Date: Object.assign(function () { return { getFullYear: () => 2026 }; }, { now: () => now }), Promise, JSON, Number, encodeURIComponent, decodeURIComponent, Math,
    fetch: (url, opts) => { fetchCalls.push({ url, method: opts.method, t: now }); return fetchImpl(url, opts, fetchCalls.length); },
  };
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  return { ctx, alerts, timers, fetchCalls };
}
const resp = (status, body) => Promise.resolve({ status, ok: status >= 200 && status < 300, json: async () => body });
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
let fail = 0;
const check = (name, cond, extra) => { console.log((cond ? "PASS " : "FAIL ") + name, cond ? "" : JSON.stringify(extra)); if (!cond) fail++; };

(async () => {
  // 1. 202 -> processing x2 -> refreshed
  let polls = 0;
  let e = makeEnv({ fetchImpl: (url, o) => o.method === "POST"
    ? resp(202, { status: "processing", job: "abc" })
    : (++polls < 3 ? resp(200, { status: "processing", job: "abc" }) : resp(200, { status: "refreshed", message: { title: "t", body: "تم" } })) });
  let body; 
  await vm.runInContext('submitRefresh("0529000000")', e.ctx); 
  check("polls until refreshed", e.alerts[e.alerts.length - 1] === "تم", e.alerts);
  check("loader text first", e.alerts[0] === "جاري الإرسال...", e.alerts);
  check("status url uses job id", e.fetchCalls[1].url === "/api/refresh/status/abc/", e.fetchCalls);
  check(">=2000ms between polls", e.timers.every((t) => t >= 2000), e.timers);

  // 2. processing + final:true stops
  e = makeEnv({ fetchImpl: (url, o) => o.method === "POST" ? resp(202, { status: "processing", job: "abc" })
    : resp(200, { status: "processing", final: true, message: { title: "x", body: "قيد المعالجة" } }) });
  await vm.runInContext('submitRefresh("0529000000")', e.ctx);
  check("final processing message shown, one poll", e.alerts[e.alerts.length - 1] === "قيد المعالجة" && e.fetchCalls.length === 2, [e.alerts, e.fetchCalls.length]);

  // 3. 3 consecutive network failures -> generic error
  e = makeEnv({ fetchImpl: (url, o) => o.method === "POST" ? resp(202, { status: "processing", job: "abc" }) : Promise.reject(new Error("net")) });
  await vm.runInContext('submitRefresh("0529000000")', e.ctx);
  check("3 failures stop with generic error", e.alerts[e.alerts.length - 1] === "حدث خطأ غير متوقع" && e.fetchCalls.length === 4, [e.alerts, e.fetchCalls.length]);

  // 4. failures reset by success
  let n = 0;
  e = makeEnv({ fetchImpl: (url, o) => { if (o.method === "POST") return resp(202, { status: "processing", job: "a" }); n++;
    if (n === 1 || n === 2 || n === 4 || n === 5) return Promise.reject(new Error("x")); if (n === 3) return resp(200, { status: "processing", job: "a" }); return resp(200, { status: "busy", message: { title: "b", body: "مشغولة" } }); } });
  await vm.runInContext('submitRefresh("0529000000")', e.ctx);
  check("success resets failure counter", e.alerts[e.alerts.length - 1] === "مشغولة", e.alerts);

  // 5. deadline
  e = makeEnv({ fetchImpl: (url, o) => o.method === "POST" ? resp(202, { status: "processing", job: "a" }) : resp(200, { status: "processing", job: "a" }) });
  await vm.runInContext('submitRefresh("0529000000")', e.ctx);
  check("gives up after wait+10s with generic error", e.alerts[e.alerts.length - 1] === "حدث خطأ غير متوقع" && e.fetchCalls.length <= 52, [e.alerts, e.fetchCalls.length]);

  // 6. superseded by new submit (first poll request is in flight when the user submits again)
  let post = 0; let release;
  e = makeEnv({ fetchImpl: (url, o) => { if (o.method === "POST") { post++; return resp(202, { status: "processing", job: "j" + post }); }
    if (url.includes("/j1/")) return new Promise((r) => { release = () => r({ status: 200, ok: true, json: async () => ({ status: "refreshed", message: { body: "stale" } }) }); });
    return resp(200, { status: "refreshed", message: { body: "fresh" } }); } });
  const first = vm.runInContext('submitRefresh("0529000000")', e.ctx);
  await wait(20);
  const second = vm.runInContext('submitRefresh("0529000001")', e.ctx);
  await second;
  release();
  await first;
  check("stale poll result is dropped, new run shows its own result", !e.alerts.includes("stale") && e.alerts[e.alerts.length - 1] === "fresh" && e.fetchCalls.filter(c => c.url.includes("/j1/")).length === 1, [e.alerts, e.fetchCalls.map(c => c.url)]);

  // 7. sync-style 200 response and 429
  e = makeEnv({ fetchImpl: () => resp(200, { status: "wait", message: { body: "انتظر" } }) });
  await vm.runInContext('submitRefresh("0529000000")', e.ctx);
  check("200 payload rendered without polling", e.alerts[e.alerts.length - 1] === "انتظر" && e.fetchCalls.length === 1, e.alerts);
  e = makeEnv({ fetchImpl: () => resp(429, {}) });
  await vm.runInContext('submitRefresh("0529000000")', e.ctx);
  check("429 shows ratelimit message", e.alerts[e.alerts.length - 1] === "عدد كبير من المحاولات، الرجاء الانتظار قليلاً", e.alerts);
  // 8. 429 on status: back off (2s -> 4s -> 8s -> 10s cap), not counted as failure
  let n429 = 0;
  e = makeEnv({ fetchImpl: (url, o) => o.method === "POST" ? resp(202, { status: "processing", job: "a" })
    : (++n429 <= 5 ? resp(429, {}) : resp(200, { status: "refreshed", message: { body: "تم بعد التهدئة" } })) });
  await vm.runInContext('submitRefresh("0529000000")', e.ctx);
  check("429 backs off and eventually shows the result", e.alerts[e.alerts.length - 1] === "تم بعد التهدئة", e.alerts);
  check("backoff doubles up to 10s", JSON.stringify(e.timers.slice(0, 6)) === JSON.stringify([2000, 4000, 8000, 10000, 10000, 10000]), e.timers);

  // 9. a 429 after 2 failures neither counts as the 3rd failure nor aborts polling
  let m = 0;
  e = makeEnv({ fetchImpl: (url, o) => { if (o.method === "POST") return resp(202, { status: "processing", job: "a" }); m++;
    if (m === 1 || m === 2) return Promise.reject(new Error("x")); if (m === 3) return resp(429, {}); return resp(200, { status: "refreshed", message: { body: "ok" } }); } });
  await vm.runInContext('submitRefresh("0529000000")', e.ctx);
  check("429 between failures is not itself a failure", e.alerts[e.alerts.length - 1] === "ok", e.alerts);

  // 10. 202 without job id -> generic error, no polling
  e = makeEnv({ fetchImpl: () => resp(202, { status: "processing" }) });
  await vm.runInContext('submitRefresh("0529000000")', e.ctx);
  check("202 without job shows generic error", e.alerts[e.alerts.length - 1] === "حدث خطأ غير متوقع" && e.fetchCalls.length === 1, e.alerts);

  console.log(fail ? "FAILED" : "ALL OK"); process.exit(fail ? 1 : 0);
})();
