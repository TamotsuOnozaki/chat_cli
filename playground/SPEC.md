# 仕様まとめ

## 概要
- 統括マネージャー（統括M）がユーザーの依頼を受け、右カラムで専門AIに相談→左カラムへ要約を返す。
- バックエンドは FastAPI。フロントはバニラHTML/CSS/JS。

## UI
- 2カラム:
  - 左: あなた ↔ 統括M のメインタイムライン
  - 右: 統括M ↔ 各専門スタッフの相談スレッド（役割ごとに分割）
- おすすめAIモーダル:
  - ホワイトリストの既定ロール + カスタムロール（cust_*）を表示
  - 各カード: アイコン（色+絵文字）/ 日本語表示名 / 説明 / [参加] / [設定]
  - 新規AI専門スタッフの作成フォーム
- 日本語ラベル表示（例: プロダクト企画/プロジェクト進行/アーキテクト/開発エンジニア/企画アドバイザー/統括M/あなた）

## 会話フロー
1) User → /api/message に投稿
2) 統括M（main）: 「確認→専門家に相談します」短いアナウンス
3) 右カラム相談（consult:<role>):
   - 役割ごとに1〜FOLLOWUP_TURNSターン
   - 返答は自己紹介・挨拶を排除し、要点先出しの自然文（必要に応じ箇条書き）
   - フォローアップは直前応答を参照し、ロール別・ターン別の多様化
4) 統括M（main）: 相談結果の要約を返す（固定の定型文なし）

## 専門家選出
- 文章キーワードとロールのマッピングで選出
- 「多くの担当者/多人数/たくさんの意見/幅広く/多数のAI/多方面」などを含むと上限を最大8まで拡張
- カスタムロール（cust_*）も候補に含める

## 役割管理
- 既定ロール: roles.json + AI_ROLES_DIR（JSON/YAML, id重複は後勝ち）
- ランタイムカスタム:
  - POST /api/roles: title/persona/tone/catchphrase/domain から system_prompt を自動生成
  - PUT  /api/roles/{id}: 上記を更新、system_prompt再生成
  - 自動アイコン生成: 性格/文体/タイトルから色(HSL)と絵文字を推定

## API
- POST /api/init
- POST /api/message
- GET  /api/feed?since=<id>
- GET  /api/recommend_v2／GET /api/recommend
- GET  /api/presets
- POST /api/add-agent, POST /api/add-agents
- 役割管理: POST /api/roles, PUT /api/roles/{role_id}

## 設定項目
- OPENAI_API_KEY, OPENAI_MODEL
- AI_ROLES_DIR: 追加ロールの外部定義ルート
- FOLLOWUP_TURNS: 1..3（既定2）
- SELECT_LIMIT: 既定3, 多人数検出で最大8

## 非機能
- CORS許可
- 静的ファイル配信
- Windows起動スクリプト（run.ps1）: venv自動・pip更新、ポート自動回避、ブラウザ自動起動

## 既知の制約
- カスタムロールはメモリ保持（再起動でリセット）
- OpenAI未設定時はモック応答
