# Task 04 — Architecture & security document for the judges

Branch: `muse/04-architecture-doc`. Allowed to create/edit: `docs/architecture.md`, `docs/img/**` (SVG only),
`README.md` (one link line under "Design choices"). No code changes.

## Goal

A single English document a hackathon judge can read in 5 minutes and come away knowing (a) how a voice note
becomes executed business actions, (b) why the agent cannot do anything the owner did not approve, and (c)
which Meta model does what. Everything must be derived from the code as it is — read it, do not guess.

## Content

1. **Flow diagram** (Mermaid, in the markdown): channels (WhatsApp Cloud API webhook on Modal, web UI) →
   `core.handle` → `pipeline` (Voice Transcribe, SAM 3.1, Spark vision) → `agent.plan` (Spark tool loop) →
   `actions.validate_proposal` → approval card → `executor.execute` → backend (Mock / KEDD adapter) → report.
   Mark clearly which boxes are model calls and which are plain code.
2. **"The model proposes, code executes"** — explain with the actual tool list from `app/agent.py` (read-only
   tools + `propose_actions` + `reply_only`) and the actual action list from `app/actions.py`. Show one real
   proposal JSON (take it from `tests/test_offline.py::_proposal`) and the approval card it renders to.
3. **Validation rules** — a table of every check in `validate_proposal`, one line each, with the failure it
   prevents. Include the two consistency rules (numbers promised in the message; summary that promises a
   campaign) and the "ids must come from a tool call in the same run" rule for segments / customers /
   appointments.
4. **Channel security** (WhatsApp path), from `app/channels/whatsapp.py`, `app/server.py`, `app/worker.py`,
   `modal_app.py`: signature check, phone_number_id check, sender allow-list, de-duplication, single worker,
   approval bound to the session and consumed once, fail-closed executor, stale-session recovery.
5. **Data handling** — customer phone numbers never leave the backend; consent gate; Standard-mode model ids
   (not used for training); what the mock simulates and labels as simulated.
6. **Meta models used** — table: model id, endpoint, where in the code, what for. Match `README.md`.
7. **Known limitations** — honest list (e.g. 24-hour access token in the dev app, mock backend for judges,
   KEDD adapter not exercised in the public repo, SAM/ASR exercised only via the smoke script).

Keep it under ~1,500 words plus the diagram and tables. Plain prose, no marketing language.

## Acceptance

- Every file path, function and rule named in the doc exists in the repo at that path (check with grep).
- Mermaid renders on GitHub (no unsupported syntax).
- `python -m pytest -q` still green (no code touched).
