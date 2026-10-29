# Merchant Action Agent

> **Try it live:** <paste the `demo` URL printed by `modal deploy` here> — runs on synthetic demo data, nothing is sent to real customers.

A multimodal, human-in-the-loop AI agent for small business automation.

**Meta Global AI Developer Hackathon 2026 — Startup track**

> Pre-existing: KEDD, our CRM + WhatsApp messaging + marketing platform for Turkish SMEs (closed source, not in this repo).
> Built during the hackathon (Oct 27 – Nov 4, 2026): everything in this repository — the agent layer that turns a
> voice note and product photos into approved, executed business actions.

## The problem

Our users (boutiques, salons, cafés) never open a dashboard. They live in WhatsApp and they talk, they don't type.
Every feature that needs a panel gets ignored. So the interface here is a conversation, and the agent does the clicking.

## What it does

A merchant sends product photos and a voice note:

> *"Bunları vitrine ekle, tanesi 850 lira, hafta sonu yüzde 15 indirim yap, uzun süredir gelmeyenlere haber ver."*
> ("Add these to the catalog at 850 TRY each, 15% off this weekend, tell customers who haven't been around.")

Or a salon owner, with no photo at all:

> *"Yarın öğleden sonra boşluklarım var, saç boyamada yüzde 20 indirim yap, sadık müşterilere haber ver.
> Bir de keratin bakımı ekle, 90 dakika, 2500 lira."*

The agent:

1. **Transcribes** the voice note — Muse Voice Transcribe (Turkish is one of the 25 validated languages)
2. **Segments** each product out of the photo — SAM 3.1 (boxes → crops)
3. **Describes** each crop into a Turkish catalog card — Muse Spark (vision)
4. **Plans** with tools: reads the business profile, catalog (products *and* services), schedule, builds a CRM
   segment, drafts the message — Muse Spark (tool calling)
5. **Validates** the plan in code (prices, discount limits, dates, segment ids, message length, and that the
   message only promises what the plan actually does)
6. **Asks for approval** with a WhatsApp-style card. Nothing runs before a human says *evet*.
7. **Executes** deterministically: catalog → discount → campaign visual (Muse Image, composed from the real crops) → WhatsApp campaign
8. **Reports** back in the merchant's language.

```
voice note ─► Muse Voice Transcribe ─┐
                                     ├─► Muse Spark (plan + tools) ─► validate ─► 👤 approve ─► execute ─► report
photos ─► SAM 3.1 ─► crops ─► Muse Spark (vision) ┘                                          └─► Muse Image (campaign visual)
```

## What the owner can ask

| Owner says (voice or text) | What happens |
|---|---|
| *"Bunları ekle, tanesi 850, hafta sonu %15, uzun süredir gelmeyenlere haber ver"* + photos | SAM crops → catalog cards → discount → campaign visual → WhatsApp campaign (after approval) |
| *"Keratin bakımı ekle, 90 dakika, 2500 lira"* | a service is added from words alone (after approval) |
| *"Yarın boşluklarım var, saç boyamada %20 indirim, sadık müşterilere yaz"* | schedule is read, discount + campaign proposed (after approval) |
| *"Ayşe'nin yarınki randevusunu bir saat sonraya al"* | customer and appointment resolved, slot checked; if it clashes the agent **asks** instead of forcing |
| *"Bugün ne oldu?"* | read-only: appointments, revenue, new customers, unanswered messages, free slots — no approval needed |
| ☀️ Sabah özeti (every morning 09:00, Çırak writes first) | today's appointments, free hours, waiting messages, yesterday's revenue + one concrete suggestion (still needs *evet*) |
| *"Tüm çantalara %10 zam yap"* | bulk price change with a before/after preview on the card (after approval) |
| *"Ayşe Y.'ye 200 puan ekle, doğum günü"* | loyalty points adjusted (after approval) |
| *"Reklamlar nasıl gidiyor?"* / *"En kötü reklamı durdur"* | ads performance + suggestions (answer only); pause and budget changes need approval |

## Design choices

- **The model proposes, code executes.** The LLM only has read-only tools plus `propose_actions`. Writes
  (`add_product`, `create_discount`, `send_campaign`) are executed by `executor.py` after approval, in a fixed order,
  with temp ids resolved to real ids. The model never sends a message by itself.
- **Validation loop.** Invalid proposals go back to the model as structured errors (max 3 revisions).
  Besides types and limits, two consistency rules caught real failures in live runs: every `%X` / `X TL`
  in the customer message must exist in the plan, and a summary that promises a campaign must come with a
  `send_campaign` action.
- **Customer data stays in the backend.** Segments come back as counts and samples; the model never sees a
  phone number, and only customers with marketing consent are counted.
- **Template campaigns.** Business-initiated WhatsApp messages use approved templates (the gateway
  fills `{{1}}` with each customer's name) while the model only supplies short variables like code and offer.
- **Fail closed.** If one step fails, later steps (like sending messages) do not run.
- **Standard mode only.** Model ids without `-contributor`: content is not used to train Meta models.

Architecture & security deep-dive: [docs/architecture.md](docs/architecture.md).

## Run it (mock backend, no KEDD needed)

```bash
git clone https://github.com/efecavdar/merchant-action-agent && cd merchant-action-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # put your MODEL_API_KEY in it
brew install ffmpeg             # or apt install ffmpeg — needed for voice notes

python scripts/check_api.py                                   # smoke test
python -m app.cli run --text "Yeni ipek fular koleksiyonu ekle, tanesi 850 lira, hafta sonu %15 indirim, uzun süredir gelmeyenlere haber ver"
python -m app.cli run --text "Bunları ekle, tanesi 850 lira, hafta sonu %15 indirim" --images demo/sample/bags.jpg
python -m app.cli run --audio demo/sample/voice.ogg --images demo/sample/bags.jpg --auto-approve
python -m app.cli run --business salon --text "Yarın öğleden sonra boşluklarım var, saç boyamada %20 indirim, sadık müşterilere haber ver"
python -m pytest -q                                           # offline tests
```

For the browser demo run `python -m app.channels.web` and open http://127.0.0.1:8000: a WhatsApp-style chat with Çırak that uses the same `core.handle` flow as WhatsApp, with photo attach, hold-to-talk voice notes, quick demo prompts, and inline campaign visuals.
Demo video bundle: `python scripts/demo.py` runs all five scenarios into `out/demo/` — see [docs/demo.md](docs/demo.md).

Outputs land in `out/`: `crops/`, `proposal.json`, `tool_log.json`, `approval_card.txt`, `campaign_visual.png`,
`report.json`, `summary.txt`.

The mock backend (`app/tools/backend.py`) is an in-memory store shaped after KEDD's data model (one catalog for
products and services, discounts as sale-price windows, customer stats + consent, appointments) and seeded with
fictional businesses: `--business butik` (default) or `--business salon`. Campaign engagement in mock mode is
simulated and labelled as such. With `BACKEND=kedd` the same interface talks to our platform through a small signed
gateway (adapter only; the platform is not included).

### KEDD integration

In this repo: the `KeddBackend` adapter (`app/tools/backend.py`) and the public contract it speaks,
[docs/kedd-gateway-contract.md](docs/kedd-gateway-contract.md) — signed requests, per-merchant tenant resolution,
idempotent writes. Built during the hackathon inside the closed-source KEDD codebase: the gateway itself (~3k lines),
which implements that contract. In the chat, `/kedd` switches the conversation to the real KEDD demo business
(`/butik` and `/salon` switch back to the mock). Judges can run everything against the mock, which follows the
same contract.

## Repository layout

```
app/
  config.py        settings, model ids, guard-rails
  meta_client.py   Meta Model API wrappers: Spark, Voice Transcribe, SAM 3.1, Muse Image (+ SAM wire parser)
  pipeline.py      voice/photo → MediaContext (transcript + product cards)
  agent.py         Phase 1: tool loop → validated proposal
  actions.py       proposal schema + business-rule validation
  approval.py      human approval card
  executor.py      Phase 2: deterministic execution
  report.py        merchant-facing summary
  cli.py           entry point
  tools/backend.py MockBackend (KEDD-shaped, synthetic data, 2 seeds) + KeddBackend (signed HTTP adapter)
scripts/check_api.py   smoke test for each Meta model
tests/                 offline tests incl. a scripted agent loop
```

## Meta models used

| Model | Endpoint | Role |
|---|---|---|
| `muse-spark-1.3` | Responses API, tool calling | orchestration, product description (vision), summaries |
| `muse-voice-transcribe-1.0` | `POST /v1/asr/transcribe` | voice note → text |
| `sam-3.1` | Responses API (stream) | product segmentation |
| `muse-image-1.0` | `POST /v1/images/edits` | campaign visual from the real product crops |

## Scope and data

Independent hackathon prototype started on October 27, 2026. Uses synthetic demonstration data and a standalone
simulated commerce backend. No proprietary platform source code, confidential business information, or real
customer data is included. Built by Efe Çavdar (Digitally 16).
