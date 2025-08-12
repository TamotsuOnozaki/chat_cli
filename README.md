# chat_cli

## 使い方
- 監視起動: `python .\\watch_claude_output.py`
- オーケストレーター単発: `python .\\multi_agent_orchestrator.py -i "…" -r idea_ai,writer_ai,proof_ai`
- [ORCH] トリガー: `output_claude_writer.txt` に `[ORCH roles=idea_ai,writer_ai,proof_ai] …` と保存

## 設定
- `.env`（例は `.env.example`）
- 役割カード: `ai_roles/cards/cards.sample.json`
- ログ: `logs/orch_YYYY-MM-DD.jsonl`
