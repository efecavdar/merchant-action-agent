# Task 03 — Demo runner for the video and the judges

Branch: `muse/03-demo-runner`. Allowed to create/edit: `scripts/demo.py`, `docs/demo.md`, `tests/test_demo.py`,
`README.md` (one line under "Run it" pointing to `docs/demo.md`). Do not touch files in `AGENTS.md` rule 2.

## Goal

One command that runs every scenario in the README table against the mock backend and leaves a tidy folder we
can screen-record or screenshot for the submission video. Judges should be able to reproduce it with only a
`MODEL_API_KEY`.

## Behaviour

`python scripts/demo.py [--only butik|salon|summary|appointment|service] [--out out/demo] [--no-images]`

Runs, in order, each with its own subfolder under `--out` (e.g. `out/demo/01-butik/`):

1. `01-butik` — boutique, text instruction from README + `demo/sample/bags.jpg` if it exists (skip images
   otherwise, say so), auto-approve.
2. `02-service` — salon, "Keratin bakımı XL ekle, 90 dakika, 2500 lira", auto-approve.
3. `03-salon-campaign` — salon, "Yarın öğleden sonra boşluklarım var, saç boyamada %20 indirim, sadık müşterilere
   haber ver", auto-approve.
4. `04-appointment` — salon, "Ayşe E.'nin yarınki randevusunu 17:15'e al", auto-approve.
5. `05-summary` — salon, "Bugün ne oldu?" (answer only, nothing to approve).

Use the public functions the CLI uses (`app.pipeline.understand_media`, `app.agent.plan`, `app.approval.render_card`,
`app.executor.execute`, `app.report.*`) — do not shell out to `python -m app.cli`, and do not import private
helpers. Each scenario gets a fresh `MockBackend` (`today` = real today).

Per scenario write: `instruction.txt`, `tool_log.json`, `approval_card.txt` or `answer.txt`, `report.json`,
`summary.txt`, and copy `campaign_visual.png` if produced. Print a one-line result per scenario with elapsed
seconds and tokens (`run.usage`), then a final table. Continue with the next scenario if one fails; exit code 1
at the end if any failed.

`docs/demo.md`: how to run it, what each folder contains, and a suggested 90-second video storyboard that walks
through the five folders (which file to show when). Turkish merchant text stays Turkish; the doc itself is English.

## Acceptance

- `tests/test_demo.py` runs the runner with `agent.plan`, `report.summarize_for_merchant` and
  `meta.generate_campaign_visual` monkeypatched (no network) and asserts the five folders and files exist, the
  summary scenario has `answer.txt` and no `approval_card.txt`, and a failing scenario does not stop the others.
- `python -m pytest -q` green.
- With a real key, `python scripts/demo.py --only summary` finishes in under a minute and prints the answer.
