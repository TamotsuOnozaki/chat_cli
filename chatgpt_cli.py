import os
from openai import OpenAI
from dotenv import load_dotenv

# .envからAPIキー読み込み
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def ask_chatgpt(prompt):
    try:
        chat_completion = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "あなたは有能なAIアシスタントです。"},
                {"role": "user", "content": prompt}
            ]
        )
        return chat_completion.choices[0].message.content
    except Exception as e:
        return f"エラー: {e}"

def main():
    print("=== ChatGPT CLI（v1対応） ===")
    while True:
        user_input = input("\nあなた > ")
        if user_input.lower() in ["exit", "quit"]:
            print("終了します。")
            break
        reply = ask_chatgpt(user_input)
        print(f"\nChatGPT > {reply}")

if __name__ == "__main__":
    main()
