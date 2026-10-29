// Task 15, item 17: a queued approval word must NOT auto-send after a card arrives.
// Usage: node web_queue_harness.js <app.js path> <hold|pass>
// Drives app.js with stub DOM + stub fetch. Exits 0 on PASS, 1 on FAIL.
const fs = require("fs");

const [appPath, scenario] = process.argv.slice(2, 4);
const QUEUED_TEXT = scenario === "hold" ? "evet" : "fiyat ne kadar";
if (!appPath || !["hold", "pass"].includes(scenario)) {
  console.error("usage: node web_queue_harness.js <app.js> <hold|pass>");
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
global.document = {
  getElementById: (id) => (byId[id] = byId[id] || makeEl()),
  createElement: () => makeEl(),
  querySelectorAll: () => [],
  addEventListener() {},
};
global.FormData = class {
  constructor() { this.fields = []; }
  append(k, v, n) { this.fields.push([k, v, n]); }
};

const fetchCalls = [];
let releaseFirst;
global.fetch = () => {
  fetchCalls.push(1);
  if (fetchCalls.length === 1) return new Promise((res) => { releaseFirst = res; });
  return Promise.resolve({ ok: true, json: async () => ({ replies: [{ text: "tamam" }], status: [] }) });
};

const CARD = "*Çırak — onayına sunuyorum*\n🛍️ Ürün: Fular — 850 ₺\n\n"
  + "Onaylıyor musun? (evet / hayır)";

(async () => {
  const fail = (msg) => { console.error(`FAIL [${scenario}]: ${msg}`); process.exit(1); };
  // eslint-disable-next-line no-eval
  eval(fs.readFileSync(appPath, "utf8"));
  const clickSend = byId.sendBtn._h.click[0];
  const pressEnter = byId.textInput._h.keydown[0];

  byId.textInput.value = "fular ekle 850 lira";
  clickSend(); // busy now; first fetch is pending
  if (fetchCalls.length !== 1) return fail(`expected 1 fetch after first send, got ${fetchCalls.length}`);

  byId.textInput.value = QUEUED_TEXT;
  pressEnter({ key: "Enter" }); // queued while busy
  if (byId.textInput.value !== "") return fail("input was not cleared when queueing");

  releaseFirst({ ok: true, json: async () => (
    { replies: [{ text: CARD }], status: [], business_name: "Nar Butik", business: "butik" }) });
  for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r)); // flush the async chain

  if (scenario === "hold") {
    if (fetchCalls.length !== 1) return fail(`queued approval was auto-sent (${fetchCalls.length} fetches)`);
    if (byId.textInput.value !== QUEUED_TEXT) {
      return fail(`queued text not restored (got ${JSON.stringify(byId.textInput.value)})`);
    }
  } else {
    if (fetchCalls.length !== 2) return fail(`ordinary message was not auto-sent (${fetchCalls.length} fetches)`);
    if (byId.textInput.value !== "") return fail("input should stay empty after auto-send");
  }
  console.log(`PASS [${scenario}]`);
})().catch((e) => { console.error(`FAIL [${scenario}]: ${e.stack}`); process.exit(1); });
