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
      const t = document.createElement("div");
      t.innerHTML = markup(text);
      d.appendChild(t);
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
    const kedd = business === "kedd";
    badgeEl.textContent = kedd ? "KEDD" : "demo";
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
      if ((rep.text || "").includes("onayına sunuyorum")) cardArrived = true;
    }
    setBusiness(data.business_name, data.business);
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
})();
