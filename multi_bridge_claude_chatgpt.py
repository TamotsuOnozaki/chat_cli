import os
import time
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv

# 環境変数からOpenAIキーを取得
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ファイルパス定義
CLAUDE_OUTPUT = "claude_output.txt"
CLAUDE_INPUT = "claude_input.txt"
LOG_FILE = "response_log.txt"

# Claudeの出力を読む
def read_claude_output():
    try:
        with open(CLAUDE_OUTPUT, "r", encoding="utf-8") as f:
            return f.read()
    except:
        return ""

# ChatGPTに送る
def ask_chatgpt(prompt):
    try:
        chat = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "あなたはClaudeの出力を受け取り、次にClaudeに送るべき応答案を考えるアシスタントです。"},
                {"role": "user", "content": prompt}
            ]
        )
        return chat.choices[0].message.content
    except Exception as e:
        return f"[エラー] {e}"

# Claude入力ファイルに保存（Claudeが読む用）
def write_to_claude_input(text):
    with open(CLAUDE_INPUT, "w", encoding="utf-8") as f:
        f.write(text)

# 応答ログに保存
def save_log(prompt, reply):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n==== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ====\n")
        f.write("[Claudeの出力]\n" + prompt + "\n")
        f.write("[ChatGPTの返答 → Claudeへ]\n" + reply + "\n")

# メインループ
def main():
    print("🔁 Claude ⇄ ChatGPT 多段中継ブリッジ 起動中...（Ctrl+Cで停止）\n")
    last_output = read_claude_output()

    while True:
        time.sleep(3)
        current_output = read_claude_output()
        if current_output and current_output != last_output:
            print("🆕 Claudeの出力を検知 → ChatGPTへ転送中...\n")
            reply = ask_chatgpt(current_output)
            print("✅ ChatGPTの返答（Claudeへ送信）：\n")
            print(reply)

            write_to_claude_input(reply)
            save_log(current_output, reply)

            print("\n📤 Claude_input.txt に送信完了。ログ保存済み。\n")
            last_output = current_output

if __name__ == "__main__":
    main()
