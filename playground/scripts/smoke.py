from __future__ import annotations

"""
Simple smoke test without starting uvicorn.
It imports the FastAPI app directly and exercises /api/init and /api/message.
Outputs compact stats: lanes distribution, consult lanes, role counts.
"""

import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # type: ignore
from app import app  # type: ignore


def main() -> int:
    client = TestClient(app)

    lines: list[str] = []

    # health
    h = client.get("/api/health").json()
    lines.append(f"HEALTH ok={h.get('ok')} roles={h.get('roles')} model={h.get('model')}")

    # init
    init = client.post("/api/init").json()
    cid = init["conversation_id"]
    lines.append(f"CID={cid}")

    # message (all participants request)
    text = "全員で、それぞれ1つずつ具体的な案をください。テーマは「秋田犬向けオンライン体験イベントの事業案」です。"
    payload = {"conversation_id": cid, "text": text}
    r = client.post("/api/message", json=payload)
    r.raise_for_status()
    events = r.json()["events"]

    lanes = [e.get("lane") for e in events]
    roles = [e.get("role") for e in events]
    c_lanes = Counter(lanes)
    c_roles = Counter(roles)
    consult_lanes = sorted({l for l in lanes if isinstance(l, str) and l.startswith("consult:")})

    lines.append("LANES=" + ",".join(f"{k}:{v}" for k, v in c_lanes.items()))
    lines.append("CONSULT=" + ",".join(consult_lanes))
    lines.append("ROLES=" + ",".join(f"{k}:{v}" for k, v in c_roles.items()))

    # Show one sample head per consult lane
    shown = set()
    for e in events:
        lane = e.get("lane") or ""
        if not lane.startswith("consult:"):
            continue
        if lane in shown:
            continue
        txt = (e.get("text") or "").strip().replace("\n", " ")
        head = txt[:80]
        lines.append(f"SAMPLE {lane} -> {e.get('role')}: {head}")
        shown.add(lane)
        if len(shown) >= 6:
            break

    # write to file for stable retrieval
    out_path = ROOT / "smoke_result.txt"
    out_path.write_text("\n".join(lines), encoding="utf-8")

    # still print
    for ln in lines:
        print(ln)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
