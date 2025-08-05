import os
from openai import OpenAI
from dotenv import load_dotenv
from datetime import datetime

# .envからAPIキー取得
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Claudeの出力ファイルパス
CLAUDE_FILE = "claude_output.txt"
LOG_FILE = "response_log.txt"

# Claudeの出力を読み込む
def read_claude_output():
    try:
        with open(CLAUDE_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None

# ChatGPTへ送信して応答を取得
def ask_chatgpt(prompt):
    try:
        completion = client.chat.completions.create(
            model="gpt-4",  # 必要に応じて "gpt-3.5-turbo"
            messages=[
                {"role": "system", "content": "あなたはClaudeの出力をレビューして補足・改善するアシスタントです。"},
                {"role": "user", "content": prompt}
            ]
        )
        return completion.choices[0].message.content
    except Exception as e:
        return f"[エラー]: {e}"

# 応答をログに保存
def save_log(prompt, reply):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n==== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ====\n")
        f.write("[Claudeの出力]\n" + prompt + "\n")
        f.write("[ChatGPTの返答]\n" + reply + "\n")

# メイン処理
def main():
    print("🧠 Claude → ChatGPT ブリッジ起動中...\n")

    claude_text = read_claude_output()
    if not claude_text:
        print("❌ claude_output.txt が見つからないか空です。Claudeの出力を保存してください。")
        return

    print("📥 Claudeの出力を読み込みました。ChatGPTに送信中...\n")
    response = ask_chatgpt(claude_text)
    print("✅ ChatGPTの応答：\n")
    print(response)

    save_log(claude_text, response)
    print("\n📝 応答は response_log.txt に保存されました。")

if __name__ == "__main__":
    main()
