// Plain-node tests for static/js/number-check-share.js (stubbed DOM/canvas in a vm).
// Run from anywhere:
//   node tests/js/ncheck_share.test.js
// No dependencies; exits non-zero on failure.
const fs = require("fs"), vm = require("vm"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "..", "static", "js", "number-check-share.js"), "utf8");

let fail = 0;
const check = (name, cond, extra) => { console.log((cond ? "PASS " : "FAIL ") + name, cond ? "" : JSON.stringify(extra)); if (!cond) fail++; };

const SHARE = {
  phone_display: "052-123-4567", network: "سلكوم", sim_type: "شريحة إلكترونية", status: "مفعل", status_tone: "ok",
  plan_name: "حملة رمضان 400 جيجا سلكوم",
  expiry: { date: "13/11/2026", days: "29", word: "يوم", state: "", tone: "ok" },
  usage: [
    { label: "إنترنت", used: "6.08", total: "400", unit: "جيجا", pct: "1.5", included: true },
    { label: "دقائق", used: "0", total: "5000", unit: "دقيقة", pct: "0", included: true },
    { label: "رسائل", used: "0", total: "0", unit: "رسالة", pct: "", included: false },
  ],
  utilization: "1.5", line_logo: "/media/icons/product_lines/cellcom.webp", checked_at: "15/10/2026 · 19:50",
};
const COLORS = {
  ink: "INK", muted: "MUTED", surface: "SURFACE", surface2: "SURFACE2", border: "BORDER", success: "OK", successSoft: "OKSOFT",
  warning: "WARN", warningSoft: "WARNSOFT", danger: "DANGER", dangerSoft: "DANGERSOFT", accent: "ACCENT",
};

function makeEnv(opts = {}) {
  const listeners = {}, captures = {}, fontCalls = [], logs = { error: [], warn: [] };
  const toasts = [], downloads = [], shared = [], styleReads = [];
  const drawn = [];
  const canvas = {
    width: 0, height: 0,
    getContext: () => ({
      measureText: (s) => ({ width: String(s).length * 20 }),
      fillRect() {}, beginPath() {}, fill() {}, stroke() {}, arc() {}, roundRect() {}, drawImage() {},
      fillText(s, x, y) { drawn.push({ s, x, y }); },
    }),
    toBlob: (cb) => cb(opts.noBlob ? null : { size: 10, type: "image/png" }),
  };
  const body = { appendChild() {}, removeChild() {} };
  const mkEl = () => ({ style: {}, setAttribute() {}, click() { downloads.push({ href: this.href, name: this.download }); } });
  const document = {
    body, fonts: { load: (spec, text) => { fontCalls.push([spec, text]); return Promise.resolve(); } },
    createElement: (tag) => (tag === "canvas" ? canvas : mkEl()),
    addEventListener: (t, fn, cap) => { (listeners[t] = listeners[t] || []).push(fn); captures[t] = cap; },
  };
  const label = { textContent: "مشاركة كصورة" };
  const json = { textContent: JSON.stringify(opts.share || SHARE) };
  const attrs = { "data-site-logo": "", "data-site-name": "Pro Sim" };
  const card = { querySelector: (s) => (s.includes("application/json") ? json : null), getAttribute: (k) => attrs[k] };
  const btn = { disabled: false, closest: (s) => (s === "[data-rd-ncheck]" ? card : null), querySelector: () => label };
  const nav = {};
  if (opts.canShare) nav.canShare = () => true;
  if (opts.shareImpl) nav.share = (d) => { shared.push(d); return opts.shareImpl(d); };
  const window = {
    rdToast: (m, l) => toasts.push({ m, l }),
    location: { href: "http://localhost/ar/employee/", origin: "http://localhost" },
    navigator: nav,
    getComputedStyle: () => ({ getPropertyValue: (n) => { styleReads.push(n); return opts.tokens === false ? "" : "#123456"; }, color: "rgb(1, 2, 3)" }),
  };
  const ctx = {
    document, window, console: { error: (...a) => logs.error.push(a), warn: (...a) => logs.warn.push(a), log() {} }, Promise, JSON, Math, Object, String, Number, parseFloat, URL, setTimeout: () => 0,
    File: class { constructor(parts, name, o) { this.parts = parts; this.name = name; this.type = o.type; } },
    Image: class { set src(v) { setTimeout0(() => (opts.imgFail ? this.onerror() : this.onload())); } get width() { return 100; } get height() { return 50; } },
  };
  ctx.URL = Object.assign(URL, {});
  ctx.URL.createObjectURL = () => "blob:x"; ctx.URL.revokeObjectURL = () => {};
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  const click = async () => {
    listeners.click[0]({ target: { closest: (s) => (s === "[data-rd-share-check]" ? btn : null) }, preventDefault() {} });
    for (let i = 0; i < 50; i++) await new Promise((r) => setImmediate(r));
  };
  return { api: window.rdNcheckShare, listeners, captures, fontCalls, logs, click, btn, label, toasts, downloads, shared, drawn, styleReads };
}
const setTimeout0 = (fn) => setImmediate(fn);

(async () => {
  let e = makeEnv();
  const api = e.api;

  // --- pure helpers
  check("groupPhone: 10 digits -> 3-3-4", api.groupPhone("0521234567") === "052-123-4567", api.groupPhone("0521234567"));
  check("groupPhone: other input as is", api.groupPhone("12345") === "12345" && api.groupPhone("") === "" && api.groupPhone("+972521234567") === "+972521234567", 0);
  check("phoneDigits strips dashes", api.phoneDigits("052-123-4567") === "0521234567", 0);
  check("usedTotalParts: pieces, no bidi run", JSON.stringify(api.usedTotalParts("6.08", "400")) === '["6.08","/","400"]' && JSON.stringify(api.usedTotalParts("6", "")) === '["6"]', 0);
  const meas = (s) => s.length * 10;
  check("wrapText wraps by words in logical order", JSON.stringify(api.wrapText("aa bb cc dd", 55, meas)) === '["aa bb","cc dd"]', api.wrapText("aa bb cc dd", 55, meas));
  check("wrapText caps lines with an ellipsis", api.wrapText("aa bb cc dd ee ff", 30, meas, 2).length === 2 && api.wrapText("aa bb cc dd ee ff", 30, meas, 2)[1].endsWith("…"), api.wrapText("aa bb cc dd ee ff", 30, meas, 2));
  check("ellipsize keeps short text, trims long", api.ellipsize("abc", 100, meas) === "abc" && api.ellipsize("abcdefghij", 55, meas).endsWith("…") && meas(api.ellipsize("abcdefghij", 55, meas)) <= 55, api.ellipsize("abcdefghij", 55, meas));
  check("fitSize shrinks only when too wide", api.fitSize(76, 300, 400) === 76 && api.fitSize(76, 800, 400) === 38, [api.fitSize(76, 300, 400), api.fitSize(76, 800, 400)]);
  const loc = { href: "http://localhost/a/", origin: "http://localhost" };
  check("isSameOrigin: relative and same host ok, other host / empty rejected",
    api.isSameOrigin("/media/x.webp", loc) && api.isSameOrigin("http://localhost/x", loc) && !api.isSameOrigin("https://cdn.example.com/x.png", loc) && !api.isSameOrigin("", loc), 0);
  check("toneColors maps ok/warn/danger/muted", api.toneColors("ok", COLORS).fg === "OK" && api.toneColors("warn", COLORS).bg === "WARNSOFT" && api.toneColors("danger", COLORS).fg === "DANGER" && api.toneColors("muted", COLORS).bg === "SURFACE2" && api.toneColors("?", COLORS).fg === "MUTED", 0);

  // --- readTokens
  const probe = { style: {}, setAttribute(k, v) { this.attrs = { ...(this.attrs || {}), [k]: v }; } };
  const doc = { createElement: () => probe, body: { appendChild() { probe.attached = true; }, removeChild() { probe.attached = false; } } };
  const gs = () => ({ getPropertyValue: () => "#abcdef", color: "rgb(10, 20, 30)" });
  const toks = api.readTokens(doc, gs);
  check("readTokens: light-theme probe, resolved colours, probe removed",
    probe.attrs["data-bs-theme"] === "light" && probe.attached === false && toks.ink === "rgb(10, 20, 30)" && Object.keys(toks).length === Object.keys(api.TOKEN_VARS).length, [probe.attrs, toks]);
  const missing = api.readTokens(doc, () => ({ getPropertyValue: () => "", color: "" }));
  check("readTokens: missing token falls back to the table", missing.ink === api.TOKEN_FALLBACK.ink && missing.success === api.TOKEN_FALLBACK.success, missing);
  check("readTokens reads the --rd-* tokens", api.TOKEN_VARS.ink === "--rd-ink" && api.TOKEN_VARS.successSoft === "--rd-success-soft" && api.TOKEN_VARS.accent === "--rd-accent", api.TOKEN_VARS);

  // --- layout
  const L = api.layoutShare(SHARE, (s, w, z) => String(s).length * z * 0.5, COLORS, { siteName: "Pro Sim", hasSiteLogo: true, hasLineLogo: true });
  const texts = L.ops.filter((o) => o.t === "text");
  const strs = texts.map((o) => o.s);
  check("layout: 1080 wide, height near 1270", L.width === 1080 && L.height > 1200 && L.height < 1400, [L.width, L.height]);
  const phone = texts.find((o) => o.s === "052-123-4567");
  check("layout: number drawn LTR from an explicit left x", phone && phone.dir === "ltr" && phone.align === "left" && phone.x > 0, phone);
  const firstY = (texts.find((o) => o.s === "6.08") || {}).y;
  const pieces = texts.filter((o) => ["6.08", "/", "400"].includes(o.s) && o.dir === "ltr" && o.y === firstY);
  check("layout: 'used / total' drawn piece by piece, increasing x", pieces.length === 3 && pieces[0].x < pieces[1].x && pieces[1].x < pieces[2].x && pieces.every((p) => p.y === pieces[0].y), pieces);
  check("layout: date and days LTR", texts.some((o) => o.s === "13/11/2026" && o.dir === "ltr") && texts.some((o) => o.s === "29" && o.dir === "ltr"), 0);
  check("layout: Arabic runs are rtl, right-aligned",
    ["مفعل", "الأيام المتبقية", "تاريخ الانتهاء", "إنترنت", "الحزمة", "نتيجة فحص الرقم"].every((s) => { const o = texts.find((t) => t.s === s); return o && o.dir === "rtl"; }), 0);
  check("layout: network · sim type, plan, days word, footer",
    strs.includes("سلكوم · شريحة إلكترونية") && strs.includes("حملة رمضان 400 جيجا سلكوم") && strs.includes("يوم") && strs.includes("Pro Sim · فحص فوري للخطوط"), strs);
  check("layout: not-included row says so and draws no bar for it", strs.includes("غير مشمولة بالحزمة") && !strs.includes("رسالة"), strs);
  check("layout: utilization drawn", strs.includes("1.5%") && strs.includes("نسبة الاستخدام"), strs);
  check("layout: line logo + site logo images when available", L.ops.filter((o) => o.t === "image").map((o) => o.key).sort().join() === "line,site", 0);
  const L2 = api.layoutShare({ ...SHARE, expiry: null, usage: [] , utilization: ""}, (s, w, z) => String(s).length * z * 0.5, COLORS, { hasLineLogo: false });
  const s2 = L2.ops.filter((o) => o.t === "text").map((o) => o.s);
  check("layout: monogram + no-expiry + no-usage + site name fallback", s2.includes("س") && s2.includes("لا يوجد تاريخ انتهاء") && s2.includes("لا تتوفر بيانات استهلاك لهذا الرقم") && s2.includes("Pro Sim") && !L2.ops.some((o) => o.t === "image"), s2);
  const L3 = api.layoutShare({ ...SHARE, expiry: { date: "01/10/2026", days: "", word: "", state: "منتهية", tone: "danger" } }, (s, w, z) => String(s).length * z * 0.5, COLORS, {});
  const t3 = L3.ops.filter((o) => o.t === "text").map((o) => o.s);
  check("layout: expired state shown instead of a day count", t3.includes("منتهية") && t3.includes("الصلاحية") && !t3.includes("الأيام المتبقية"), t3);
  const leaky = api.layoutShare({ ...SHARE, plan_price: "77.7", iccid: "8997201234567890123", sale: { price: "61.5" }, product: "x" }, (s, w, z) => String(s).length * z * 0.5, COLORS, {});
  const all = JSON.stringify(leaky.ops);
  check("layout: never draws price, iccid or sale even if present in the data", !all.includes("8997") && !all.includes("77.7") && !all.includes("61.5"), 0);
  const long = api.layoutShare({ ...SHARE, phone_display: "052-123-4567", plan_name: "حزمة ".repeat(40) }, (s, w, z) => String(s).length * z * 0.5, COLORS, {});
  check("layout: a long plan name wraps (taller image, <= 3 lines)", long.height > L.height && long.height <= L.height + 2 * 54 + 1, [L.height, long.height]);
  const pill = L.ops.find((o) => o.t === "circle" && o.fill === "OK");
  check("layout: status dot + soft pill use the tone colours", !!pill && L.ops.some((o) => o.t === "rect" && o.fill === "OKSOFT"), 0);

  // --- click flow
  check("delegated click listener on document (no bind-once guard)", (e.listeners.click || []).length === 1, Object.keys(e.listeners));
  await e.click();
  check("no Web Share API: downloads number-check-<phone>.png + success toast",
    e.downloads.length === 1 && e.downloads[0].name === "number-check-0521234567.png" && e.toasts.length === 1 && e.toasts[0].m === "تم حفظ الصورة" && e.toasts[0].l === "success", [e.downloads, e.toasts]);
  check("button re-enabled and label restored", e.btn.disabled === false && e.label.textContent === "مشاركة كصورة", [e.btn.disabled, e.label.textContent]);
  check("text was drawn on the canvas", e.drawn.some((d) => d.s === "052-123-4567"), e.drawn.length);

  e = makeEnv({ canShare: true, shareImpl: () => Promise.resolve() });
  const pending = e.click();
  check("busy state: disabled with the preparing label", e.btn.disabled === true && e.label.textContent === "جارٍ التجهيز…", [e.btn.disabled, e.label.textContent]);
  await pending;
  check("canShare: navigator.share with the PNG file, title and text; no download",
    e.shared.length === 1 && e.shared[0].files[0].name === "number-check-0521234567.png" && e.shared[0].text === "نتيجة فحص الرقم 0521234567" && e.downloads.length === 0 && e.toasts.length === 0, [e.shared, e.downloads, e.toasts]);

  e = makeEnv({ canShare: true, shareImpl: () => Promise.reject(Object.assign(new Error("x"), { name: "AbortError" })) });
  await e.click();
  check("AbortError is silent: no download, no toast", e.downloads.length === 0 && e.toasts.length === 0 && e.btn.disabled === false, [e.downloads, e.toasts]);

  e = makeEnv({ canShare: true, shareImpl: () => Promise.reject(Object.assign(new Error("x"), { name: "NotAllowedError" })) });
  await e.click();
  check("other share errors fall back to download + toast", e.downloads.length === 1 && e.toasts[0].m === "تم حفظ الصورة", [e.downloads, e.toasts]);

  e = makeEnv({ noBlob: true });
  await e.click();
  check("real failure: error toast, button restored, nothing downloaded", e.toasts.length === 1 && e.toasts[0].m === "تعذّر تجهيز الصورة" && e.toasts[0].l === "error" && e.downloads.length === 0 && e.btn.disabled === false, [e.toasts, e.downloads]);

  e = makeEnv({ imgFail: true, share: { ...SHARE, line_logo: "/media/x.webp" } });
  await e.click();
  check("image load error never breaks drawing (monogram used)", e.downloads.length === 1 && e.drawn.some((d) => d.s === "س"), [e.downloads, e.toasts]);


  // --- review fixes
  e = makeEnv();
  await e.click();
  check("document.fonts.load gets Arabic and digit sample text for 400/600/700",
    e.fontCalls.length === 3 && e.fontCalls.every(([, t]) => /[\u0600-\u06FF]/.test(t) && /\d/.test(t)) && ["400", "600", "700"].every((w) => e.fontCalls.some(([f]) => f.startsWith(w + " "))), e.fontCalls);

  const logo = {};
  const mkImg = (extra = {}) => ({ tagName: "IMG", hidden: false, closest: (s) => (s === ".rd-ncheck__logo" ? logo : null), ...extra });
  check("capture-phase error listener on document", (e.listeners.error || []).length === 1 && e.captures.error === true, [e.listeners.error, e.captures]);
  const onError = e.listeners.error[0];
  const broken = mkImg(); onError({ target: broken });
  check("image error inside the logo tile hides the img", broken.hidden === true, broken);
  const other = { tagName: "IMG", hidden: false, closest: () => null }; onError({ target: other });
  check("image error outside the logo tile is ignored", other.hidden === false, other);
  const notImg = { tagName: "DIV", hidden: false, closest: () => logo }; onError({ target: notImg });
  check("non-img error targets are ignored", notImg.hidden === false, notImg);
  onError({ target: null }); onError({ target: mkImg({ closest: undefined }) });
  check("odd error targets do not throw", true);
  const doneBroken = mkImg({ complete: true, naturalWidth: 0 }), doneOk = mkImg({ complete: true, naturalWidth: 40 }), loading = mkImg({ complete: false, naturalWidth: 0 });
  e.api.hideBrokenLogos({ querySelectorAll: (sel) => (sel === ".rd-ncheck__logo img" ? [doneBroken, doneOk, loading] : []) });
  check("hideBrokenLogos hides an already-failed img only (not loaded, not still loading)", doneBroken.hidden === true && doneOk.hidden === false && loading.hidden === false, [doneBroken, doneOk, loading]);
  e.api.hideBrokenLogos(null);
  check("hideBrokenLogos tolerates a missing root", true);

  e = makeEnv({ noBlob: true });
  await e.click();
  check("failure logs console.error before the toast", e.logs.error.length === 1 && e.toasts[0].l === "error", [e.logs, e.toasts]);

  e = makeEnv({ canShare: true, shareImpl: () => Promise.reject(Object.assign(new Error("x"), { name: "NotAllowedError" })) });
  await e.click();
  check("NotAllowedError: console.warn, still downloads + toast", e.logs.warn.length === 1 && e.downloads.length === 1 && e.toasts[0].m === "تم حفظ الصورة", [e.logs, e.downloads]);

  e = makeEnv({ canShare: true, shareImpl: () => Promise.reject(Object.assign(new Error("x"), { name: "AbortError" })) });
  await e.click();
  check("AbortError stays silent (no warn)", e.logs.warn.length === 0 && e.logs.error.length === 0, e.logs);

  e = makeEnv({ share: { ...SHARE, phone_display: "" } });
  await e.click();
  check("empty phone: filename is number-check.png", e.downloads.length === 1 && e.downloads[0].name === "number-check.png", e.downloads);

  console.log(fail ? "FAILED" : "ALL OK"); process.exit(fail ? 1 : 0);
})();
