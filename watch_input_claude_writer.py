import os
import time
import subprocess

INPUT_FILE = "input_claude_writer.txt"
TEMP_FLAG = ".last_input_check"

def get_last_modified_time(path):
    try:
        return os.path.getmtime(path)
    except FileNotFoundError:
        return 0

def main():
    print(f"👀 Monitoring {INPUT_FILE} for updates...")

    last_checked = get_last_modified_time(TEMP_FLAG)

    while True:
        time.sleep(2)  # 2秒間隔でチェック

        if not os.path.exists(INPUT_FILE):
            continue

        current_mtime = get_last_modified_time(INPUT_FILE)

        if current_mtime > last_checked:
            print("🆕 新しい入力を検出しました。Claudeに送信中...")

            # Claudeコード起動（writer-aiを想定）
            subprocess.run([
                "wsl",
                "-e",
                "bash",
                "-c",
                "cd ~/my-ai-team/writer-ai && cat ~/chat_cli/input_claude_writer.txt | claude"
            ])

            # チェックタイム更新
            with open(TEMP_FLAG, "w") as f:
                f.write(str(time.time()))
            last_checked = current_mtime

if __name__ == "__main__":
    main()
