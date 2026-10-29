(() => {
  const chat = document.getElementById("chat");
  const textInput = document.getElementById("textInput");
  const sendBtn = document.getElementById("sendBtn");
  const attachBtn = document.getElementById("attachBtn");
  const fileInput = document.getElementById("fileInput");
  const micBtn = document.getElementById("micBtn");
  const resetBtn = document.getElementById("resetBtn");
  const preview = document.getElementById("preview");
  const businessNameEl = document.getElementById("businessName");
  const badgeEl = document.getElementById("badge");
  const lightbox = document.getElementById("lightbox");
  const lightboxImg = document.getElementById("lightboxImg");
  const tryLiveBtn = document.getElementById("tryLive");
  const phoneColEl = document.getElementById("phoneCol");
  const phoneTimeEl = document.getElementById("phoneTime");
  const coverageEl = document.getElementById("coverage");
  const traceTimelineEl = document.getElementById("traceTimeline");
  const traceEmptyEl = document.getElementById("traceEmpty");
  const traceModelsEl = document.getElementById("traceModels");
  const traceMiniEl = document.getElementById("traceMini");
  const traceMiniEmptyEl = document.getElementById("traceMiniEmpty");
  const syntheticLineEl = document.getElementById("syntheticLine");
  let picked = [];
  let busy = false;
  let queued = null;   // at most one: {kind:"message",...} | {kind:"brief"}, each with .el
  let typingEl = null;

  function scrollDown() { chat.scrollTop = chat.scrollHeight; }

  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // WhatsApp *bold* + line breaks.
  function markup(s) {
    return esc(s).replace(/\*([^*\n]+?)\*/g, "<strong>$1</strong>").replace(/\n/g, "<br>");
  }

  // Card titles in every owner language (app/i18n.py "card.title"): the queue-hold guard depends on this.
  const CARD_MARKERS = ["onayına sunuyorum", "for your approval"];
  function isCard(text) {
    return CARD_MARKERS.some((m) => (text || "").includes(m));
  }

  // Approval cards render as a real card with one-tap evet/hayır buttons. Everything is
  // textContent (tool/model strings must never become HTML).
  function renderCard(el, text) {
    const card = document.createElement("div");
    card.className = "card";
    let title = "Çırak — onayına sunuyorum", note = "";
    const rows = [];
    let current = null;
    for (const raw of text.split("\n")) {
      const line = raw.trimEnd();
      if (!line.trim()) continue;
      if (line.startsWith("*") && line.endsWith("*") && line.length > 2) {
        title = line.slice(1, -1);
        continue;
      }
      if (line.startsWith("Çırak'ın notu:")) { note = line; continue; }
      if (line.includes("Onaylıyor musun?")) continue;
      if (/^\s/.test(raw)) {
        if (current) current.sub = current.sub ? current.sub + "\n" + line.trim() : line.trim();
        continue;
      }
      current = { main: line, sub: "" };
      rows.push(current);
    }
    const t = document.createElement("div");
    t.className = "card-title";
    t.textContent = title;
    card.appendChild(t);
    const list = document.createElement("div");
    list.className = "card-rows";
    for (const r of rows) {
      const d = document.createElement("div");
      d.className = "card-row";
      d.textContent = r.main;
      if (r.sub) {
        const s = document.createElement("span");
        s.className = "sub";
        s.textContent = r.sub;
        d.appendChild(s);
      }
      list.appendChild(d);
    }
    card.appendChild(list);
    if (note) {
      const n = document.createElement("div");
      n.className = "card-note";
      n.textContent = note;
      card.appendChild(n);
    }
    const acts = document.createElement("div");
    acts.className = "card-actions";
    const ok = document.createElement("button");
    ok.type = "button";
    ok.className = "approve";
    ok.textContent = "Evet ✓";
    ok.addEventListener("click", () => send({ text: "evet" }));
    const no = document.createElement("button");
    no.type = "button";
    no.className = "reject";
    no.textContent = "Hayır";
    no.addEventListener("click", () => send({ text: "hayır" }));
    acts.appendChild(ok);
    acts.appendChild(no);
    card.appendChild(acts);
    el.appendChild(card);
  }

  function addBubble(side, text, imageUrl, extraClass) {
    const d = document.createElement("div");
    d.className = "bubble " + side + (extraClass ? " " + extraClass : "");
    if (imageUrl) {
      const img = document.createElement("img");
      img.src = imageUrl;
      img.alt = "Kampanya görseli";
      img.loading = "lazy";
      d.appendChild(img);
    }
    if (text) {
      if (side === "them" && isCard(text)) {
        renderCard(d, text);
      } else {
        const t = document.createElement("div");
        t.innerHTML = markup(text);
        d.appendChild(t);
      }
    }
    chat.appendChild(d);
    scrollDown();
    return d;
  }

  function addStatus(text) {
    const d = document.createElement("div");
    d.className = "system";
    d.textContent = text;
    chat.appendChild(d);
    scrollDown();
  }

  function setBusiness(name, business) {
    if (name) businessNameEl.textContent = name;
    const demo = business === "kedd-demo";
    const kedd = demo || business === "kedd";
    badgeEl.textContent = demo ? "KEDD demo" : (business === "kedd" ? "KEDD" : "demo");
    badgeEl.className = "badge " + (kedd ? "kedd" : "demo");
  }

  function showTyping() {
    if (typingEl) return;
    typingEl = document.createElement("div");
    typingEl.className = "bubble them typing";
    typingEl.setAttribute("aria-label", "Çırak yazıyor");
    const label = document.createElement("span");
    label.className = "typing-label";
    label.textContent = "Çırak yazıyor…";
    const dots = document.createElement("span");
    dots.className = "dots";
    dots.innerHTML = "<i></i><i></i><i></i>";
    typingEl.appendChild(label);
    typingEl.appendChild(dots);
    chat.appendChild(typingEl);
    scrollDown();
  }

  function hideTyping() {
    if (typingEl) {
      typingEl.remove();
      typingEl = null;
    }
  }

  function setBusy(on) {
    busy = on;
    sendBtn.classList.toggle("busy", on);
  }

  async function postForm(url, body) {
    const r = await fetch(url, { method: "POST", body });
    if (!r.ok) throw new Error("http " + r.status);
    return r.json();
  }

  // Anything queued while a plan was running is held once its card arrives: the owner wrote it
  // before reading the card, so it must never approve it (a word list here drifted from core.YES).
  let cardArrived = false;

  function renderResponse(data) {
    cardArrived = false;
    for (const s of data.status || []) addStatus(s);
    for (const rep of data.replies || []) {
      addBubble("them", rep.text || "", rep.image_url || null);
      if (isCard(rep.text)) cardArrived = true;
    }
    setBusiness(data.business_name, data.business);
    if (landingReady) {
      refreshTrace();
      refreshKedd(false);
    }
  }

  function drainQueue() {
    if (!queued || busy) return;
    const q = queued;
    queued = null;
    if (q.kind === "message" && cardArrived) {
      cardArrived = false;
      if (q.el) q.el.remove();
      textInput.value = q.text || "";
      textInput.focus();
      addStatus("Kart geldi — sıradaki mesajını otomatik göndermedim, karta bakıp sen gönder."
        + (q.images.length || q.audioBlob ? " Fotoğraf/ses notunu tekrar eklemen gerek." : ""));
      return;
    }
    if (q.kind === "brief") runBrief(q.el);
    else runMessage({ text: q.text, audioBlob: q.audioBlob, images: q.images, pendingEl: q.el });
  }

  // Returns false (after a system line) when the single queue slot is taken.
  function enqueue(item) {
    if (queued) {
      addStatus("Bir önceki isteğini bitiriyorum…");
      return false;
    }
    queued = item;
    return true;
  }

  async function runMessage({ text, audioBlob, images, pendingEl }) {
    setBusy(true);
    showTyping();
    if (pendingEl) {
      pendingEl.classList.remove("pending");   // the grey bubble turns into the sent one
    } else if (text) {
      addBubble("me", text, null);
    } else if (images.length) {
      addBubble("me", `📷 ${images.length} fotoğraf`, null);
    } else if (audioBlob) {
      addBubble("me", "🎙️ Sesli not", null);
    }
    const fd = new FormData();
    if (text) fd.append("text", text);
    for (const f of images) fd.append("images[]", f, f.name);
    if (audioBlob) fd.append("audio", audioBlob, "voice.webm");
    try {
      renderResponse(await postForm("/api/message", fd));
    } catch (e) {
      addStatus("Bağlantı koptu, tekrar dener misin?");
    } finally {
      hideTyping();
      setBusy(false);
      drainQueue();
    }
  }

  async function runBrief(pendingEl) {
    setBusy(true);
    showTyping();
    if (pendingEl) pendingEl.classList.remove("pending");
    else addBubble("me", "☀️ Sabah özeti", null);
    try {
      renderResponse(await postForm("/api/brief", null));
    } catch (e) {
      addStatus("Bağlantı koptu, tekrar dener misin?");
    } finally {
      hideTyping();
      setBusy(false);
      drainQueue();
    }
  }

  async function send({ text, audioBlob } = {}) {
    text = (text ?? textInput.value).trim();
    const images = [...picked];
    if (!text && !audioBlob && images.length === 0) return;
    picked = [];
    renderPreview();
    textInput.value = "";
    if (busy) {
      const label = text || (images.length ? `📷 ${images.length} fotoğraf` : "🎙️ Sesli not");
      if (!enqueue({ kind: "message", text, audioBlob, images })) return;
      queued.el = addBubble("me", label, null, "pending");
      return;
    }
    await runMessage({ text, audioBlob, images, pendingEl: null });
  }

  async function requestBrief() {
    if (busy) {
      if (!enqueue({ kind: "brief" })) return;
      queued.el = addBubble("me", "☀️ Sabah özeti", null, "pending");
      return;
    }
    await runBrief(null);
  }

  function renderPreview() {
    preview.innerHTML = "";
    preview.hidden = picked.length === 0;
    for (const f of picked) {
      const img = document.createElement("img");
      img.src = URL.createObjectURL(f);
      img.alt = f.name;
      preview.appendChild(img);
    }
  }

  sendBtn.addEventListener("click", () => send());
  textInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") send();
  });
  attachBtn.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    picked = [...picked, ...fileInput.files].filter((f) => /jpe?g|png/i.test(f.type + " " + f.name));
    fileInput.value = "";
    renderPreview();
  });
  document.querySelectorAll(".quick button").forEach((b) => {
    b.addEventListener("click", () => {
      if (b.dataset.brief) {
        requestBrief();
        return;
      }
      if (b.dataset.send) {
        send({ text: b.dataset.send });
        return;
      }
      textInput.value = b.dataset.fill || "";
      textInput.focus();
    });
  });
  resetBtn.addEventListener("click", async () => {
    if (busy) return;
    setBusy(true);
    try {
      const data = await postForm("/api/reset", null);
      chat.innerHTML = "";
      for (const rep of data.replies || []) addBubble("them", rep.text || "", rep.image_url || null);
      setBusiness(data.business_name, data.business);
    } finally {
      setBusy(false);
    }
  });

  // Images in bubbles open full size in a lightbox.
  chat.addEventListener("click", (e) => {
    const img = e.target && e.target.closest ? e.target.closest(".bubble img") : null;
    if (img && img.src) {
      lightboxImg.src = img.src;
      lightbox.hidden = false;
    }
  });
  lightbox.addEventListener("click", () => {
    lightbox.hidden = true;
    lightboxImg.removeAttribute("src");
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !lightbox.hidden) {
      lightbox.hidden = true;
      lightboxImg.removeAttribute("src");
    }
  });

  // Press-and-hold voice note.
  let recorder = null, chunks = [];
  async function startRec(e) {
    e.preventDefault();
    if (recorder) return;
    if (busy) {
      addStatus("Bir önceki isteğini bitiriyorum…");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
      recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      chunks = [];
      recorder.ondataavailable = (ev) => { if (ev.data.size) chunks.push(ev.data); };
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunks, { type: "audio/webm" });
        recorder = null;
        micBtn.classList.remove("rec");
        if (blob.size) send({ text: "", audioBlob: blob });
      };
      recorder.start();
      micBtn.classList.add("rec");
    } catch {
      addStatus("Mikrofona erişemedim.");
    }
  }
  function stopRec(e) {
    if (e) e.preventDefault();
    if (recorder && recorder.state !== "inactive") recorder.stop();
  }
  micBtn.addEventListener("mousedown", startRec);
  micBtn.addEventListener("mouseup", stopRec);
  micBtn.addEventListener("mouseleave", stopRec);
  micBtn.addEventListener("touchstart", startRec, { passive: false });
  micBtn.addEventListener("touchend", stopRec);
  micBtn.addEventListener("touchcancel", stopRec);

  addBubble("them", "Merhaba, ben Çırak 👋 Ne yapmak istersin? Yaz ya da sesli not bırak.", null);

  /* ---- landing layer: i18n, coverage, trace. Wired on DOMContentLoaded so the chat
   * core above stays dependency-free; renderResponse refreshes the trace once ready. */
  let landingReady = false;
  let lang = "en";
  let coverageItems = [];

  const STR = () => (window.I18N && (window.I18N[lang] || window.I18N.en)) || {};
  function t(key) {
    return STR()[key] || (window.I18N && window.I18N.en && window.I18N.en[key]) || key;
  }

  function getLang() {
    try {
      const s = localStorage.getItem("cirak-lang");
      if (s === "tr" || s === "en") return s;
    } catch (e) { /* private mode: fall through to the browser language */ }
    const n = (typeof navigator !== "undefined" && navigator.language) || "en";
    return n.toLowerCase().startsWith("tr") ? "tr" : "en";
  }

  // Quick-prompt chips fill the composer in the page language. The Turkish fills live in
  // index.html (data-fill); the English versions below swap in when the toggle is EN.
  const QUICK_EN = new Map([
    ["Yeni ipek fular koleksiyonu ekle, tanesi 850 lira, hafta sonu %15 indirim, uzun süredir gelmeyenlere haber ver",
     "Add a new silk scarf collection, 850 lira each, 15% off this weekend, tell customers who haven't visited"],
    ["Yarın öğleden sonra boşluklarım var, saç boyamada %20 indirim, sadık müşterilere haber ver. Bir de keratin bakımı ekle, 90 dakika, 2500 lira.",
     "I have free slots tomorrow afternoon, 20% off hair coloring, tell loyal customers. Also add keratin treatment, 90 minutes, 2500 lira."],
  ]);

  function paintQuick() {
    for (const b of document.querySelectorAll(".quick button")) {
      if (b.dataset.fill === undefined) continue;   // brief + /kedd chips carry no fill text
      if (b.dataset.fillTr === undefined) b.dataset.fillTr = b.dataset.fill;
      const tr = b.dataset.fillTr;
      b.dataset.fill = (lang === "en" && QUICK_EN.has(tr)) ? QUICK_EN.get(tr) : tr;
    }
  }

  function applyLang(next) {
    if (!window.I18N) return;
    lang = next;
    try { localStorage.setItem("cirak-lang", lang); } catch (e) { /* private mode */ }
    document.documentElement.lang = lang;
    document.title = t("meta.title");
    for (const el of document.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
    for (const el of document.querySelectorAll("[data-i18n-aria]")) {
      el.setAttribute("aria-label", t(el.dataset.i18nAria));
    }
    for (const b of document.querySelectorAll("#langToggle button")) {
      b.setAttribute("aria-pressed", b.dataset.lang === lang ? "true" : "false");
    }
    paintCoverage();
    paintKedd();
    paintQuick();
  }

  function paintCoverage() {
    coverageEl.innerHTML = "";
    for (const it of coverageItems) {
      const card = document.createElement("div");
      card.className = "cov-card";
      const area = document.createElement("div");
      area.className = "cov-area";
      area.textContent = it.area || "";
      const what = document.createElement("p");
      what.className = "cov-what";
      what.textContent = (lang === "tr" ? it.what_tr : it.what_en) || "";
      const pill = document.createElement("span");
      pill.className = "pill " + (it.status === "live" ? "live" : "soon");
      pill.textContent = it.status === "live" ? t("cov.live") : t("cov.soon");
      card.appendChild(area);
      card.appendChild(what);
      card.appendChild(pill);
      coverageEl.appendChild(card);
    }
  }

  async function loadCoverage() {
    try {
      const r = await fetch("/static/coverage.json");
      if (!r.ok) throw new Error("coverage " + r.status);
      coverageItems = await r.json();
    } catch (e) {
      coverageEl.innerHTML = "";
      const p = document.createElement("p");
      p.className = "trace-empty";
      p.textContent = t("cov.fail");
      coverageEl.appendChild(p);
      return;
    }
    paintCoverage();
  }

  const TOOL_META = {
    get_store_profile: ["🏪", "store profile"], list_catalog: ["📋", "catalog"],
    find_customers: ["👥", "segment"], find_customer: ["👤", "customer"],
    get_schedule: ["🗓", "schedule"], find_appointments: ["📅", "appointments"],
    get_daily_summary: ["☀️", "daily summary"], preview_bulk_price: ["💸", "price preview"],
    get_ads_performance: ["📢", "ads"], get_ads_suggestions: ["💡", "ad tips"],
    list_campaign_templates: ["📝", "templates"], propose_actions: ["🗂", "plan"],
    reply_only: ["💬", "answer"],
  };

  function shortModel(id) {
    const s = String(id || "").toLowerCase();
    if (s.includes("spark")) return "Muse Spark";
    if (s.includes("sam")) return "SAM 3.1";
    if (s.includes("image")) return "Muse Image";
    if (s.includes("voice") || s.includes("transcrib") || s.includes("asr")) return "Muse Voice";
    if (s.includes("llama")) return "Llama";
    return String(id || "").slice(0, 32);
  }

  function paintTrace(ol, emptyEl, data) {
    ol.innerHTML = "";
    if (!data || !data.steps || !data.steps.length) {
      emptyEl.hidden = false;
      return;
    }
    emptyEl.hidden = true;
    for (const step of data.steps) {
      const li = document.createElement("li");
      const [icon, label] = TOOL_META[step.tool] || ["•", step.tool];
      const ico = document.createElement("span");
      ico.className = "t-ico";
      ico.textContent = icon;
      li.appendChild(ico);
      li.appendChild(document.createTextNode(label));
      if (typeof step.count === "number") {
        const c = document.createElement("span");
        c.className = "t-count";
        c.textContent = ` — ${step.count} customers`;
        li.appendChild(c);
      }
      ol.appendChild(li);
    }
  }

  function paintModels(data) {
    traceModelsEl.innerHTML = "";
    if (!data || !data.models || !data.models.length) {
      traceModelsEl.textContent = "—";
      return;
    }
    for (const m of data.models) {
      const b = document.createElement("span");
      b.className = "model";
      b.textContent = shortModel(m);
      traceModelsEl.appendChild(b);
      traceModelsEl.appendChild(document.createTextNode(" "));
    }
  }

  async function refreshTrace() {
    let data = null;
    try {
      const r = await fetch("/api/trace");
      if (r.ok) data = await r.json();
    } catch (e) { /* offline or no run yet: keep the empty state */ }
    paintTrace(traceTimelineEl, traceEmptyEl, data);
    paintTrace(traceMiniEl, traceMiniEmptyEl, data);
    paintModels(data);
  }

  /* ---- Live-in-KEDD panel: read-only proof of the demo business. All server
   * strings land in textContent, like the approval card renderer above. */
  const keddPillEl = document.getElementById("keddPill");
  const keddBusinessEl = document.getElementById("keddBusiness");
  const keddRowsEl = document.getElementById("keddRows");
  const keddRetryEl = document.getElementById("keddRetry");
  const keddErrorEl = document.getElementById("keddError");
  let keddData = null;

  function keddRow(title, lines) {
    const box = document.createElement("div");
    box.className = "kedd-row";
    const h = document.createElement("h3");
    h.textContent = title;
    box.appendChild(h);
    const ul = document.createElement("ul");
    for (const line of lines) {
      const li = document.createElement("li");
      li.textContent = line;
      ul.appendChild(li);
    }
    box.appendChild(ul);
    keddRowsEl.appendChild(box);
  }

  function paintKedd() {
    if (!keddRowsEl) return;
    keddRowsEl.innerHTML = "";
    if (!keddData) {
      keddPillEl.textContent = t("kedd.off");
      keddPillEl.className = "pill soon";
      return;
    }
    const d = keddData;
    const pill = d.state === "live" ? ["kedd.live", "live"]
      : d.state === "fallback" ? ["kedd.fallback", "fallback"] : ["kedd.mock", "soon"];
    keddPillEl.textContent = t(pill[0]);
    keddPillEl.className = "pill " + pill[1];
    keddBusinessEl.textContent = d.business_name || "";
    keddRetryEl.hidden = d.state !== "fallback";
    keddErrorEl.hidden = !d.error;
    keddErrorEl.textContent = d.error || "";
    const sample = d.catalog_sample || [];
    keddRow(t("kedd.catalog"), sample.length ? sample.map((it) => {
      const price = typeof it.price === "number" ? ` · ${it.price} ₺` : "";
      return `${it.name || "?"}${price}`;
    }) : [t("kedd.nowrites")]);
    const appts = (d.appointments_today && d.appointments_today.items) || [];
    const n = (d.appointments_today && d.appointments_today.count) || 0;
    keddRow(`${t("kedd.appts")} (${n})`, appts.length ? appts.map((a) => {
      const who = a.staff ? ` · ${a.staff}` : "";
      return `${a.time || "?"} ${a.service || "?"}${who}`;
    }) : [t("kedd.nowrites")]);
    const writes = d.recent_writes || [];
    const wTitle = t("kedd.writes") + (d.writes_window_hours ? ` (${d.writes_window_hours}h)` : "");
    keddRow(wTitle, writes.length ? writes.map((w) => (w.capability ? keddWriteLabel(w)
      : (w.label || w.kind || "?"))) : [t("kedd.nowrites")]);
  }

  // Every capability the server can send, as full i18n keys (unknown future ones
  // render as the raw key rather than breaking the panel).
  const KEDD_WRITE_KEYS = ["kedd.w.catalog.create", "kedd.w.catalog.update",
    "kedd.w.catalog.bulkPriceApply", "kedd.w.discounts.create", "kedd.w.appointments",
    "kedd.w.campaigns.send"];

  function keddWriteLabel(w) {
    const key = "kedd.w." + w.capability;
    const name = KEDD_WRITE_KEYS.includes(key) ? t(key) : String(w.capability || "?");
    const base = `${name} × ${w.ok || 0}`;
    return w.error ? `${base} (${w.error} ${t("kedd.werr")})` : base;
  }

  async function refreshKedd(fresh) {
    try {
      const r = await fetch("/api/kedd-state" + (fresh ? "?fresh=1" : ""));
      if (r.ok) keddData = await r.json();
    } catch (e) { /* keep the last painted state */ }
    paintKedd();
  }

  function landingInit() {
    if (landingReady) return;
    landingReady = true;
    applyLang(getLang());
    for (const b of document.querySelectorAll("#langToggle button")) {
      b.addEventListener("click", () => applyLang(b.dataset.lang));
    }
    tryLiveBtn.addEventListener("click", () => {
      const calm = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
      phoneColEl.scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "start" });
      textInput.focus({ preventScroll: true });
    });
    try {
      phoneTimeEl.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch (e) { /* keep the static fallback */ }
    if (document.querySelectorAll(".demobanner").length && syntheticLineEl) syntheticLineEl.hidden = false;
    loadCoverage();
    refreshTrace();
    refreshKedd(false);
    if (keddRetryEl) keddRetryEl.addEventListener("click", () => refreshKedd(true));
  }
  document.addEventListener("DOMContentLoaded", landingInit);
})();
