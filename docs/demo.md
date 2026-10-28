# Demo runner

One command reproduces every scenario from the README against the mock backend and leaves a tidy
folder per scenario for the submission video. No WhatsApp setup and no KEDD access needed — only a
`MODEL_API_KEY`.

## Run it

```bash
cp .env.example .env            # put your MODEL_API_KEY in it
python scripts/demo.py                                   # all five scenarios → out/demo/
python scripts/demo.py --only summary                    # a single scenario (under a minute)
python scripts/demo.py --out /tmp/demo --no-images       # custom folder, text-only
```

`--only` takes `butik`, `service`, `salon`, `appointment` or `summary`. Each scenario gets a fresh
mock backend (today = real today) and is auto-approved, so the run needs no input. If
`demo/sample/bags.jpg` exists it is attached to scenario 01, otherwise 01 runs text-only and says so.
A failing scenario is reported and skipped; the exit code is 1 at the end if any scenario failed.

## What each folder contains

`out/demo/01-butik/` … `out/demo/05-summary/`, each with:

| File | Contents |
|---|---|
| `instruction.txt` | What the merchant said (Turkish), plus attached photo names |
| `tool_log.json` | Every tool call the planner made, with arguments and results |
| `approval_card.txt` | The human-approval card — or `answer.txt` when there was nothing to approve |
| `report.json` | Machine-readable execution result (steps + backend state) |
| `summary.txt` | The short Turkish reply the merchant would receive |
| `campaign_visual.png` | The generated campaign visual, when the plan produced one |
| `crops/` | Segmented product crops, when photos were attached |
| `error.txt` | Traceback, only if that scenario failed |

The five scenarios:

1. `01-butik` — boutique: *"Yeni ipek fular koleksiyonu ekle, tanesi 850 lira, hafta sonu %15 indirim,
   uzun süredir gelmeyenlere haber ver"* (+ photo when available). Catalog → discount → visual → campaign.
2. `02-service` — salon: *"Keratin bakımı XL ekle, 90 dakika, 2500 lira"*. A text-only service add.
3. `03-salon-campaign` — salon: *"Yarın öğleden sonra boşluklarım var, saç boyamada %20 indirim, sadık
   müşterilere haber ver"*. Schedule-aware discount + campaign to loyal customers.
4. `04-appointment` — salon: *"Ayşe E.'nin yarınki randevusunu 17:15'e al"*. Finds the existing
   appointment and moves it.
5. `05-summary` — salon: *"Bugün ne oldu?"*. Read-only answer, no approval card, no execution.

## 90-second video storyboard

Total ~90 seconds. Show the file named in each beat (terminal `cat` or editor).

| Time | Show | Say |
|---|---|---|
| 0:00–0:10 | Terminal: `python scripts/demo.py`, folders appearing under `out/demo/` | "One command runs all five demo scenarios." |
| 0:10–0:25 | `01-butik/`: `instruction.txt`, `crops/`, `approval_card.txt`, `campaign_visual.png` | "A boutique owner sends a photo and a voice note; Çırak plans, a human approves, the shop updates." |
| 0:25–0:35 | `02-service/`: `approval_card.txt`, then `summary.txt` | "Adding a service takes one sentence — no dashboard." |
| 0:35–0:50 | `03-salon-campaign/`: `approval_card.txt` (discount + segment count), `campaign_visual.png` | "Empty afternoon tomorrow: discount plus a campaign to loyal customers." |
| 0:50–1:05 | `04-appointment/`: `instruction.txt`, `tool_log.json` (find → reschedule), `summary.txt` | "Moving an appointment is one message; the old slot frees up." |
| 1:05–1:20 | `05-summary/`: `answer.txt` | "And at the end of the day: *Bugün ne oldu?* — answered, nothing executed." |
| 1:20–1:30 | Terminal: final results table | "Reproducible with one key; every number comes from the mock backend." |
