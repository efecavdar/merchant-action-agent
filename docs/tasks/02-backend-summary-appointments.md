# Task 02 — Backend data for "daily summary" and appointment actions

Branch: `muse/02-backend-summary`. Allowed to edit: `app/tools/backend.py`, `tests/test_offline.py`
(add tests; do not delete existing ones). Do not touch the files in `AGENTS.md` rule 2 — the agent tools and the
executor wiring for these methods will be done separately by a human.

## Goal

Two new use cases need data from the backend:

1. **"Bugün ne oldu?"** — a read-only daily summary the owner can ask for by voice.
2. **Appointment actions** — "Ayşe Hanım'ın randevusunu cumaya al", "yarın 15:00'e saç kesimi yaz Elif'e".

Both the `Backend` protocol and `MockBackend` get the methods; `KeddBackend` gets thin HTTP adapters following the
existing pattern (`/agent/...` paths, same `_get`/`_post` helpers).

## Methods

```python
def daily_summary(self, day: str) -> dict          # read
def find_appointments(self, customer_name: str | None = None, day: str | None = None) -> list[dict]   # read
def create_appointment(self, customer_id: str, item_id: str, staff: str, starts: str) -> dict        # write
def reschedule_appointment(self, appointment_id: str, starts: str, staff: str | None = None) -> dict # write
def cancel_appointment(self, appointment_id: str, reason: str = "") -> dict                          # write
```

### `daily_summary(day)`
Returns, for one ISO date: `appointments` (count, completed, upcoming, no-shows), `revenue` (total TRY and by
item category, from a new seeded `revenue_entries` list — both businesses), `new_customers`, `new_leads`,
`unanswered_messages` (count + up to 3 `{customer, preview, age_minutes}` — synthetic previews, no phone numbers),
`campaigns_sent` (count, recipients) and `catalog_changes` (items added that day). All numbers must be derivable
from the seeded data so the summary is stable across runs (seeded RNG, like the existing customers).
Seed data for the boutique too: it has no appointments but has revenue, leads and messages.

### `find_appointments(customer_name, day)`
Case- and accent-insensitive partial match on customer name ("ayşe" ≈ "Ayşe Y."); returns the matching
appointments as `{id, customer, service, staff, starts, ends, status}`. If several customers match, return all
(the agent will ask the owner). Never return phone numbers.

### `create_appointment`
Validates: service exists and is a service, staff exists, `starts` is inside business hours, the staff member is
free for the service's duration (reuse the overlap logic behind `get_schedule`), and the customer exists.
On conflict raise `ValueError` with a Turkish-readable reason (the executor shows it). Returns the appointment dict.

### `reschedule_appointment` / `cancel_appointment`
Same validation; cancelled appointments get `status="cancelled"` and free their slot. Rescheduling keeps the same
service and customer.

`get_schedule` must keep working unchanged (existing test) and must ignore cancelled appointments.
`report()` should include `appointments_changed` (list of the appointments created/moved/cancelled since start).

## Acceptance

- `python -m pytest -q` green, with new tests covering: deterministic daily summary for both seeds; name matching
  with Turkish accents; create inside/outside hours; conflict with an existing appointment raises; reschedule frees
  the old slot (visible in `get_schedule`); cancel is reflected in `get_schedule` and `report()`.
- No phone numbers anywhere in the returned dicts (assert `"phone" not in json.dumps(result)` in tests).
- `save()` persists the new collections.
