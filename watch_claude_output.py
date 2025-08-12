import time
import re
import sys
import subprocess
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# パス定義
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = BASE_DIR / "output_claude_writer.txt"
INPUT_PATH = BASE_DIR / "input_claude_writer.txt"
DEFAULT_MODEL = "gpt-4o-mini"


def load_model() -> str:
    import os
    return os.getenv("OPENAI_MODEL", DEFAULT_MODEL)


def build_client() -> OpenAI:
    load_dotenv()
    return OpenAI()


def call_llm(client: OpenAI, content: str) -> str:
    res = client.chat.completions.create(
        model=load_model(),
        messages=[
            {
                "role": "system",
                "content": "あなたは有能なアシスタントです。簡潔かつ具体的に回答してください。"
            },
            {"role": "user", "content": content},
        ],
        temperature=0.2,
    )
    return (res.choices[0].message.content or "").strip()


def run_orchestrator(text: str, roles_csv: str = "idea_ai,writer_ai,proof_ai") -> str:
    """
    multi_agent_orchestrator.py を起動し、“=== Final ===” 以降の最終結果を返す。
    logs は chat_cli\\logs に出るように cwd を固定。
    """
    script = BASE_DIR / "multi_agent_orchestrator.py"
    if not script.exists():
        return "[orchestrator not found: multi_agent_orchestrator.py]"
    cmd = [sys.executable, str(script), "-i", text, "-r", roles_csv]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        cwd=str(BASE_DIR),
    )
    out = proc.stdout or ""
    if "=== Final ===" in out:
        return out.split("=== Final ===", 1)[1].strip()
    return (out.strip() or (proc.stderr or "").strip() or "[empty output]")


class Handler(FileSystemEventHandler):
    def __init__(self, client: OpenAI):
        super().__init__()
        self.client = client
        self._last_content: Optional[str] = None

    def on_modified(self, event):
        # 監視対象のみ
        if Path(event.src_path).resolve() != OUTPUT_PATH:
            return

        # 書き込み直後の揺れ対策
        time.sleep(0.15)

        try:
            content = OUTPUT_PATH.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            return
        if not content or content == self._last_content:
            return
        self._last_content = content

        # [ORCH roles=...] 判定
        m = re.match(r"^\[ORCH(?:\s+roles=([^\]]+))?\]\s*(.*)$", content, re.S | re.I)
        if m:
            roles_csv = (m.group(1) or "idea_ai,writer_ai,proof_ai").strip()
            user_text = (m.group(2) or "").strip()
            reply = run_orchestrator(user_text, roles_csv)
        else:
            reply = call_llm(self.client, content)

        reply_text = (reply or "").strip()
        INPUT_PATH.write_text(reply_text + "\n[STATUS:CONTINUE]", encoding="utf-8")
        print("[wrote] input_claude_writer.txt")


def ensure_files():
    OUTPUT_PATH.touch(exist_ok=True)
    INPUT_PATH.touch(exist_ok=True)
    # 連携先で使うフォルダも用意
    (BASE_DIR / "ai_roles" / "cards").mkdir(parents=True, exist_ok=True)
    (BASE_DIR / "logs").mkdir(parents=True, exist_ok=True)


def main():
    ensure_files()
    client = build_client()

    handler = Handler(client)
    observer = Observer()
    observer.schedule(handler, str(BASE_DIR), recursive=False)
    observer.start()
    print(f"watching {OUTPUT_PATH.name} (Ctrl+C to stop)")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()