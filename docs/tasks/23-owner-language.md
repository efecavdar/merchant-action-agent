# Task 23 — Answer the owner in the language they write in (Turkish or English)

Branch: `muse/23-owner-language`. Allowed to edit: `app/core.py` (session language + passing it on only),
`app/agent.py` (prompt language line only), `app/approval.py` (labels only), `app/report.py`, `app/brief.py`,
new `app/i18n.py`, `app/static/app.js` (quick-prompt chips only), tests. `MockBackend.SCHEMA` / Session shape: if you
add a Session attribute, give it a default so old pickled sessions load (test it).

## Goal

Judges are international. Today Çırak understands Turkish and English input but always answers in Turkish. Make
every **owner-facing** text follow the owner's language. **Customer-facing** text stays Turkish no matter what:
campaign messages, template variables, Instagram captions, catalog names and descriptions (the customers are Turkish).

## Change

1. `Session.lang` ("tr" default). Set it from each owner text message / transcript with a small deterministic
   detector in `app/i18n.py` (Turkish letters çğıöşü, common Turkish vs English stop-words; ties keep the previous
   value). No model call for detection. Approval words stay as they are (`evet`, `yes`, … already accepted).
2. `app/i18n.py`: one dict `{key: {"tr": ..., "en": ...}}` for every fixed owner-facing string: greeting, 📷 aldım,
   errors, "onayına sunuyorum", card line labels, "Onaylıyor musun? (evet / hayır)" → "Approve? (yes / no)",
   cancellation, delivery-race message, report fallback, morning-brief headings. Card **facts** stay identical (numbers,
   names, dates); only labels change. Customer message quotes on the card stay Turkish and are marked
   "(müşteriye Türkçe gider)" / "(sent to customers in Turkish)".
3. Agent: one line in the system prompt "Reply to the owner in {English|Turkish}. Customer-facing texts (messages,
   captions, catalog names/descriptions) are always Turkish." `report.py` and `brief.py` write in `session.lang`.
4. Web demo: when the page language toggle is EN, the quick-prompt chips send English versions
   ("Add a new silk scarf collection, 850 lira each, 15% off this weekend, tell customers who haven't visited").

## Acceptance

`python -m pytest -q` green, with tests for: detector (Turkish, English, mixed, emoji-only keeps previous); an English
instruction gives an English card with the same facts as the Turkish one; customer message on the card stays Turkish;
`yes` approves in both languages; old pickled Session without `lang` loads as "tr"; report/brief honour the language;
nothing customer-facing changes language (assert on executor inputs).
