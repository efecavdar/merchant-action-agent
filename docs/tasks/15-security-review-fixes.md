# Task 15 — Security review fixes (public repo)

Branch: `muse/15-security-fixes`, from `main` **after task 13 is merged**. Allowed to edit: `app/approval.py`,
`app/actions.py`, `app/agent.py` (logging lines only), `app/executor.py`, `app/pipeline.py`, `app/meta_client.py`,
`app/channels/whatsapp.py` (logging + download size only), `app/server.py` (dispatch-failure handling only),
`app/channels/web.py`, `app/static/app.js`, `app/tools/backend.py` (`KeddBackend` signing only), `modal_app.py`
(secret scoping only), `docs/architecture.md`, tests. For every item: first a failing test, then the fix.
Already fixed on main (do not redo): approval only after delivery, timestamp check, narrow approval words,
checkpoint before execution, secret not pickled.

## Approval card shows everything that will happen (M2, M3)
1. `add_catalog_item`: show category and the description (≤ 120 chars) that will be written — including the vision
   fallback the executor would use — on the card line.
2. `create_discount`: list the item names (first 5 + "+N kalem"), resolved from catalog facts / new items in the plan.
3. `send_campaign` with `attach_visual`: add "📎 görsel eklenecek"; `generate_visual`: show the first 80 chars of the
   prompt.
4. Every third-party string placed on the card (customer/service/campaign/item names, template variables) is
   sanitised: control chars and newlines collapsed to one space, `*` `_` `~` stripped, truncated to 40 chars.
5. Card order: action lines first, then "Çırak'ın notu:" + `summary_tr` (so prose cannot frame the actions).

## Validator gaps (L2)
6. Free-text `message`, template variables, catalog name/description: reject URLs, e-mail addresses, phone numbers
   (TR and international patterns) and IBANs. Name ≤ 60, description ≤ 500, category ≤ 40, ≤ 12 actions per plan.
7. Promised-offer words in messages/variables ("bedava", "ücretsiz", "yarı fiyat", "1 alana 1", "hediye") require a
   discount action in the plan.
8. Template requirement follows the **session's** backend (`isinstance(backend, KeddBackend)`), not `settings.backend`
   — pass a flag into `validate_proposal`.
9. `create_discount`: `starts` ≥ today; de-duplicate `item_refs`. Appointments: `starts` in the future.
10. `generate_visual` with catalog ids only (no photo crops) is rejected until the executor supports it.

## KEDD signing — lockstep with gateway K1.2 (do NOT deploy before the gateway)
11. Signed string becomes `"<ts>.<METHOD>.<target>.<X-Agent-Phone>.<Idempotency-Key or empty>.<rawBody>"`;
    signature lowercase hex. Update `docs/kedd-gateway-contract.md` and the signing tests.

## Logs, secrets, media (L3–L5, L8–L10)
12. No user content in logs: drop model prose, rejected proposals (log only error codes + action types), notify text,
    vision output, Graph error bodies (log status + error code only).
13. `modal_app.py`: `web` gets only `META_APP_SECRET`, `WA_VERIFY_TOKEN`, `WA_PHONE_NUMBER_ID`, `WA_ALLOWED_SENDERS`
    via `Secret.from_dict` built locally (guard with `modal.is_local()` so containers never read `.env`);
    `worker`/`morning_brief` keep the full secret.
14. ffmpeg/ffprobe: `timeout=60`, `-protocol_whitelist file,pipe`, `-t 181`, input magic-byte allow-list (ogg, webm,
    m4a/mp4, mp3, wav); always check the converted WAV's real duration.
15. `download_media`: refuse > 16 MB (stream with a byte counter).
16. Webhook: if `dispatch` fails, un-mark the message id so Meta's redelivery is processed; non-object JSON → 400.
17. Web UI queue: a queued message that is an approval word is NOT auto-sent after a card arrives — show it back in the
    input so the owner sends it consciously.
18. Replace the fixture phone `+905321234567` with an obviously fake number; fix "7 action types" in architecture.md.

## Acceptance
`python -m pytest -q` green; PR lists each item number with its test name.
