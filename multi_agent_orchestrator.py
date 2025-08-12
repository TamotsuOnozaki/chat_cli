import os, json, argparse, datetime
from typing import List, Dict
from dotenv import load_dotenv
from openai import OpenAI

DEFAULT_MODEL = "gpt-4o-mini"
CARDS_PATH = os.path.join("ai_roles", "cards", "cards.sample.json")
LOGS_DIR = "logs"

def load_cards(path: str) -> Dict[str, dict]:
    # UTF-8 (BOM付き/なし) 両対応
    with open(path, "r", encoding="utf-8-sig") as f:
        arr = json.load(f)
    return {c["id"]: c for c in arr}

def call_role(client: OpenAI, card: dict, text: str, model: str) -> str:
    sys = card.get("system_prompt", "")
    res = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": sys},
            {"role": "user", "content": text},
        ],
        temperature=0.4,
    )
    return res.choices[0].message.content.strip()

def log_jsonl(role: str, prompt: str, output: str):
    os.makedirs(LOGS_DIR, exist_ok=True)
    path = os.path.join(LOGS_DIR, f"orch_{datetime.date.today().isoformat()}.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": datetime.datetime.now().isoformat(),
            "role": role,
            "prompt": prompt,
            "output": output,
        }, ensure_ascii=False) + "\n")

def run_pipeline(text: str, roles: List[str]):
    load_dotenv()
    client = OpenAI()
    model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
    cards = load_cards(CARDS_PATH)

    current = text
    for r in roles:
        card = cards.get(r)
        if not card:
            raise SystemExit(f"role not found: {r}")
        print(f"\n=== {card.get('title', r)} ({r}) ===")
        out = call_role(client, card, current, model)
        print(out)
        log_jsonl(r, current, out)
        current = out
    print("\n=== Final ===\n" + current)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--input", required=True, help="input text")
    ap.add_argument("-r", "--roles", default="idea_ai,writer_ai,proof_ai",
                    help="comma-separated role ids")
    args = ap.parse_args()
    roles = [s.strip() for s in args.roles.split(",") if s.strip()]
    run_pipeline(args.input, roles)