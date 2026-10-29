# Çırak — 3-minute demo video script (for judges)

Format: split screen, whole video. **Left: WhatsApp chat with Çırak** (the merchant's view).
**Right: the KEDD side** — the live demo page's *Live in KEDD* panel for scenes 1–4, the full
demo page for scene 5. Total runtime ≈ 3:00 (5 × ~30 s scenes + 0:20 closing).
Record the chat in Turkish; burn in the English subtitles below.

Setup before recording: open the demo page at
`https://efe-cavdar--cirak-demo.us-east.modal.run`, start a fresh chat
(↺ reset), keep the *Live in KEDD* panel visible on the right. Have 2–3 product
photos (bags/scarves) and the salon prompt ready to paste. Nothing here sends
real messages: the demo runs on the KEDD demo business with customer-facing
writes held as previews.

## Scene 1 — Morning brief (0:00–0:30)

- **Send (tap the ☀️ chip, or type):** `Sabah özeti`
- **Subtitle:** "Every morning at 09:00, Çırak writes first: the day at a glance."
- **Must be visible:** the brief bubble — today's appointments, free hours, waiting
  messages, yesterday's revenue, one concrete suggestion.
- **Expected card:** none — the brief is read-only, no approval needed.
- **Voiceover:** "Çırak starts the day for the merchant, before she even asks."
- **Fallback if slow:** the brief is one model call; if it stalls, cut to the already
  loaded *Live in KEDD* panel and narrate over it, then cut the brief in when it lands.

## Scene 2 — Photos + voice note → shop action (0:30–1:05)

- **Send:** attach 2 product photos, then as a voice note (or paste as text):
  `Yeni ipek fular koleksiyonu ekle, tanesi 850 lira, hafta sonu %15 indirim, uzun süredir gelmeyenlere haber ver`
- **Subtitle:** "Add the new silk scarf collection at 850 lira each, 15% off this weekend, tell customers who haven't visited in a while."
- **Must be visible:** left — the "⏳ Bakıyorum…" status, then the approval card
  (new catalog items with prices → weekend discount → win-back campaign with
  recipient count); right — the panel's catalog sample still showing the old range.
- **Expected card:** `*Çırak — onayına sunuyorum*` with 🛍️ items, 🏷️ %15 indirimi,
  📣 WhatsApp audience line. Reply **`evet`**.
- **After evet:** the campaign visual appears (designed from the real product crops),
  then the "Hallettim" summary; the panel's *Recent writes* row updates.
- **Voiceover:** "She talks; Çırak sees the products, plans the whole push, and waits for one word."
- **Fallback if slow:** SAM + vision + planning takes ~30–60 s — record it once, then
  jump-cut from send to card ("a minute later…"). Never fast-forward the card itself.

## Scene 3 — Salon slots + offer (1:05–1:35)

- **Send (type or paste, no photo):**
  `Yarın öğleden sonra boşluklarım var, saç boyamada %20 indirim, sadık müşterilere haber ver. Bir de keratin bakımı ekle, 90 dakika, 2500 lira.`
- **Subtitle:** "I have openings tomorrow afternoon — 20% off hair coloring for loyal customers. Also add keratin treatment, 90 minutes, 2500 lira."
- **Must be visible:** left — the card showing the read schedule (free afternoon slots),
  the %20 discount, the loyal-customer campaign, and the new 90-minute service;
  right — the panel's *Today's appointments* for the salon context.
- **Expected card:** 🏷️ indirim + 📣 campaign + 🛍️ keratin service lines. Reply **`evet`**.
- **Voiceover:** "No photos needed — Çırak reads the appointment book and sells the empty chairs."
- **Fallback if slow:** this plan is text-only and usually fast; if the card lags,
  narrate the schedule panel on the right while waiting.

## Scene 4 — Instagram post, designed first (1:35–2:05)

- **Send:** `Fularlar için Instagram gönderisi hazırla`
- **Subtitle:** "Draft an Instagram post for the scarves."
- **Must be visible:** left — the approval card **with the designed visual already on
  it** (the owner sees exactly what would go out), caption quoted verbatim.
- **Expected card:** Instagram gönderisi + caption + preview image. Reply **`evet`** →
  the outcome confirms it scheduled (demo: held as a preview, never published).
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
