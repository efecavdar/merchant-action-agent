"""Gateway smoke test on the demo key only. Reads every endpoint plus one campaign
dry run, then prints one table (endpoint, HTTP status, ms, row count).

    KEDD_API_URL=... KEDD_DEMO_SECRET=... KEDD_DEMO_PHONE=... python scripts/smoke_kedd.py

No writes except the dry run (dry_run: true is hardcoded in preview_campaign).
Refuses to run without KEDD_DEMO_SECRET. Prints no secrets, names or phone
numbers — a failing endpoint shows its status, never the gateway message.
Exit 0 only when every endpoint answers 2xx (loyalty/members is expected to 404
until gateway K5.1 deploys).
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.tools.backend import KeddBackend, get_backend  # noqa: E402

_PROBE_NAME = "a"  # single-letter lookup probe, not a real customer name


def _install_recorder(kb: KeddBackend) -> list[tuple]:
    """Record (method, path, status) of every HTTP call the probes make.

    The adapter raises above 400 without surfacing the code, so the smoke test
    wraps the session to report honest per-endpoint statuses instead of "200/ERR".
    """
    calls: list[tuple] = []
    sess, base = kb._s, kb.base
    orig_get, orig_post = sess.get, sess.post

    def _path(url: str) -> str:
        return url[len(base):].split("?")[0] if url.startswith(base) else url.split("?")[0]

    def get(url: str, headers: Any = None, timeout: Any = None) -> Any:
        try:
            r = orig_get(url, headers=headers, timeout=timeout)
        except Exception:  # noqa: BLE001 — transport failure is a row, not a crash
            calls.append(("GET", _path(url), None))
            raise
        calls.append(("GET", _path(url), getattr(r, "status_code", None)))
        return r

    def post(url: str, data: Any = None, headers: Any = None, timeout: Any = None) -> Any:
        try:
            r = orig_post(url, data=data, headers=headers, timeout=timeout)
        except Exception:  # noqa: BLE001 — transport failure is a row, not a crash
            calls.append(("POST", _path(url), None))
            raise
        calls.append(("POST", _path(url), getattr(r, "status_code", None)))
        return r

    sess.get, sess.post = get, post  # type: ignore[method-assign]
    return calls


def run_smoke(kb: KeddBackend, day: str) -> list[dict[str, Any]]:
    """Run every read plus one campaign dry run. One row per endpoint; a failing
    endpoint records its status and the rest still run."""
    http = _install_recorder(kb)
    rows: list[dict[str, Any]] = []
    ctx: dict[str, Any] = {}

    def probe(method: str, path: str, fn: Any, count: Any) -> None:
        def status(new: list[tuple]) -> Any:
            hits = [s for m, p, s in new if m == method and p == path and s is not None]
            return hits[-1] if hits else "ERR"

        before, start = len(http), time.monotonic()
        try:
            res = fn()
        except Exception:  # noqa: BLE001 — every failure mode is one honest row
            rows.append({"method": method, "path": path, "status": status(http[before:]),
                         "ms": _ms(start), "rows": "-"})
            return
        rows.append({"method": method, "path": path, "status": status(http[before:]),
                     "ms": _ms(start), "rows": count(res)})

    def segments() -> dict[str, Any]:
        res = kb.find_customers(inactive_days=60)
        ctx["segment_id"] = res["segment_id"]
        return res

    def templates() -> list[dict[str, Any]]:
        res = kb.campaign_templates()
        ctx["templates"] = res
        return res

    def dry_run() -> dict[str, Any]:
        tmpl = (ctx.get("templates") or [{}])[0]
        variables = {str(v.get("number")): "SMOKE" for v in tmpl.get("variables") or []
                     if str(v.get("number")) != "1"}
        return kb.preview_campaign(ctx.get("segment_id", ""), tmpl.get("name", ""), variables)

    probe("GET", "/agent/profile", kb.store_profile, lambda _r: 1)
    probe("GET", "/agent/catalog", kb.list_catalog, len)
    probe("GET", "/agent/schedule", lambda: kb.get_schedule(day),
          lambda r: len(r.get("appointments") or []))
    probe("GET", "/agent/customers", lambda: kb.find_customer(_PROBE_NAME), len)
    probe("GET", "/agent/daily-summary", lambda: kb.daily_summary(day), lambda _r: 1)
    probe("POST", "/agent/segments", segments, lambda r: r.get("count", 0) or 0)
    probe("GET", "/agent/campaign-templates", templates, len)
    probe("GET", "/agent/loyalty/members", lambda: kb.find_loyalty_member(_PROBE_NAME),
          lambda r: len(r.get("members") or []))
    probe("GET", "/agent/ads/performance", lambda: kb.ads_performance(7),
          lambda r: len(r.get("campaigns") or []))
    probe("GET", "/agent/report", kb.agent_write_counts, lambda r: len(r.get("counts") or {}))
    probe("POST", "/agent/campaigns", dry_run, lambda r: r.get("recipients", 0) or 0)
    return rows


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _print(rows: list[dict[str, Any]]) -> None:
    print(f"{'endpoint':<32}{'status':>8}{'ms':>8}{'rows':>8}")
    for r in rows:
        print(f"{r['method'] + ' ' + r['path']:<32}{str(r['status']):>8}{r['ms']:>8}"
              f"{str(r['rows']):>8}")


def main(argv: list[str] | None = None) -> int:
    _ = argv
    missing = [k for k in ("KEDD_API_URL", "KEDD_DEMO_SECRET", "KEDD_DEMO_PHONE")
               if not os.environ.get(k)]
    if missing:
        print(f"smoke_kedd: missing {', '.join(missing)} in env; refusing to run",
              file=sys.stderr)
        return 1
    kb = get_backend("kedd-demo")  # the ONLY key this script can ever use
    assert isinstance(kb, KeddBackend) and kb._key_id == "demo"
    rows = run_smoke(kb, date.today().isoformat())
    _print(rows)
    return 0 if all(isinstance(r["status"], int) and 200 <= r["status"] < 300
                    for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
