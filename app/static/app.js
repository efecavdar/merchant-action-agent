(() => {
  const chat = document.getElementById("chat");
  const textInput = document.getElementById("textInput");
  const sendBtn = document.getElementById("sendBtn");
  const attachBtn = document.getElementById("attachBtn");
  const fileInput = document.getElementById("fileInput");
  const micBtn = document.getElementById("micBtn");
  const resetBtn = document.getElementById("resetBtn");
  const preview = document.getElementById("preview");
  const businessEl = document.getElementById("business");
  let picked = [];
  let busy = false;

  function scrollDown() { chat.scrollTop = chat.scrollHeight; }

  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // WhatsApp *bold* + line breaks.
  function markup(s) {
    return esc(s).replace(/\*([^*\n]+?)\*/g, "<strong>$1</strong>").replace(/\n/g, "<br>");
  }

  function addBubble(side, text, imageUrl) {
    const d = document.createElement("div");
    d.className = "bubble " + side;
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
  }

  function addStatus(text) {
    const d = document.createElement("div");
    d.className = "system";
    d.textContent = text;
    chat.appendChild(d);
    scrollDown();
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

  async function send({ text, audioBlob } = {}) {
    if (busy) return;
    text = (text ?? textInput.value).trim();
    if (!text && !audioBlob && picked.length === 0) return;
    busy = true;
    const fd = new FormData();
    if (text) {
      fd.append("text", text);
      addBubble("me", text, null);
    }
    for (const f of picked) fd.append("images[]", f, f.name);
    if (picked.length && !text) addBubble("me", `📷 ${picked.length} fotoğraf`, null);
    if (audioBlob) {
      fd.append("audio", audioBlob, "voice.webm");
      if (!text && !picked.length) addBubble("me", "🎙️ Sesli not", null);
    }
    picked = [];
    renderPreview();
    textInput.value = "";
    try {
      const r = await fetch("/api/message", { method: "POST", body: fd });
      if (!r.ok) throw new Error("http " + r.status);
      const data = await r.json();
      for (const s of data.status || []) addStatus(s);
      for (const rep of data.replies || []) addBubble("them", rep.text || "", rep.image_url || null);
      if (data.business_name) businessEl.textContent = data.business_name;
    } catch (e) {
      addStatus("Bağlantı koptu, tekrar dener misin?");
    } finally {
      busy = false;
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
  async function requestBrief() {
    if (busy) return;
    busy = true;
    try {
      const r = await fetch("/api/brief", { method: "POST" });
      if (!r.ok) throw new Error("http " + r.status);
      const data = await r.json();
      for (const rep of data.replies || []) addBubble("them", rep.text || "", rep.image_url || null);
      if (data.business_name) businessEl.textContent = data.business_name;
    } catch (e) {
      addStatus("Bağlantı koptu, tekrar dener misin?");
    } finally {
      busy = false;
    }
  }
  document.querySelectorAll(".quick button").forEach((b) => {
    b.addEventListener("click", () => {
      if (b.dataset.brief) {
        requestBrief();
        return;
      }
      textInput.value = b.dataset.fill || "";
      textInput.focus();
    });
  });
  resetBtn.addEventListener("click", async () => {
    if (busy) return;
    busy = true;
    try {
      const r = await fetch("/api/reset", { method: "POST" });
      const data = await r.json();
      chat.innerHTML = "";
      for (const rep of data.replies || []) addBubble("them", rep.text || "", rep.image_url || null);
      if (data.business_name) businessEl.textContent = data.business_name;
    } finally {
      busy = false;
    }
  });

  // Press-and-hold voice note.
  let recorder = null, chunks = [];
  async function startRec(e) {
    e.preventDefault();
    if (busy || recorder) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
      recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      chunks = [];
      recorder.ondataavailable = (ev) => { if (ev.data.size) chunks.push(ev.data); };
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunks, { type: "audio/webm" });
        const rec = recorder;
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
