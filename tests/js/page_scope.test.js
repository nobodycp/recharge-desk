// Plain-node tests for static/js/page-scope.js (stub window/document/htmx in a vm).
// Run from anywhere:
//   node tests/js/page_scope.test.js
// No dependencies; exits non-zero on failure.
const fs = require("fs"), vm = require("vm"), path = require("path"), assert = require("assert");
const src = fs.readFileSync(path.join(__dirname, "..", "..", "static", "js", "page-scope.js"), "utf8");

// Minimal EventTarget (the real prototype is patched by page-scope.js, so every
// setup() gets a fresh class).
function makeEventTargetClass() {
  return class FakeEventTarget {
    addEventListener(type, fn) {
      if (!this.__l) Object.defineProperty(this, "__l", { value: [], enumerable: false, writable: true });
      if (!this.__l.some((l) => l.type === type && l.fn === fn)) this.__l.push({ type, fn });
    }
    removeEventListener(type, fn) {
      if (!this.__l) return;
      this.__l = this.__l.filter((l) => !(l.type === type && l.fn === fn));
    }
  };
}
const listenersOf = (t, type) => ((t.__l || []).filter((l) => l.type === type)).map((l) => l.fn);

// Fake htmx mirroring the 1.9.12 contract: onLoad(fn) registers and returns a
// wrapper on "htmx:load"; off("htmx:load", wrapper) removes it.
function makeHtmx(env) {
  const h = {
    marker: "real-htmx",
    live: new Set(),
    offCalls: [],
    onLoad(fn) {
      const wrapper = (e) => fn(e && e.detail && e.detail.elt);
      h.live.add(wrapper);
      return wrapper;
    },
    off(evt, wrapper) { h.offCalls.push([evt, wrapper]); h.live.delete(wrapper); },
    process() { return "processed"; },
  };
  return h;
}

function setup({ htmxFirst = false } = {}) {
  const env = {};
  const FakeEventTarget = makeEventTargetClass();
  const ctx = {};
  ctx.window = ctx; ctx.self = ctx; ctx.EventTarget = FakeEventTarget;
  ctx.setInterval = (fn, ms) => { env.nextId = (env.nextId || 0) + 1; env.intervals.add(env.nextId); return env.nextId; };
  ctx.clearInterval = (id) => { env.intervals.delete(id); };
  env.intervals = new Set();
  const headScript = { name: "head-script" }, bodyScript = { name: "body-script" };
  const body = new FakeEventTarget();
  body.contains = (n) => n === bodyScript;
  const document = new FakeEventTarget();
  document.body = body; document.currentScript = null;
  ctx.document = document;
  env.ctx = ctx; env.document = document; env.body = body;
  env.htmx = makeHtmx(env);
  if (htmxFirst) ctx.htmx = env.htmx;
  vm.runInContext(src, vm.createContext(ctx));
  // Mimic the htmx UMD wrapper: `e.htmx = e.htmx || t()`.
  env.loadHtmx = () => vm.runInContext("self.htmx = self.htmx || __make()", Object.assign(ctx, { __make: () => env.htmx }));
  env.asHead = (fn) => { document.currentScript = headScript; try { return fn(); } finally { document.currentScript = null; } };
  env.asBody = (fn) => { document.currentScript = bodyScript; try { return fn(); } finally { document.currentScript = null; } };
  env.fire = (target, type, evt) => listenersOf(target, type).slice().forEach((fn) => fn(evt));
  env.bodySwap = (extra) => env.fire(ctx, "htmx:beforeSwap", { detail: Object.assign({ shouldSwap: true, target: body }, extra) });
  return env;
}

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("window.htmx stays a normal, working object after load", () => {
  const env = setup();
  assert.strictEqual(env.ctx.htmx, undefined);
  env.loadHtmx();
  assert.strictEqual(env.ctx.htmx, env.htmx);
  assert.strictEqual(env.ctx.htmx.marker, "real-htmx");
  assert.strictEqual(env.ctx.htmx.process(), "processed");
  const d = Object.getOwnPropertyDescriptor(env.ctx, "htmx");
  assert.ok("value" in d && d.writable && d.configurable && d.enumerable, "plain data property");
  env.ctx.htmx = { other: 1 }; // later assignments are plain writes
  assert.strictEqual(env.ctx.htmx.other, 1);
});

test("htmx loaded before page-scope is patched too", () => {
  const env = setup({ htmxFirst: true });
  const fn = () => {};
  const w = env.asBody(() => env.ctx.htmx.onLoad(fn));
  env.bodySwap();
  assert.deepStrictEqual(env.htmx.offCalls, [["htmx:load", w]]);
});

test("page-owned onLoad returns the wrapper and is removed on a body swap", () => {
  const env = setup(); env.loadHtmx();
  let ran = 0;
  const w = env.asBody(() => env.ctx.htmx.onLoad(() => { ran++; }));
  assert.strictEqual(typeof w, "function");
  assert.ok(env.htmx.live.has(w));
  w({ detail: { elt: {} } });
  assert.strictEqual(ran, 1, "callback still runs through the wrapper");
  env.bodySwap();
  assert.deepStrictEqual(env.htmx.offCalls, [["htmx:load", w]]);
  assert.strictEqual(env.htmx.live.size, 0);
});

test("head-owned onLoad is never removed", () => {
  const env = setup(); env.loadHtmx();
  const w = env.asHead(() => env.ctx.htmx.onLoad(() => {}));
  env.bodySwap(); env.fire(env.ctx, "htmx:historyCacheMissLoad", {});
  assert.deepStrictEqual(env.htmx.offCalls, []);
  assert.ok(env.htmx.live.has(w));
});

test("onLoad with no current script and no page phase is not removed", () => {
  const env = setup(); env.loadHtmx();
  const w = env.ctx.htmx.onLoad(() => {});
  env.bodySwap();
  assert.deepStrictEqual(env.htmx.offCalls, []);
  assert.ok(env.htmx.live.has(w));
});

test("same fn registered on two visits leaves exactly one live registration", () => {
  const env = setup(); env.loadHtmx();
  const init = () => {};
  env.asBody(() => env.ctx.htmx.onLoad(init));
  env.bodySwap(); // leave page 1
  env.asBody(() => env.ctx.htmx.onLoad(init)); // visit 2
  assert.strictEqual(env.htmx.live.size, 1);
  env.bodySwap();
  env.asBody(() => env.ctx.htmx.onLoad(init)); // visit 3
  assert.strictEqual(env.htmx.live.size, 1);
  assert.strictEqual(env.htmx.offCalls.length, 2);
});

test("same fn registered twice in one visit is a single registration", () => {
  const env = setup(); env.loadHtmx();
  const init = () => {};
  const a = env.asBody(() => env.ctx.htmx.onLoad(init));
  const b = env.asBody(() => env.ctx.htmx.onLoad(init));
  assert.strictEqual(a, b);
  assert.strictEqual(env.htmx.live.size, 1);
  env.bodySwap();
  assert.strictEqual(env.htmx.live.size, 0);
});

test("onLoad from inside a page-owned listener (after the script ended) is owned", () => {
  const env = setup(); env.loadHtmx();
  let w;
  env.asBody(() => env.document.addEventListener("click", () => { w = env.ctx.htmx.onLoad(() => {}); }));
  env.fire(env.document, "click", {}); // currentScript is null here
  assert.ok(w && env.htmx.live.has(w));
  env.bodySwap();
  assert.strictEqual(env.htmx.live.size, 0);
});

test("partial swaps and refused swaps keep registrations", () => {
  const env = setup(); env.loadHtmx();
  env.asBody(() => env.ctx.htmx.onLoad(() => {}));
  env.bodySwap({ target: {} });
  env.bodySwap({ shouldSwap: false });
  assert.deepStrictEqual(env.htmx.offCalls, []);
  assert.strictEqual(env.htmx.live.size, 1);
});

test("history cache miss load also clears page onLoad callbacks", () => {
  const env = setup(); env.loadHtmx();
  env.asBody(() => env.ctx.htmx.onLoad(() => {}));
  env.fire(env.ctx, "htmx:historyCacheMissLoad", {});
  assert.strictEqual(env.htmx.live.size, 0);
});

test("existing behaviour: document/body/window listeners and intervals are cleaned up", () => {
  const env = setup(); env.loadHtmx();
  const d = () => {}, b = () => {}, w = () => {}, headFn = () => {};
  env.asBody(() => {
    env.document.addEventListener("click", d);
    env.body.addEventListener("click", b);
    vm.runInContext("EventTarget.prototype.addEventListener.call(window, 'resize', __w)", Object.assign(env.ctx, { __w: w }));
    env.ctx.setInterval(() => {}, 10);
  });
  env.asHead(() => {
    env.document.addEventListener("keydown", headFn);
    env.ctx.setInterval(() => {}, 10);
  });
  assert.strictEqual(env.intervals.size, 2);
  env.bodySwap();
  assert.strictEqual(listenersOf(env.document, "click").length, 0);
  assert.strictEqual(listenersOf(env.body, "click").length, 0);
  assert.strictEqual(listenersOf(env.ctx, "resize").length, 0);
  assert.strictEqual(listenersOf(env.document, "keydown").length, 1, "head listener kept");
  assert.strictEqual(env.intervals.size, 1, "head interval kept");
});

let failed = 0;
for (const [name, fn] of tests) {
  try { fn(); console.log("ok   - " + name); } catch (e) { failed++; console.log("FAIL - " + name + "\n  " + (e && e.stack || e)); }
}
if (failed) { console.log(failed + " failed"); process.exit(1); }
console.log("all " + tests.length + " passed");
