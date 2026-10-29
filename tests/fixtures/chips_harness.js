// Task 24: the quick-prompt set follows the KEDD state — the KEDD chips show only
// when the business is live, the mock boutique/salon chips otherwise.
// Usage: node chips_harness.js <app.js path> <live|fallback>
// Drives app.js with stub DOM + stub fetch. Exits 0 on PASS, 1 on FAIL.
const fs = require("fs");

const [appPath, scenario] = process.argv.slice(2, 4);
if (!appPath || !["live", "fallback"].includes(scenario)) {
  console.error("usage: node chips_harness.js <app.js> <live|fallback>");
  process.exit(2);
}

function makeEl() {
  return {
    value: "", hidden: false, innerHTML: "", textContent: "", src: "", alt: "",
    className: "", dataset: {}, files: [], scrollTop: 0, scrollHeight: 0,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    setAttribute() {}, removeAttribute() {}, focus() {}, click() {},
    appendChild(c) { return c; },
    remove() { this.removed = true; },
    addEventListener(ev, fn) { ((this._h = this._h || {})[ev] = (this._h[ev] || [])).push(fn); },
  };
}

const byId = {};
const listeners = {};
global.document = {
  getElementById: (id) => (byId[id] = byId[id] || makeEl()),
  createElement: () => makeEl(),
  querySelectorAll: () => [],
  addEventListener: (ev, fn) => { listeners[ev] = fn; },
  documentElement: {},
  title: "",
};
global.window = { I18N: { en: {} } };
global.navigator = { language: "en" };
global.localStorage = { getItem: () => null, setItem: () => {} };

global.fetch = (url) => {
  const u = String(url);
  if (u.includes("/api/kedd-state")) {
    const live = scenario === "live";
    return Promise.resolve({
      ok: true,
      json: async () => ({
        state: live ? "live" : "fallback",
        business_name: live ? "KEDD" : "salon",
        catalog_sample: [], appointments_today: { count: 0, items: [] },
        recent_writes: [], writes_window_hours: live ? 24 : null,
        error: live ? null : "down",
      }),
    });
  }
  if (u.includes("/api/trace")) {
    return Promise.resolve({ ok: true, json: async () => ({ steps: [], models: [] }) });
  }
  if (u.includes("coverage.json")) {
    return Promise.resolve({ ok: true, json: async () => [] });
  }
  return Promise.resolve({ ok: false, json: async () => ({}) });
};

(async () => {
  const fail = (msg) => { console.error(`FAIL [${scenario}]: ${msg}`); process.exit(1); };
  // eslint-disable-next-line no-eval
  eval(fs.readFileSync(appPath, "utf8"));
  if (typeof listeners.DOMContentLoaded !== "function") return fail("landing never initialized");
  listeners.DOMContentLoaded();
  for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r)); // flush the async chain
  const mock = byId.quickMock, kedd = byId.quickKedd;
  if (!mock || !kedd) return fail("chip sets were never toggled");
  const wantLive = scenario === "live";
  if (mock.hidden !== wantLive || kedd.hidden !== !wantLive) {
    return fail(`quickMock.hidden=${mock.hidden} quickKedd.hidden=${kedd.hidden}`);
  }
  console.log(`PASS [${scenario}]`);
})().catch((e) => { console.error(`FAIL [${scenario}]: ${e.stack}`); process.exit(1); });
