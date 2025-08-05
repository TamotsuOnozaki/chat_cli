import os
from openai import OpenAI
from dotenv import load_dotenv
import time

# .env ファイルから OpenAI APIキー読み込み
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# 初期掲示板の読み込み
def load_bulletin_board():
    try:
        with open("bulletin-board.md", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "📌 掲示板ファイルが見つかりませんでした。bulletin-board.md を作成してください。"

# ChatGPTへ質問を送信
def ask_chatgpt(prompt):
    try:
        chat_completion = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "あなたは優秀な企画支援AIアシスタントです。Markdownスタイルで返答してください。"},
                {"role": "user", "content": prompt}
            ]
        )
        return chat_completion.choices[0].message.content
    except Exception as e:
        return f"⚠️ エラー: {e}"

# メイン処理
def main():
    print("※ Welcome to ChatGPT CLI ※")
    print("/help で使い方表示・/exit で終了\n")
    print(f"cwd: {os.getcwd()}\n")
    time.sleep(0.5)

    # 掲示板読み込み
    print("● 掲示板ファイルを読み込み中...")
    board = load_bulletin_board()
    print("○ Read(bulletin-board.md)\n")
    print(f"{board}\n")

    # 入力ループ
    while True:
        user_input = input("> あなた > ")
        if user_input.lower() in ["exit", "quit"]:
            print("✔️ セッションを終了します。")
            break
        elif user_input.lower() in ["help", "/help"]:
            print("🟡 使い方：質問を入力してください。/exit で終了します。\n")
            continue
        print("ChatGPT > ", end="", flush=True)
        reply = ask_chatgpt(user_input)
        print(f"\n{reply}\n")

if __name__ == "__main__":
    main()
