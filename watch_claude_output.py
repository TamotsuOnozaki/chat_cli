import os
import time
from openai import OpenAI
from dotenv import load_dotenv
from datetime import datetime

# .envからOpenAI APIキーを読み込み
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

CLAUDE_FILE = "claude_output.txt"
LOG_FILE = "response_log.txt"

def read_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except:
        return ""

def ask_chatgpt(prompt):
    try:
        chat = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "あなたはClaudeの出力をレビューし、補足や改善を提案するアシスタントです。"},
                {"role": "user", "content": prompt}
            ]
        )
        return chat.choices[0].message.content
    except Exception as e:
        return f"[エラー] {e}"

def save_log(prompt, reply):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n==== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ====\n")
        f.write("[Claudeの出力]\n" + prompt + "\n")
        f.write("[ChatGPTの返答]\n" + reply + "\n")

def watch_file():
    print("🔍 Claudeの出力を監視中...（終了するには Ctrl+C）\n")
    last_content = read_file(CLAUDE_FILE)
    while True:
        time.sleep(2)
        current_content = read_file(CLAUDE_FILE)
        if current_content and current_content != last_content:
            print("🆕 Claudeの出力が更新されました。ChatGPTに送信中...\n")
            reply = ask_chatgpt(current_content)
            print("✅ ChatGPTの応答：\n")
            print(reply)
            save_log(current_content, reply)
            print("\n📝 response_log.txt に保存しました。\n")
            last_content = current_content

if __name__ == "__main__":
    watch_file()
