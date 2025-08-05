# Claude ➝ ChatGPT に送信するスクリプトの例
import os
import time

CLAUDE_OUTPUT = "output_claude_writer.txt"
INPUT_TO_GPT = "input_chatgpt.txt"
LAST_FLAG = ".last_claude_writer_check"

def read_latest_claude_response():
    try:
        with open(CLAUDE_OUTPUT, "r", encoding="utf-8") as f:
            return f.read().strip()
    except:
        return ""

def main():
    print("🔁 Claudeの出力を監視中...")

    last_time = os.path.getmtime(LAST_FLAG) if os.path.exists(LAST_FLAG) else 0

    while True:
        time.sleep(2)
        if not os.path.exists(CLAUDE_OUTPUT):
            continue

        current_time = os.path.getmtime(CLAUDE_OUTPUT)
        if current_time > last_time:
            msg = read_latest_claude_response()
            if msg:
                print("📩 Claudeの返答をChatGPTに送信します。")
                with open(INPUT_TO_GPT, "w", encoding="utf-8") as f:
                    f.write(msg)

                with open(LAST_FLAG, "w") as f:
                    f.write(str(time.time()))
                last_time = current_time

if __name__ == "__main__":
    main()
