# Motivator Orchestrator Playground

小規模オーケストレーター（統括マネージャー「統括M」）と複数の専門AIが協働するデモアプリです。バックエンドは FastAPI、フロントはバニラJS/CSS で実装しています。

## 特徴
- 左右2カラムUI
	- 左: あなた ↔ 統括M（メイン）
	- 右: 統括M ↔ 専門スタッフ（相談スレッド）
- おすすめAI（日本語表示）とフェーズプリセット
- 相談スレッドは自己紹介なしで要点から返答
- フォローアップはロール別・ターン別に変化
- 統括Mは相談結果をメインに要約（固定の定型文は無し）
- 「多くの担当者」などの依頼を検知すると右側で多人数ディスカッション
- AIロールのカスタム作成/編集（性格/文体/口癖/専門領域）と自動アイコン（色+絵文字）

## ディレクトリ構成
- `backend/` FastAPIアプリ（静的ファイル提供含む）
- `frontend/` HTML/CSS/JS
- `run.ps1` Windows向け起動スクリプト（ポート自動回避、ブラウザ自動起動）

## 起動（Windows PowerShell）
- `run.ps1` を実行（初回は仮想環境と依存を自動セットアップ、指定ポートが使用中なら自動で次ポート）

## 主なAPI
- `POST /api/init` 会話開始（会話ID発行）
- `POST /api/message` メッセージ送信（右側で専門家相談→左側へ統括M要約）
- `GET  /api/feed?since=<id>` イベントの増分取得
- `GET  /api/recommend_v2` おすすめロール一覧（日本語表示用）
- `GET  /api/presets` フェーズプリセット一覧
- `POST /api/add-agent` / `POST /api/add-agents` ロールの相談参加
- 役割管理
	- `POST /api/roles` 新規ロール作成（title, persona, tone, catchphrase, domain）
	- `PUT  /api/roles/{role_id}` 既存ロール更新（system_promptとアイコン自動再生成）

## 設定
- `.env`（任意）
	- `OPENAI_API_KEY` / `OPENAI_MODEL`（OpenAI利用時）
	- `AI_ROLES_DIR` 追加ロール定義（JSON/YAML）
	- `SELECT_LIMIT`（既定3, 最大8） `FOLLOWUP_TURNS`（1..3）

## 既知の注意
- カスタムロールはメモリ保持。再起動で消えます（永続化は今後の拡張）
- 右カラムの相談は要点優先・自己紹介省略を徹底

## ライセンス
本リポジトリ内のコードはサンプル用途です。
# 統括マネージャーとの会話（Playground）

このアプリは「統括マネージャー（motivator_ai）」がハブとなり、必要に応じて複数の専門職AI（IdeaAI / WriterAI / ProofAI / PM-AI）に相談し、ユーザーの議題を前に進めるための会話型プレイグラウンドです。

- 統括マネージャー: 進行役。議題を問い返しつつ、必要なときに専門職に意見を求めたり、参加させます。
- 専門職AI: それぞれの観点で短い助言を返します。必要に応じて複数名が並行して回答。
- UI: 1つの会話パネルの中で「メイン（統括M）」と「相談（各専門職）」のレーンに分けてメッセージを表示します。

設計メモ
- 強制的な「要点まとめ」「次の一歩」は出力しません（会話の自然さを優先）。
- 役割カードは `backend/roles.json` と、任意の `AI_ROLES_DIR` 配下の JSON から読み込み、id 重複は後勝ちでマージします。
- 起動: `run.ps1` を実行し http://localhost:8083 を開きます。

環境変数（.env）
- OPENAI_API_KEY: OpenAI を使う場合に設定。無設定時はモック応答になります。
- OPENAI_MODEL: 既定 gpt-4o-mini
- FOLLOWUP_TURNS: 1..8（既定 5）
- SELECT_LIMIT: 1..8（既定 3）
- AI_ROLES_DIR: 追加の役割カード JSON を置くフォルダのパス。
 - SERPAPI_API_KEY: SerpAPI でWeb検索を行う場合に設定（任意）
 - BING_SEARCH_API_KEY / BING_SEARCH_ENDPOINT: Bing Web Search API を使う場合に設定（任意、例: https://api.bing.microsoft.com/v7.0/search）

研究系の問いでURLが無い場合でも、上記の検索APIキーが設定されていれば「検索 → 候補URLのクロール（本文抽出） → 相談担当への抜粋添付 → 統括のとりまとめ」まで自動で行います。
