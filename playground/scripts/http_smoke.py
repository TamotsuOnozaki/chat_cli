from __future__ import annotations

import json
from collections import Counter

import httpx

PORTS = list(range(8084, 8095))


def pick_port() -> int | None:
    for p in PORTS:
        try:
            r = httpx.get(f"http://localhost:{p}/api/health", timeout=3)
            if r.status_code == 200 and r.json().get("ok"):
                return p
        except Exception:
            pass
    return None


def main() -> int:
    port = pick_port()
    if not port:
        open("http_smoke_result.txt", "w", encoding="utf-8").write("NO_PORT")
        return 1

    base = f"http://localhost:{port}"
    lines: list[str] = [f"PORT={port}"]

    init = httpx.post(f"{base}/api/init", timeout=10)
    init.raise_for_status()
    cid = init.json()["conversation_id"]
    lines.append(f"CID={cid}")

    text = "全員で、それぞれ1つずつ具体的な案をください。テーマは「秋田犬向けオンライン体験イベントの事業案」です。"
    payload = {"conversation_id": cid, "text": text}
    r = httpx.post(f"{base}/api/message", json=payload, timeout=90)
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

    with open("http_smoke_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    with open("http_smoke_events.json", "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
