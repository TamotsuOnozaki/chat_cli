@echo off
cd /d %~dp0

REM Claude 出力監視を起動
start "" python watch_claude_output.py

REM ChatGPT Claude Viewer を起動
start "" python chatgpt_claude_viewer.py

REM Claude → ChatGPT連携ブリッジを起動
start "" python multi_bridge_claude_chatgpt_gui_api_v2_envload.py

pause
