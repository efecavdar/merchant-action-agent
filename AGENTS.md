# Working in this repository (for coding agents)

Hackathon project: a human-in-the-loop WhatsApp agent for small businesses. Read `README.md` first.

## Ground rules

1. **Work on a branch, never on `main`.** Branch name: `muse/<task-id>-<slug>` (e.g. `muse/01-web-ui`).
   One task per branch, one PR per task. Tasks live in `docs/tasks/`.
2. **Do not edit these files.** They are the trust boundary of the agent and are reviewed by a human:
   `app/actions.py`, `app/executor.py`, `app/agent.py`, `app/core.py`, `app/channels/whatsapp.py`,
   `app/server.py`, `app/worker.py`, `modal_app.py`, `app/config.py`, `.env*`, `AGENTS.md`.
   If a task seems to need a change there, stop and write the proposed change in the PR description instead.
   **Exception:** a task brief may list one of these files under "Allowed to edit" — then you may change it,
   only as far as the brief says, and you must keep every existing test green.
3. **Never read, print or commit secrets.** `.env` is git-ignored; keep it that way. No API keys in code or docs.
4. **The model proposes, code executes.** Do not add any code path where a model output triggers a write
   (catalog, discount, message, appointment) without going through `app/core.py` → approval → `app/executor.py`.
5. **No customer phone numbers in tool outputs, logs or UI.** Segments are counts + samples.
6. **Tests must pass:** `python -m pytest -q` (offline, no API key needed). Add tests for what you build.
7. **Style:** Python 3.12, type hints, ~110-char lines (`setup.cfg`), comments only where the *why* is not obvious.
   Match the existing code; no new dependencies without a one-line justification in the PR.
8. **`MockBackend.SCHEMA`:** if you add, remove or rename an attribute of `MockBackend`, bump `SCHEMA` by one.
   Live sessions are pickled; an old shape meeting new code crashed an approved plan once.
9. **Turkish-facing text** (merchant messages, UI labels) is informal "sen", short, one emoji max.
10. **Finish with a PR description:** what changed, how to test it manually, anything you were unsure about.

## Layout

```
app/core.py            conversation core (Session, handle)  ← channels call this
app/channels/          WhatsApp (done), web UI (task 01)
app/tools/backend.py   MockBackend (KEDD-shaped synthetic data) + KeddBackend adapter
app/pipeline.py        voice/photo → MediaContext
docs/tasks/            task briefs with acceptance criteria
tests/                 offline tests (fake Responses API, TestClient)
```

Run the mock flow locally without any key: see `tests/test_channels.py::test_core_flow_plan_approve_execute`
for how `core.handle` is driven with a fake planner.
