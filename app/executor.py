"""Phase 2: deterministic execution of an APPROVED proposal.

No model decides anything here. Each action maps 1:1 to a backend call, in order,
with temp ids / refs resolved to real ids. Every step is logged for the report.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from . import meta_client as meta
from .config import settings
from .pipeline import MediaContext
from .tools.backend import Backend, KeddBackend

log = logging.getLogger(__name__)

# Same art direction for campaign and social visuals: the product stays the hero, square, no text.
_VISUAL_SUFFIX = (" Clean studio composition, soft daylight, premium small-business look,"
                  " square format, no text, no watermark, no recognizable faces.")


def prepare_social_previews(proposal: dict[str, Any], ctx: MediaContext, run_dir: Path) -> None:
    """Generate the social visuals BEFORE the owner approves, so the card shows exactly what
    will be published. Mutates the proposal: each publish_social_post gets `_preview_path`;
    an action whose visual fails is dropped and explained in `proposal[\"_social_notes\"]`."""
    notes: list[str] = []
    # "_" keys are ours, never the model's: a model-written path or note must not reach the card or the publish.
    proposal.pop("_social_notes", None)
    for a in proposal.get("actions") or []:
        for k in [k for k in a if k.startswith("_")]:
            a.pop(k)
    crops = {p.temp_id: p for p in ctx.products}
    keep: list[dict[str, Any]] = []
    for pos, a in enumerate(proposal.get("actions") or []):
        if a.get("type") != "publish_social_post":
            keep.append(a)
            continue
        plats = ", ".join(p.capitalize() for p in (a.get("platforms") or [])) or "sosyal medya"
        try:
            refs = [r for r in (a.get("item_refs") or []) if r in crops]
            imgs = [Image.open(crops[r].crop_path) for r in refs]
            out = run_dir / f"social_preview_{pos}.png"
            meta.generate_campaign_visual(imgs, a.get("visual_prompt", "") + _VISUAL_SUFFIX, out)
            a["_preview_path"] = str(out)
            keep.append(a)
        except Exception as e:  # noqa: BLE001 — a dead image endpoint must not kill the card
            log.warning("social preview failed: %s", type(e).__name__)
            notes.append(f"{plats} gönderisi için görsel üretemedim, onu plandan çıkardım"
                         " — tekrar dene istersen.")
    proposal["actions"] = keep
    if notes:
        proposal["_social_notes"] = notes


def execute(proposal: dict[str, Any], ctx: MediaContext, backend: Backend,
            out_dir: Path | None = None,
            on_step: Callable[[str, int, int, dict[str, Any] | None], None] | None = None,
            ) -> list[dict[str, Any]]:
    """Run the approved actions in order, failing closed on the first error.

    on_step(event, i, n, step) announces progress: ("start", 0, n, None) once, then one event
    per action ("ok" | "failed", 1-based i) plus ("skipped", …) for actions never attempted
    after a failure. The callback is best effort: its exceptions are swallowed and logged
    (type only) and can never change what executes or what is returned.
    """
    out_dir = out_dir or settings.out_dir
    crops = {p.temp_id: p for p in ctx.products}
    id_map: dict[str, str] = {}          # temp_id / ref -> real catalog item id
    crm_refs: dict[str, str] = {}        # ref -> real customer / lead / task id, created earlier
    visual_path: str | None = None
    steps: list[dict[str, Any]] = []
    actions = proposal["actions"]
    n = len(actions)

    def record(action: dict[str, Any], status: str, result: Any) -> None:
        steps.append({"action": action["type"], "status": status, "result": result})
        log.info("%s → %s", action["type"], status)

    def emit(event: str, i: int, step: dict[str, Any] | None) -> None:
        if on_step is None:
            return
        try:
            on_step(event, i, n, step)
        except Exception as e:  # noqa: BLE001 — progress must never affect execution
            log.warning("progress callback failed: %s", type(e).__name__)

    emit("start", 0, None)
    failed_at: int | None = None
    for i, a in enumerate(actions):
        t = a["type"]
        # Stable key per approved action: a retried delivery of the same approval reuses the
        # key, so the gateway answers from its stored result instead of running twice.
        # Only KeddBackend takes the key; the mock path is unaffected.
        idem = {"idempotency_key": f"{out_dir.name}-{i}"} if isinstance(backend, KeddBackend) else {}
        marked = len(steps)
        try:
            if t == "add_catalog_item":
                card = crops.get(a.get("temp_id") or "")
                handle = a.get("temp_id") or a["ref"]
                res = backend.add_catalog_item(
                    item_type=a["item_type"],
                    name=a.get("name") or (card.name if card else ""),
                    price=float(a["price"]),
                    category=a.get("category") or (card.category if card else ""),
                    description=a.get("description") or (card.description if card else ""),
                    image_path=card.crop_path if card else "",
                    duration_minutes=a.get("duration_minutes"),
                    **idem,
                )
                id_map[handle] = res["id"]
                record(a, "ok", {"item_id": res["id"], "type": res["type"], "name": res["name"],
                                 "price": res["price"], "from_photo": card is not None})

            elif t == "create_discount":
                ids = [id_map.get(r, r) for r in a["item_refs"]]
                res = backend.create_discount(
                    name=a.get("name") or f"%{a['percent']} indirim", percent=int(a["percent"]),
                    item_ids=ids, starts=a["starts"], ends=a["ends"],
                    **idem,
                )
                record(a, "ok", {"discount_id": res["id"], "percent": res["percent"], "items": ids,
                                 "starts": a["starts"], "ends": a["ends"],
                                 "sale_prices": res.get("sale_prices", {})})

            elif t == "generate_visual":
                refs = a.get("item_refs") or list(crops)
                imgs = [Image.open(crops[r].crop_path) for r in refs if r in crops]
                prompt = a["visual_prompt"] + _VISUAL_SUFFIX
                path = meta.generate_campaign_visual(imgs, prompt, out_dir / "campaign_visual.png")
                visual_path = str(path)
                record(a, "ok", {"visual_path": visual_path, "real_product_crops_used": len(imgs)})

            elif t == "send_campaign":
                res = backend.send_campaign(
                    name=a.get("name") or f"Kampanya {date.today().isoformat()}",
                    segment_id=a["segment_id"], message=a.get("message") or "",
                    visual_path=(visual_path if a.get("attach_visual") else "") or "",
                    template=a.get("template"), variables=a.get("variables"),
                    **idem,
                )
                record(a, "ok", {"campaign_id": res["id"], "recipients": res["recipients"],
                                 "note": res.get("note", "")})

            elif t == "publish_social_post":
                preview = a.get("_preview_path") or ""
                if not preview:
                    raise ValueError("Sosyal gönderi için onaylı bir görsel yok,"
                                     " planı tekrar çalıştırır mısın?")
                res = backend.publish_social_post(
                    caption=a["caption"], image_path=preview, platforms=list(a["platforms"]),
                    publish_at=a.get("publish_at"),
                    **idem,
                )
                record(a, "ok", {"campaign_id": res["campaign_id"], "status": res["status"],
                                 "scheduled_for": res["scheduled_for"], "platforms": a["platforms"],
                                 "note": res.get("note", "")})

            elif t == "create_appointment":
                res = backend.create_appointment(
                    customer_id=a["customer_id"], item_id=id_map.get(a["item_ref"], a["item_ref"]),
                    staff=a["staff"], starts=a["starts"],
                    **idem,
                )
                record(a, "ok", {"appointment_id": res["id"], "starts": res["starts"], "label": a.get("label")})

            elif t == "reschedule_appointment":
                res = backend.reschedule_appointment(a["appointment_id"], a["starts"], a.get("staff"),
                                           **idem)
                record(a, "ok", {"appointment_id": res["id"], "starts": res["starts"], "label": a.get("label")})

            elif t == "cancel_appointment":
                res = backend.cancel_appointment(a["appointment_id"], a.get("reason") or "", **idem)
                record(a, "ok", {"appointment_id": res["id"], "status": res["status"], "label": a.get("label")})

            elif t == "apply_bulk_price":
                res = backend.apply_bulk_price(a["preview_id"], **idem)
                record(a, "ok", {"preview_id": res["preview_id"], "updated": res["count"],
                                 "items": res["items"], "label": a.get("label")})

            elif t == "adjust_loyalty":
                res = backend.adjust_loyalty(a["customer_id"], int(a["points"]), a.get("reason") or "",
                                     **idem)
                record(a, "ok", {"customer_id": res["customer_id"], "points": res["points"],
                                 "delta": res["delta"], "label": a.get("label")})

            elif t == "ads_action":
                res = backend.ads_action(a["campaign_id"], a["action"], a.get("delta_pct"), **idem)
                record(a, "ok", {"campaign_id": res["id"], "status": res["status"],
                                 "daily_budget": res["daily_budget"], "label": a.get("label")})

            elif t == "create_customer":
                res = backend.create_customer(a["name"], phone=a.get("phone"), email=a.get("email"),
                                              tags=a.get("tags"), note=a.get("note"), **idem)
                if a.get("ref"):
                    crm_refs[a["ref"]] = res["id"]
                record(a, "ok", {"customer_id": res["id"], "name": res["name"],
                                 "tags": list(a.get("tags") or [])})

            elif t == "update_customer":
                res = backend.update_customer(crm_refs.get(a["customer_id"], a["customer_id"]),
                                              name=a.get("name"), add_tags=a.get("add_tags"),
                                              remove_tags=a.get("remove_tags"), note=a.get("note"),
                                              **idem)
                record(a, "ok", {"customer_id": res["id"], "name": res["name"], "tags": res["tags"],
                                 "added_tags": list(a.get("add_tags") or []),
                                 "removed_tags": list(a.get("remove_tags") or []),
                                 "note_added": a.get("note") is not None,
                                 "renamed": a.get("name") is not None})

            elif t == "create_lead":
                res = backend.create_lead(a["name"], phone=a.get("phone"), email=a.get("email"),
                                          estimated_value=a.get("estimated_value"),
                                          tags=a.get("tags"), note=a.get("note"), **idem)
                if a.get("ref"):
                    crm_refs[a["ref"]] = res["id"]
                record(a, "ok", {"lead_id": res["id"], "name": res["name"],
                                 "value": a.get("estimated_value")})

            elif t == "set_lead_stage":
                res = backend.set_lead_stage(crm_refs.get(a["lead_id"], a["lead_id"]),
                                             a["stage"], **idem)
                record(a, "ok", {"lead_id": res["id"], "stage": res["stage"],
                                 "prev_stage": res.get("prev_stage")})

            elif t == "add_lead_note":
                res = backend.add_lead_note(crm_refs.get(a["lead_id"], a["lead_id"]),
                                            a["note"], **idem)
                record(a, "ok", {"lead_id": res["id"], "note_id": res["note_id"],
                                 "note": a.get("note")})

            elif t == "create_task":
                res = backend.create_task(a["title"], a["due_date"],
                                          customer_id=crm_refs.get(a["customer_id"], a.get("customer_id"))
                                          if a.get("customer_id") else None,
                                          lead_id=crm_refs.get(a["lead_id"], a.get("lead_id"))
                                          if a.get("lead_id") else None,
                                          **idem)
                if a.get("ref"):
                    crm_refs[a["ref"]] = res["id"]
                record(a, "ok", {"task_id": res["id"], "title": res["title"],
                                 "due_date": res["due_date"], "status": res["status"]})

            elif t == "complete_task":
                res = backend.complete_task(crm_refs.get(a["task_id"], a["task_id"]), **idem)
                record(a, "ok", {"task_id": res["id"], "status": res["status"]})
        except Exception as e:
            record(a, "failed", f"{type(e).__name__}: {e}")
            emit("failed", i + 1, steps[-1])
            # Fail closed: do not continue sending things after an error.
            failed_at = i
            break
        else:
            if len(steps) > marked:  # an unrecognized type records nothing and announces nothing
                emit("ok", i + 1, steps[-1])
    if failed_at is not None:
        # Announced only: skipped actions ran nothing and leave no step record behind.
        for j in range(failed_at + 1, n):
            emit("skipped", j + 1, {"action": actions[j]["type"], "status": "skipped", "result": ""})
    return steps
