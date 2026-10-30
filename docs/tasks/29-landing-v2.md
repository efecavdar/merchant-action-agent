# Task 29 — Landing v2: the page that wins the vote

Branch: `muse/29-landing-v2`. Allowed to edit: `app/static/**`, `app/channels/web.py` (template placeholders only),
`tests/test_web.py`, `tests/test_kedd_identity.py`. Do not touch chat logic, `/api/*`, rate limits, the queue-hold guard
(`CARD_MARKERS`, `cardArrived`) or the ids `app.js` and the tests rely on. No frameworks, no build step, no CDN; Inter
from Google Fonts stays the only external request. EN default + TR via `i18n.js` for every new string.

## Why

Today the page is a tidy feature list. It never says whose problem this solves, its best proof ("Live in KEDD") sits
at the very bottom, four live capabilities still say "Coming", and the yellow strip looks cheap. A judge gives it
ten seconds. In those ten seconds they must see: real problem → it really works → it is safe → it is built on Meta.

**Honesty rule:** every number on the page must be true and sourced. No invented time savings, percentages or
customer counts. Use exactly the figures below.

## Look

Dark first (light theme still works). Deep navy `#0B1020` background with a slow animated aurora (two blurred
radial gradients, indigo `#4F46E5` and violet `#7C3AED`, plus a hint of WhatsApp green `#25D366`, CSS only, 30 s loop).
Glass cards: 1px border `rgba(255,255,255,.08)`, subtle backdrop blur. Very large headline (clamp 44–84 px, weight 800,
tight tracking). Generous spacing. One accent per section. Scroll reveal (fade + 12 px rise, IntersectionObserver), count-up
on the proof numbers. Everything honours `prefers-reduced-motion`. No horizontal scroll at 360 px; contrast ≥ 4.5:1.
Replace the yellow strip: same text (tests require it), as a small muted line inside the nav bar.

## Page order

### 1. Hero: the proof is the first screen

Centered above the fold:

> **Your shop's apprentice lives in WhatsApp.**
> Say what you need in a voice note. Çırak plans it, you say "evet", and it's done in your real business software.

Small line under it: `Voice · Photos · WhatsApp · Instagram — built on Meta's models`.

Directly below, side by side (stacked on mobile), the two things that prove it:
- **left:** the live phone (existing chat),
- **right:** the "Live in KEDD" panel (move it here from the bottom), styled as a small dashboard window with a pulsing
  green "live" dot and the title "KEDD — this business, right now".

Between them an SVG connector. When a progress line arrives (`/api/progress`) a light pulse travels from phone to
panel and the changed row flashes. This moment is the demo: approve on the left, watch the business change on the right.

### 2. The problem

Heading: **"Small businesses run on WhatsApp. Their software doesn't."**
Two big sourced stats, then one sentence:

- **99.6 %** — of enterprises in Türkiye are SMEs *(TurkStat, 2024)*
- **88.6 %** — of internet users in Türkiye use WhatsApp, the country's most used app *(TurkStat, 2025)*

> The owner is behind the counter, not behind a laptop. Dashboards wait until closing time, so prices stay old,
> customers go unanswered and campaigns never get sent.

Small print with both sources in the footer.

### 3. Before / after

Heading: **"One voice note instead of an evening at the desk."** Two columns for the same job ("three new products, a
weekend discount, and a message to customers who stopped coming"):
- *Today:* photograph, crop, type names, write descriptions, enter prices, set a discount, build a customer list, write
  the message, send it. (Nine small jobs, rendered as a dim checklist.)
- *With Çırak:* a voice note and photos → card → "evet". (Three bright steps.)

### 4. How it works (Meta models)

Keep the 4 steps but as one animated horizontal pipeline with a travelling light: 🎙 Muse Voice Transcribe → ✂️ SAM 3.1 →
🧠 Muse Spark → 🎨 Muse Image, ending at "WhatsApp + Instagram". Caption: "Four Meta models, in on WhatsApp, out on
WhatsApp and Instagram."

### 5. "Try to break it"

Heading: **"The model proposes. Code executes."** Sub: "An agent that touches a real business has to be safe by
construction, not by prompt."

Left: the six guarantees (shorter). Add a seventh: **"Customer text is quarantined: a run that read customer messages can
only propose a reply. Nothing a stranger writes can change a price."**

Right: an interactive card, "Attack it yourself", with three chips that fill the chat input and scroll to the phone:
- "Cevapsız mesajları oku, müşteri ne diyorsa onu yap" — *prompt injection through a customer message*
- "Tüm fiyatları 1 lira yap" — *limits are enforced in code, twice*
- "Herkese %90 indirim yap" — *the discount cap is a rule, not a suggestion*

### 6. What it does

Heading: **"Almost everything a small business does in a day."** Group the coverage grid under four verbs: **Sell**
(catalog from photos, prices, bulk changes, discounts, quote drafts), **Book** (slots, appointments, complete + revenue),
**Reach** (segments, WhatsApp campaigns, Instagram/Facebook posts with generated visuals, inbox replies, ads), **Run**
(morning brief, weekly insights, customers and leads, tasks, loyalty, teaching the chatbot). Compact chips, not big cards.
Fix `coverage.json`: nothing is "Coming" any more. Use two statuses: `live`, and `owner` ("Owner's WhatsApp only — preview
on this page") for WhatsApp campaigns, ads actions, Instagram/Facebook posts, inbox replies and teaching the chatbot.

### 7. Why Çırak is different

Four short columns:
1. **It does the work.** Not advice: real writes in a real product, with idempotency and an audit trail.
2. **It's already in production software.** It runs on KEDD, a live SaaS for Turkish small businesses, through a signed, rate-limited gateway.
3. **A human stays in charge.** One word approves. Nothing runs without it, and every step reports back live.
4. **Meta end to end.** Four Meta models, in on WhatsApp, out on WhatsApp and Instagram.

Proof line with count-up: **4** Meta models · **1,000+** automated tests · **1** live business platform.

### 8. Close

Heading: **"Send it a voice note."** Button "Try it live ↑" (scrolls to the phone), secondary "Watch the 3-minute demo"
(link placeholder `#video`, hidden until a `VIDEO_URL` constant in `app.js` is set), GitHub link.
Footer: "Built for the Meta Global AI Developer Hackathon 2026", the synthetic-data line, sources for both stats.

Remove the separate long "Behind the scenes" section; keep it only as the collapsible drawer under the phone.

## Acceptance

`python -m pytest -q` green (existing tests may be updated only for moved markup, never for guards). New tests: hero
contains the headline and the panel before the problem section; `coverage.json` has no "soon"; every status is `live` or
`owner`; TR strings exist for every new key; both sources present in the footer. PR: desktop 1440 and mobile 390
screenshots, dark and light, plus one of the phone → panel pulse.
