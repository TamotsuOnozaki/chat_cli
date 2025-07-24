import tkinter as tk
from tkinter import scrolledtext
import os
import time
from threading import Thread

# ファイルパス
CLAUDE_FILE = "claude_output.txt"
LOG_FILE = "response_log.txt"

# Claudeの出力を読む
def read_claude():
    try:
        with open(CLAUDE_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except:
        return "❌ claude_output.txt が見つかりません。"

# ChatGPTの最新応答を読む（ログの最後の返答のみ）
def read_latest_chatgpt():
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.read().split("====")
            if len(lines) < 2:
                return "❌ response_log.txt に応答が見つかりません。"
            latest_block = lines[-1]
            if "[ChatGPTの返答]" in latest_block:
                return latest_block.split("[ChatGPTの返答]")[-1].strip()
            else:
                return "⚠️ ChatGPTの応答が正しく記録されていません。"
    except:
        return "❌ response_log.txt が見つかりません。"

# GUI更新ループ
def update_loop(claude_textbox, chatgpt_textbox):
    last_claude, last_gpt = "", ""
    while True:
        current_claude = read_claude()
        current_gpt = read_latest_chatgpt()
        if current_claude != last_claude:
            claude_textbox.config(state="normal")
            claude_textbox.delete("1.0", tk.END)
            claude_textbox.insert(tk.END, current_claude)
            claude_textbox.config(state="disabled")
            last_claude = current_claude
        if current_gpt != last_gpt:
            chatgpt_textbox.config(state="normal")
            chatgpt_textbox.delete("1.0", tk.END)
            chatgpt_textbox.insert(tk.END, current_gpt)
            chatgpt_textbox.config(state="disabled")
            last_gpt = current_gpt
        time.sleep(2)

# メインGUI
def launch_gui():
    root = tk.Tk()
    root.title("Claude & ChatGPT Viewer")

    # レイアウト
    tk.Label(root, text="Claudeの出力", font=("Arial", 12, "bold")).grid(row=0, column=0, padx=10, pady=5)
    tk.Label(root, text="ChatGPTの応答", font=("Arial", 12, "bold")).grid(row=0, column=1, padx=10, pady=5)

    claude_textbox = scrolledtext.ScrolledText(root, wrap=tk.WORD, width=60, height=30, state="disabled")
    claude_textbox.grid(row=1, column=0, padx=10, pady=5)

    chatgpt_textbox = scrolledtext.ScrolledText(root, wrap=tk.WORD, width=60, height=30, state="disabled")
    chatgpt_textbox.grid(row=1, column=1, padx=10, pady=5)

    # 監視スレッド起動
    thread = Thread(target=update_loop, args=(claude_textbox, chatgpt_textbox), daemon=True)
    thread.start()

    root.mainloop()

if __name__ == "__main__":
    launch_gui()
