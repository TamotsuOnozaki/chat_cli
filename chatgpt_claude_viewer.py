import tkinter as tk
from tkinter import ttk, scrolledtext
import os
import time
from threading import Thread

# Claude出力ファイルとChatGPTログのマッピング
FILES = {
    "writer": "output_claude_writer.txt",
    "idea": "output_claude_idea.txt",
    "proof": "output_claude_proof.txt"
}
LOG_FILE = "response_log.txt"

def read_file(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except:
        return f"❌ {file_path} が見つかりません。"

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

def update_loop(textboxes):
    last_contents = {k: "" for k in FILES}
    last_gpt = ""
    while True:
        for name, file_path in FILES.items():
            content = read_file(file_path)
            if content != last_contents[name]:
                textbox = textboxes[name]
                textbox.config(state="normal")
                textbox.delete("1.0", tk.END)
                textbox.insert(tk.END, content)
                textbox.config(state="disabled")
                last_contents[name] = content
        gpt = read_latest_chatgpt()
        if gpt != last_gpt:
            textbox = textboxes["gpt"]
            textbox.config(state="normal")
            textbox.delete("1.0", tk.END)
            textbox.insert(tk.END, gpt)
            textbox.config(state="disabled")
            last_gpt = gpt
        time.sleep(2)

def launch_gui():
    root = tk.Tk()
    root.title("Claude & ChatGPT Viewer")

    notebook = ttk.Notebook(root)
    notebook.pack(expand=True, fill='both')

    textboxes = {}

    for name, file in FILES.items():
        frame = ttk.Frame(notebook)
        notebook.add(frame, text=f"Claude：{name}")
        textbox = scrolledtext.ScrolledText(frame, wrap=tk.WORD, width=80, height=30, state="disabled")
        textbox.pack(padx=10, pady=10, expand=True, fill='both')
        textboxes[name] = textbox

    gpt_frame = ttk.Frame(notebook)
    notebook.add(gpt_frame, text="ChatGPT応答")
    gpt_box = scrolledtext.ScrolledText(gpt_frame, wrap=tk.WORD, width=80, height=30, state="disabled")
    gpt_box.pack(padx=10, pady=10, expand=True, fill='both')
    textboxes["gpt"] = gpt_box

    thread = Thread(target=update_loop, args=(textboxes,), daemon=True)
    thread.start()

    root.mainloop()

if __name__ == "__main__":
    launch_gui()
