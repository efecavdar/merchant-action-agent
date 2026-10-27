"""Phase 2: deterministic execution of an APPROVED proposal.

No model decides anything here. Each action maps 1:1 to a backend call, in order,
with temp ids / refs resolved to real ids. Every step is logged for the report.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from PIL import Image

from . import meta_client as meta
from .config import settings
from .pipeline import MediaContext
from .tools.backend import Backend

log = logging.getLogger(__name__)


def execute(proposal: dict[str, Any], ctx: MediaContext, backend: Backend,
            out_dir: Path | None = None) -> list[dict[str, Any]]:
    out_dir = out_dir or settings.out_dir
    crops = {p.temp_id: p for p in ctx.products}
    id_map: dict[str, str] = {}          # temp_id / ref -> real catalog item id
    visual_path: str | None = None
    steps: list[dict[str, Any]] = []

    def record(action: dict[str, Any], status: str, result: Any) -> None:
        steps.append({"action": action["type"], "status": status, "result": result})
        log.info("%s → %s", action["type"], status)

    for a in proposal["actions"]:
        t = a["type"]
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
                )
                id_map[handle] = res["id"]
                record(a, "ok", {"item_id": res["id"], "type": res["type"], "name": res["name"],
                                 "price": res["price"], "from_photo": card is not None})

            elif t == "create_discount":
                ids = [id_map.get(r, r) for r in a["item_refs"]]
                res = backend.create_discount(
                    name=a.get("name") or f"%{a['percent']} indirim", percent=int(a["percent"]),
                    item_ids=ids, starts=a["starts"], ends=a["ends"],
                )
                record(a, "ok", {"discount_id": res["id"], "percent": res["percent"], "items": ids,
                                 "sale_prices": res.get("sale_prices", {})})

            elif t == "generate_visual":
                refs = a.get("item_refs") or list(crops)
                imgs = [Image.open(crops[r].crop_path) for r in refs if r in crops]
                prompt = (a["visual_prompt"] + " Clean studio composition, soft daylight, premium "
                          "small-business look, square format, no text, no watermark, no recognizable faces.")
                path = meta.generate_campaign_visual(imgs, prompt, out_dir / "campaign_visual.png")
                visual_path = str(path)
                record(a, "ok", {"visual_path": visual_path, "real_product_crops_used": len(imgs)})

            elif t == "send_campaign":
                res = backend.send_campaign(
                    name=a.get("name") or f"Kampanya {date.today().isoformat()}",
                    segment_id=a["segment_id"], message=a["message"],
                    visual_path=(visual_path if a.get("attach_visual") else "") or "",
                )
                record(a, "ok", {"campaign_id": res["id"], "recipients": res["recipients"],
                                 "note": res.get("note", "")})

            elif t == "create_appointment":
                res = backend.create_appointment(
                    customer_id=a["customer_id"], item_id=id_map.get(a["item_ref"], a["item_ref"]),
                    staff=a["staff"], starts=a["starts"],
                )
                record(a, "ok", {"appointment_id": res["id"], "starts": res["starts"], "label": a.get("label")})

            elif t == "reschedule_appointment":
                res = backend.reschedule_appointment(a["appointment_id"], a["starts"], a.get("staff"))
                record(a, "ok", {"appointment_id": res["id"], "starts": res["starts"], "label": a.get("label")})

            elif t == "cancel_appointment":
                res = backend.cancel_appointment(a["appointment_id"], a.get("reason") or "")
                record(a, "ok", {"appointment_id": res["id"], "status": res["status"], "label": a.get("label")})
        except Exception as e:
            record(a, "failed", f"{type(e).__name__}: {e}")
            # Fail closed: do not continue sending things after an error.
            break
    return steps
