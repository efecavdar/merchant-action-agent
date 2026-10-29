# Task 24 — The live demo business is now "KEDD itself"

Branch: `muse/24-kedd-identity`. Start after task 23 is merged. Allowed to edit: `app/agent.py` (system prompt wording
only), `app/static/**`, `app/channels/web.py` (quick prompts / copy only), `docs/demo-script.md`, `README.md`
("Try it live" paragraph only), tests.

## Why

The live KEDD demo business is being re-seeded as a clone of KEDD (a Turkish SaaS for small businesses): its catalog
is KEDD's plans and add-ons, its appointments are demo calls and onboarding sessions, its customers are (synthetic)
small businesses using KEDD. Instagram posts go to KEDD's real account. The product story for judges becomes
"we run our own company with Çırak", while the mock boutique/salon stay as offline fallbacks.

## Change

1. System prompt: the business can be a boutique, a salon **or a software company serving small businesses**; content
   follows the business profile from tools (no salon/boutique assumptions in examples). Customer-facing texts stay
   Turkish (task 23 rule).
2. Web demo quick prompts for the live business (TR + EN pairs, task 23):
   - "KEDD Büyüme'ye bu hafta sonu %20 indirim yap, bir süredir gelmeyen müşterilere haber ver"
   - "Yarın 14:00'e Nar Kuaför ile demo görüşmesi ayarla"
   - "Yeni WhatsApp asistanımız için Instagram gönderisi hazırla" (on the public page this ends as a preview; say so in
     the chip's English gloss: "preview only on this page")
   - "Ada Kafe'ye 100 tavsiye puanı ekle"
   - ☀️ Sabah özeti
   Mock fallback keeps the existing boutique/salon chips.
3. `docs/demo-script.md`: rewrite scenes 2–4 for KEDD (photos of KEDD materials/brochure or a product shot of the
   QRise table stand; plan discount; demo-call booking; Instagram post on KEDD's real account), keep scene 1 and 5.
4. README "Try it live": one sentence that the live business is a KEDD clone with synthetic customers.

## Acceptance

`python -m pytest -q` green; tests that the live-mode chips are the KEDD set and the mock chips are unchanged, and that
the system prompt no longer hard-codes boutique/salon examples.
