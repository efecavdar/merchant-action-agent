# Task 05 — Conversation memory (short follow-ups work)

Branch: `muse/05-conversation-memory`. Allowed to edit: `app/core.py`, `app/pipeline.py`, `app/agent.py` (only the
system prompt text and the user message built in `plan`), `tests/test_channels.py`, `tests/test_agent_loop.py`.

## Problem (seen live)

The agent asked "Ayşe E.'nin randevusunu 17:15'e alayım mı?" via `reply_only`. The owner answered "olur". Today that
second message reaches `agent.plan` with no context, so the agent does not know what "olur" refers to.

## Change

1. `core.Session` gets `history: list[dict]` — items `{"role": "owner"|"cirak", "text": str, "at": float}`.
   - Append the owner message on every `handle` call that reaches planning or approval: the text, the voice
     transcript (`ctx.instruction` when the source is voice) or `"[N fotoğraf]"` for buffered photos.
   - Append every reply text Çırak returns (approval cards, answers, reports), truncated to 600 chars.
   - Keep the last 10 items and drop items older than 2 hours. `/sifirla` and `/butik`/`/salon` clear it.
   - Sessions are pickled: old pickles have no `history`. Add `__setstate__` to `Session` that fills missing fields
     with their defaults, so old sessions load without errors (add a test that unpickles a Session created without
     the attribute).
2. `pipeline.MediaContext` gets `history: list[dict]` (default empty) and `to_prompt_json()` includes it as
   `"recent_conversation"` only when non-empty. `core.handle` passes `session.history` (before appending the current
   message) into the context.
3. System prompt in `app/agent.py`: add one rule — "`recent_conversation` holds the last messages of this chat. Use it
   to resolve short replies such as 'olur', 'evet o saat', 'ikincisi', '17:30 olsun'. The latest owner message wins.
   An approval still always goes through propose_actions and the card; never treat 'olur' as an approval by itself."
4. Do NOT change the approval rule: `evet` approves only a pending card. When nothing is pending, `evet`/`olur` is a
   normal instruction that goes to the planner with history (so the planner can turn the earlier question into a
   proposal → card → the owner approves the card).

## Acceptance

- `python -m pytest -q` green. New tests: history is recorded and trimmed; history reaches `MediaContext` and the
  prompt JSON; `/sifirla` clears it; unpickling an old-shape Session works; `evet` with nothing pending does not
  execute anything.
- Manual: in the web UI (salon) ask "Ayşe E.'nin yarınki randevusunu bir saat sonraya al" → the agent proposes another
  time → answer "olur" → you get an approval card for that time.
