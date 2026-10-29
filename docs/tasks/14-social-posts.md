# Task 14 — Social media posts designed by Çırak, published after "evet"

Branch: `muse/14-social-posts`. Allowed to edit: `app/actions.py`, `app/agent.py`, `app/executor.py`,
`app/approval.py`, `app/core.py` (only to attach the preview image to the card reply), `app/tools/backend.py`,
`app/meta_client.py` (only if a helper is needed), tests, `README.md` (one table row).

## Goal

> "Yeni fular koleksiyonu için Instagram'a bir gönderi hazırla, yarın sabah 10'da paylaş."

Çırak designs the post — a visual with **Muse Image** (from the real product crops when photos were sent, otherwise
from catalog items) and a Turkish caption with hashtags — shows the **actual image and caption** on the approval card,
and only after `evet` publishes it (or schedules it) through the backend. Instagram/Facebook are Meta platforms: this
shows Meta's models producing content that goes out on Meta's channels, with a human in the loop.

## Change

1. Planning stays text-only. New action `publish_social_post` {`caption` (≤ 2200, ≤ 15 hashtags, no URLs, no phone
   numbers, no prices/percents that are not in this plan — reuse the message number rule), `platforms` ⊆
   {instagram, facebook}, `publish_at?` ISO datetime ≥ now+5 min and ≤ 30 days, `visual_prompt`, `item_refs?`}.
2. The visual must exist **before** the owner approves (they approve what they see). After a valid proposal that
   contains `publish_social_post`, `core.handle` generates the visual (same `generate_campaign_visual`, real crops for
   `item_refs` that are photo temp_ids) into the run dir, stores its path in the pending plan, and the card reply
   carries that image (`Reply.image_path`) with the caption shown verbatim under "📱 Instagram gönderisi".
   If generation fails, the card says so and the action is dropped from the plan (owner can retry).
3. Executor: reads the stored image, calls `backend.publish_social_post(caption, image_path, platforms, publish_at)`.
   Mock: stores it, returns `{campaign_id, status: "scheduled"|"published", scheduled_for}` and labels it simulated.
   Kedd: POST `/agent/social/post` with base64 image (jpg, ≤ 5 MB — re-encode/resize if needed), stable idempotency key.
4. Report says "Yarın 10:00'da Instagram'da yayında olacak" / "Instagram'da yayınlandı", from the backend result only.

## Acceptance

`python -m pytest -q` green with tests for: validation rules, visual generated before the card (monkeypatched image
call) and attached to the card reply, generation failure path, executor uses the stored image (not a new one),
Kedd body shape + idempotency key, mock scheduling.

**Public demo (task 17):** when `settings.public_demo` is on, never call `publish_social_post` on any backend;
show the visual + caption as a preview. Task 17 wires this; build the action so a `dry_run` flag is easy to pass.
