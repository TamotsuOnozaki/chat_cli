@echo off
cd /d %~dp0

REM ========== Claude 出力監視 ==========
start "" python watch_claude_output_writer.py
start "" python watch_claude_output_idea.py
start "" python watch_claude_output_proof.py

REM ========== Claude → ChatGPT ブリッジ ==========
start "" python multi_bridge_claude_chatgpt_gui_api_v2_envload.py --ai writer
start "" python multi_bridge_claude_chatgpt_gui_api_v2_envload.py --ai idea
start "" python multi_bridge_claude_chatgpt_gui_api_v2_envload.py --ai proof
start "" python watch_chatgpt_to_claude.py


REM ========== GUI ビューア（統合ビュー対応） ==========
start "" python chatgpt_claude_viewer.py

pause
