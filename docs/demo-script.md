# Çırak — 3-minute demo video script (for judges)

Format: split screen, whole video. **Left: WhatsApp chat with Çırak** (the merchant's view).
**Right: the KEDD side** — the live demo page's *Live in KEDD* panel for scenes 1–4, the full
demo page for scene 5. Total runtime ≈ 3:00 (5 × ~30 s scenes + 0:20 closing).
Record the chat in Turkish; burn in the English subtitles below.

Setup before recording: open the demo page at
`https://efe-cavdar--cirak-demo.us-east.modal.run`, start a fresh chat
(↺ reset), keep the *Live in KEDD* panel visible on the right. Have 2 photos
(a KEDD brochure, a QRise table stand on a café counter) and the plan-discount
prompt ready to paste. Nothing here sends real messages: the demo runs on the
KEDD demo business with customer-facing writes held as previews.

## Scene 1 — Morning brief (0:00–0:30)

- **Send (tap the ☀️ chip, or type):** `Sabah özeti`
- **Subtitle:** "Every morning at 09:00, Çırak writes first: the day at a glance."
- **Must be visible:** the brief bubble — today's appointments, free hours, waiting
  messages, yesterday's revenue, one concrete suggestion.
- **Expected card:** none — the brief is read-only, no approval needed.
- **Voiceover:** "Çırak starts the day for the merchant, before she even asks."
- **Fallback if slow:** the brief is one model call; if it stalls, cut to the already
  loaded *Live in KEDD* panel and narrate over it, then cut the brief in when it lands.

## Scene 2 — Photos + voice note → plan push (0:30–1:05)

- **Send:** attach 2 photos (KEDD brochure, QRise table stand), then as a voice note
  (or paste as text):
  `QRise Menü Eklentisi'ni kataloğa ekle, Büyüme planına bu hafta sonu %20 indirim yap, bir süredir gelmeyen müşterilere haber ver`
- **Subtitle:** "Add the QRise Menu add-on to the catalog, 20% off the Büyüme plan this weekend, tell customers who haven't visited in a while."
- **Must be visible:** left — the "⏳ Bakıyorum…" status, then the approval card
  (new catalog item → weekend plan discount → win-back campaign with
  recipient count); right — the panel's catalog sample still showing the old range.
- **Expected card:** `*Çırak — onayına sunuyorum*` with the 🛍️ add-on, 🏷️ %20 indirimi,
  📣 WhatsApp audience line. Reply **`evet`**.
- **After evet:** the campaign visual appears (designed from the real product crops),
  then the "Hallettim" summary; the panel's *Recent writes* row updates.
- **Voiceover:** "He talks; Çırak sees the materials, plans the whole push, and waits for one word."
- **Fallback if slow:** SAM + vision + planning takes ~30–60 s — record it once, then
  jump-cut from send to card ("a minute later…"). Never fast-forward the card itself.

## Scene 3 — Demo-call booking + offer (1:05–1:35)

- **Send (type or paste, no photo):**
  `Yarın 14:00'e Nar Kuaför ile demo görüşmesi ayarla. Büyüme planına bu hafta sonu %20 indirim yap, bir süredir gelmeyen müşterilere haber ver.`
- **Subtitle:** "Book a demo call with Nar Kuaför for tomorrow 14:00. 20% off the Büyüme plan this weekend, tell customers who haven't visited in a while."
- **Must be visible:** left — the card showing the resolved customer (Nar Kuaför —
  Elif Kaya), the 30-minute Demo Görüşmesi at 14:00, the %20 plan discount and the
  campaign; right — the panel's *Today's appointments* for the KEDD team calendar.
- **Expected card:** 📅 appointment + 🏷️ indirim + 📣 campaign lines. Reply **`evet`**.
- **Voiceover:** "No photos needed — Çırak reads the calendar and fills the empty slots."
- **Fallback if slow:** this plan is text-only and usually fast; if the card lags,
  narrate the appointments panel on the right while waiting.

## Scene 4 — Instagram post on KEDD's account, designed first (1:35–2:05)

- **Send:** `Yeni WhatsApp asistanımız için Instagram gönderisi hazırla`
- **Subtitle:** "Draft an Instagram post for our new WhatsApp assistant."
- **Must be visible:** left — the approval card **with the designed visual already on
  it** (the owner sees exactly what would go out), caption quoted verbatim.
- **Expected card:** Instagram gönderisi + caption + preview image. Reply **`evet`** →
  the outcome confirms it scheduled for KEDD's real account (on this demo page the
  publish itself is held as a preview, never sent).
- **Voiceover:** "The visual is designed before approval — what you see is what goes out."
- **Fallback if slow:** image generation is the slowest step (~1 min). Pre-generate by
  sending the message once before recording, reset, and record the replay; or cut from
  the caption card to the finished visual.

## Scene 5 — The public demo page (2:05–2:40)

- **Send:** nothing — scroll the demo page on both halves (zoom the phone out).
- **Subtitle:** "Everything you just saw runs here, live, on the KEDD demo business."
- **Must be visible:** the *Live in KEDD* panel — green `live` pill, business name,
  catalog sample with prices, today's appointments (no customer names, ever),
  last-24h writes with counts; then scroll to *The model proposes, code executes*
  and linger on the six guarantees.
- **Expected card:** none — this scene is the proof, not the product.
- **Voiceover:** "Live business data on the right, the safety contract underneath — six guarantees, all enforced in code."
- **Fallback if slow:** the panel caches for 10 s; if KEDD is down it flips to the
  amber `fallback: mock salon` pill with a retry button — narrate that as the
  graceful-degradation story, it is part of the demo, not a failure.

## Closing (2:40–3:00)

On screen: the phone showing the final "Hallettim" summary next to the live panel.

> "Çırak: small merchants live in WhatsApp, so Çırak works where they live. The model
> proposes, code executes, and the owner approves every step with a single word — evet."
