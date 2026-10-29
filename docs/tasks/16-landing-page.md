# Task 16 — Landing page: explain Çırak and run it live on the same page

Branch: `muse/16-landing-page`. Allowed to edit: `app/static/**` (new files fine), `app/channels/web.py` (template
placeholders + one read-only `/api/trace` endpoint), `tests/test_web.py`. Start after task 15 is merged (`git pull`).
No new Python dependencies, no JS framework, no build step, no external scripts or CDNs (self-host everything;
a Google Fonts `<link>` is the only allowed external request).

## Why

The public demo is what judges of a *global* Meta hackathon open first. Today it is a bare WhatsApp lookalike: it does
not say what Çırak is, why it is safe, or which Meta models do what. The page must explain and prove it in one screen.

## Layout

Desktop (≥ 1024 px): two columns. Left = scrolling story (sections below). Right = a **sticky phone** with the live
chat (the existing chat, restyled), always visible while reading. Mobile: hero → phone → story sections.

Landing copy is **English** with a `TR / EN` toggle (strings in one `i18n.js` object; default from
`navigator.language`, remembered in `localStorage` inside try/catch). The chat itself stays Turkish (it is the
product); every suggestion chip shows the Turkish text plus a one-line English gloss under it.

### Sections (left column)

1. **Hero.** "Çırak — the apprentice that runs your shop from WhatsApp." Sub-line: "Send a voice note or a photo.
   Çırak plans it, you approve with one word, it gets done in KEDD." Two buttons: "Try it live →" (focuses the
   phone input) and "How it stays safe" (anchor). Small line: "Built for the Meta Global AI Developer Hackathon 2026".
2. **How it works** — 4 steps in a horizontal stepper (vertical on mobile), each with the Meta model badge:
   ① Voice note → *Muse Voice Transcribe* ② Product photos → *SAM 3.1* cut-outs + *Muse Spark* vision
   ③ Plan + approval card → *Muse Spark* (tool calls on live business data) ④ "evet" → deterministic code executes,
   *Muse Image* designs the campaign visual.
3. **The model proposes, code executes.** Six short guarantees as a grid: nothing runs without "evet"; the card is
   rendered from verified data, not from the model's words; every ID must come from a real lookup; business limits
   are enforced twice (agent + KEDD server); every write is idempotent and audited; no customer phone numbers ever
   reach the model or the UI.
4. **What Çırak can do in KEDD** — a grid rendered from `app/static/coverage.json` (so it can be updated without
   touching code): `[{area, what_en, what_tr, status: "live"|"soon"}]`. Status pill: green "Live", grey "Coming".
   Seed it with: catalog & prices, bulk price change, discounts (scheduled), appointments (book / move / cancel),
   daily summary & morning brief, customer segments — **live**; WhatsApp template campaigns, loyalty points,
   ads performance & actions, Instagram/Facebook posts — **soon**.
5. **Behind the scenes** (also shown as a collapsible drawer under the phone): after each answer, a timeline of what
   happened for *that* message — "🎙 transcribed (1.2 s) → 🔎 catalog.list → 👥 segment: 23 customers → 🗂 card".
   Source: new `GET /api/trace` → `{steps: [{tool, ms?}], models: [...]}` built from the session's latest
   `tool_log.json` in its run dir. **Only tool names and counts**: never args, results, customer names or text.
   404 when there is no run yet. Same session cookie, same rate-limit-free class as `/` (it calls no model).
6. **Footer:** GitHub repo link, "Synthetic demo data · nothing is sent to real customers" (public mode), license.

## Look

- Brand: KEDD indigo → violet gradient (`#4F46E5` → `#7C3AED`) for hero and accents; neutral surfaces; chat bubbles
  keep a WhatsApp-like green for the owner and white/grey for Çırak so it still *reads* as WhatsApp.
- Phone: realistic frame (rounded 40 px, notch, status bar with time), soft shadow, 390×780 on desktop.
- Approval cards are the star: render the existing card text as a styled card (title, bullet rows, a highlighted
  "evet / hayır" footer with two buttons that send those words). Image replies keep the lightbox.
- Type: Inter (Google Fonts) with system fallback; 16 px base; generous whitespace; one accent colour per section.
- Light and dark (`prefers-color-scheme`), colours as CSS variables on `:root`.
- Motion: subtle (fade/slide 150–250 ms), honour `prefers-reduced-motion`. A typing indicator while busy.
- Accessibility: real buttons, focus rings, `aria-live` on chat, contrast ≥ 4.5:1, no horizontal scroll at 360 px.

## Must not change

Everything task 13 guarantees (banner in public mode, rate limits, upload checks, `/kedd` refusal, origin guard,
no content logging) — the existing tests must stay green untouched. Busy-state behaviour from task 12 stays.

## Acceptance

`python -m pytest -q` green, plus tests for: `/api/trace` returns only `tool` names/ms (assert no `args`, `result`,
digits-only phone-like strings), 404 before any run, other session cannot read your trace; `coverage.json` is valid
and every item has the four fields; index contains the language toggle and the coverage container.
PR: attach desktop + 375 px screenshots (light and dark).
