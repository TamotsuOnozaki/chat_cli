import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import os

FILE_NAME = "output_claude_idea.txt"
FOLDER = os.path.dirname(os.path.abspath(__file__))

class ClaudeFileHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if event.src_path.endswith(FILE_NAME):
            with open(os.path.join(FOLDER, FILE_NAME), "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    print(f"[Idea Claude Output Detected]\n{content}\n")

if __name__ == "__main__":
    path = FOLDER
    print(f"Watching {FILE_NAME} for changes...")
    event_handler = ClaudeFileHandler()
    observer = Observer()
    observer.schedule(event_handler, path=path, recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
