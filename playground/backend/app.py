import os, json, uuid, glob, re
# NOTE: ランタイムは .venv に依存パッケージがインストール済み（run.ps1 が pip install 済み）。
# VS Code 上の未解決インポートは .vscode/settings.json の python.defaultInterpreterPath 設定で解消します。
import requests
from bs4 import BeautifulSoup  # type: ignore
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Iterable
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
try:
    import yaml  # type: ignore
except Exception:  # 依存が無い場合でも起動は継続（JSONだけ読み込む）
    yaml = None  # type: ignore

# .env を確実に読み込む（playground/.env と リポジトリ直下の .env の両方を試行）
def _load_dotenv_multi() -> None:
    try:
        # 1) 現在の作業ディレクトリから上位へ探索
        load_dotenv(override=False)
    except Exception:
        pass
    try:
        # 2) playground/.env（backend の親）
        p1 = (Path(__file__).resolve().parent.parent / ".env")
        if p1.exists():
            load_dotenv(p1, override=False)
    except Exception:
        pass
    try:
        # 3) リポジトリ直下 .env（backend の親の親の親）
        p2 = (Path(__file__).resolve().parents[2] / ".env")
        if p2.exists():
            load_dotenv(p2, override=False)
    except Exception:
        pass

_load_dotenv_multi()
app = FastAPI(title="Motivator Orchestrator Playground")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def _as_roles(obj: Any) -> List[Dict[str, Any]]:
    # 入力が配列 or 単一オブジェクトの両方を許容
    if obj is None:
        return []
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        # 典型的に {"roles":[...]} という形も受ける
        if "roles" in obj and isinstance(obj["roles"], list):
            return [x for x in obj["roles"] if isinstance(x, dict)]
        return [obj]
    return []

def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def _load_yaml(path: Path) -> Any:
    if yaml is None:
        raise RuntimeError("PyYAML is not installed")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def load_roles() -> List[Dict[str, Any]]:
    base_path = Path(__file__).parent / "roles.json"
    roles: List[Dict[str, Any]] = []
    try:
        roles.extend(_as_roles(_load_json(base_path)))
    except Exception as e:
        print(f"[roles] base load failed: {e}")

    # 追加: AI_ROLES_DIR 以下の *.json/*.yaml/*.yml をすべて読む（サブディレクトリ含む）
    extra_dir = os.getenv("AI_ROLES_DIR")
    if extra_dir:
        p = Path(extra_dir)
        if p.exists():
            patterns = ["*.json", "*.yaml", "*.yml"]
            for pat in patterns:
                for jp in p.rglob(pat):
                    try:
                        if jp.suffix.lower() == ".json":
                            roles.extend(_as_roles(_load_json(jp)))
                        else:
                            roles.extend(_as_roles(_load_yaml(jp)))
                    except Exception as e:
                        print(f"[roles] skip {jp}: {e}")
        else:
            print(f"[roles] AI_ROLES_DIR not found: {p}")

    # 追加: 同ディレクトリの roles_custom.json も（存在すれば）読み込む
    try:
        custom_path = Path(__file__).parent / "roles_custom.json"
        if custom_path.exists():
            roles.extend(_as_roles(_load_json(custom_path)))
    except Exception as e:
        print(f"[roles] custom load failed: {e}")

    # id で重複排除（後勝ち）
    dedup: Dict[str, Dict[str, Any]] = {}
    for r in roles:
        rid = r.get("id")
        if not rid:
            continue
        dedup[rid] = r

    merged = list(dedup.values())
    if not merged:
        raise RuntimeError("no roles loaded")
    return merged

ROLES: List[Dict[str, Any]] = load_roles()
ROLES_BY_ID: Dict[str, Dict[str, Any]] = {r["id"]: r for r in ROLES}
# 起動後に追加されるカスタムロール（メモリ保持）
CUSTOM_ROLES: Dict[str, Dict[str, Any]] = {}
CUSTOM_ROLES_PATH = Path(__file__).parent / "roles_custom.json"

def _load_custom_roles() -> None:
    """roles_custom.json からカスタムロールを読み込み、グローバルに反映。
    読み込み失敗時は黙ってスキップ（初回未作成など）。"""
    global CUSTOM_ROLES, ROLES_BY_ID
    try:
        if CUSTOM_ROLES_PATH.exists():
            data = _load_json(CUSTOM_ROLES_PATH)
            roles = _as_roles(data)
            for r in roles:
                rid = r.get("id")
                if not rid:
                    continue
                CUSTOM_ROLES[rid] = r
                ROLES_BY_ID[rid] = r
    except Exception as e:
        print(f"[roles_custom] load failed: {e}")

def _save_custom_roles() -> None:
    """メモリ上の CUSTOM_ROLES を roles_custom.json へ保存。"""
    try:
        payload = {"roles": list(CUSTOM_ROLES.values())}
        with CUSTOM_ROLES_PATH.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[roles_custom] save failed: {e}")

# 起動時に永続カスタムロールを取り込み
_load_custom_roles()

EVENTS: List[Dict[str, Any]] = []
NEXT_EID = 1
# 会話ごとの参加メンバー（追加順を保持）
CONV_MEMBERS: Dict[str, List[str]] = {}
# 統括のみで対話するフラグ（会話単位）
ORCH_ONLY: Dict[str, bool] = {}
# 統括のメイン応答履歴（重複回避用・直近数件のみ保持）
ACK_HISTORY: Dict[str, List[str]] = {}
# 使用したテンプレートキーの履歴（重複パターン回避用）
ACK_KEY_HISTORY: Dict[str, List[str]] = {}
# レーンごとの直近発言履歴（統括Mのみ、重複監視に使用）
LANE_HISTORY: Dict[str, List[str]] = {}

def now_iso(): return datetime.utcnow().isoformat()+"Z"

def push_event(conv_id: str, role: str, text: str, lane: str = "main"):
    global NEXT_EID
    ev = {"id": NEXT_EID, "conv_id": conv_id, "role": role, "text": text, "ts": now_iso(), "lane": lane}
    EVENTS.append(ev); NEXT_EID += 1; return ev

def _lane_key(conv_id: str, lane: str) -> str:
    return f"{conv_id}|{lane}"

def _remember_lane(conv_id: str, lane: str, text: str) -> None:
    key = _lane_key(conv_id, lane)
    hist = LANE_HISTORY.setdefault(key, [])
    t = (text or "").strip()
    if not t:
        return
    hist.append(t)
    if len(hist) > 8:
        del hist[:-8]

def _recent_lane_texts(conv_id: str, lane: str) -> List[str]:
    return list(LANE_HISTORY.get(_lane_key(conv_id, lane), []) or [])

def _members_of(conv_id: str) -> List[str]:
    return CONV_MEMBERS.get(conv_id, [])

def _add_member(conv_id: str, role_id: str) -> None:
    if not role_id:
        return
    lst = CONV_MEMBERS.setdefault(conv_id, [])
    if role_id not in lst:
        lst.append(role_id)

class InitResponse(BaseModel):
    conversation_id: str
    events: List[Dict[str, Any]]
class MessageRequest(BaseModel):
    conversation_id: str
    text: str
class FeedResponse(BaseModel):
    events: List[Dict[str, Any]]

# 簡易キーワード→役割のマップ（複数一致を許可）
KEYMAP = [
    ("pm_ai", ["計画","優先","依存","スケジュール","タスク","段取り","ロードマップ","プロダクト"]),
    ("product_manager_ai", ["価値","顧客","要件","KPI","ロードマップ","プロダクト"]),
    ("project_manager_ai", ["進捗","WBS","リスク","スケジュール","担当","遅延","課題"]),
    ("architect_ai", ["設計","アーキテクチャ","技術選定","非機能","スケーラビリティ","セキュリティ"]),
    ("dev_ai", ["実装","コード","バグ","API","フロント","バックエンド"]),
    ("writer_ai", ["文章","書いて","構成","見出し","本文"]),
    ("proof_ai", ["校正","誤字","表記ゆれ","推敲","レビュー"]),
    ("idea_ai", ["企画","アイデア","案","発想","ブレスト"]),
    # 市場調査系キーワードでリサーチャー（カスタム）を自動選定
    ("cust_25895571", ["市場","市場調査","リサーチ","調査","競合","市場規模","統計","公開資料","一次情報","デスクトップリサーチ"]),
]
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL","gpt-4o-mini")
# 他プロバイダの環境変数（.env の別名キーも吸収）
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-pro")
# Web検索APIキー（任意）
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")
BING_SEARCH_API_KEY = os.getenv("BING_SEARCH_API_KEY")
BING_SEARCH_ENDPOINT = os.getenv("BING_SEARCH_ENDPOINT", "https://api.bing.microsoft.com/v7.0/search")
# 司会（統括M）が深掘りする往復数（既定5）
FOLLOWUP_TURNS_DEFAULT = max(1, min(8, int(os.getenv("FOLLOWUP_TURNS", "5"))))  # 1..8
SELECT_LIMIT = max(1, min(8, int(os.getenv("SELECT_LIMIT", "3"))))       # 1..8

# 依存関係（追加時の警告用）
DEPENDENCIES: Dict[str, List[str]] = {
    "dev_ai": ["architect_ai"],
}

# フェーズ別の専門職プリセット（必要に応じて拡張可）
PRESETS = [
    {"id":"phase_core","title":"フェーズ1: コアチーム","description":"PM/アーキテクト/実装の最小体制","roles":["product_manager_ai","project_manager_ai","architect_ai","dev_ai","pm_ai"]},
    {"id":"phase_idea","title":"企画フェーズ","description":"発想と方向付け","roles":["idea_ai","pm_ai"]},
    {"id":"phase_write","title":"執筆フェーズ","description":"構成→執筆→軽いチェック","roles":["writer_ai","proof_ai","pm_ai"]},
    {"id":"phase_finish","title":"仕上げフェーズ","description":"最終チェックと磨き込み","roles":["proof_ai","writer_ai"]},
]

# 日本語ラベル（UIと整合）
ROLE_LABEL_JA: Dict[str, str] = {
    "idea_ai": "企画アドバイザー",
    "writer_ai": "ライターAI",
    "proof_ai": "校正AI",
    "pm_ai": "全体進行（PM補助）",
    "product_manager_ai": "プロダクト企画",
    "project_manager_ai": "プロジェクト進行",
    "architect_ai": "アーキテクト",
    "dev_ai": "開発エンジニア",
    "motivator_ai": "統括M",
    # custom roles (business side)
    "cust_bce7cc85": "CFO 財務責任者",
    "cust_biz_dev_manager": "ビジネス開発マネージャー",
    "cust_sales_marketing": "営業・マーケティング担当",
    "cust_business_analyst": "ビジネスアナリスト",
    "cust_market_research": "市場調査アナリスト",
    "cust_competitive_analyst": "競合分析スペシャリスト",
    "cust_financial_analyst": "財務アナリスト",
    "cust_uiux_designer": "UI/UXデザイナー",
    "cust_legal_compliance": "法務・コンプライアンス担当",
    "cust_tech_lead": "技術リーダー/ソフトウェアアーキテクト",
}

# おすすめに表示する既定の順序
# 先頭は多角的な事業提案の起点となる3名（財務/企画/プロダクト）
RECOMMEND_IDS = [
    # 事業構想〜計画の多角起点
    "cust_bce7cc85",           # CFO 財務責任者（カスタム）
    "cust_biz_dev_manager",    # ビジネス開発
    "cust_sales_marketing",    # 営業/マーケ
    "cust_business_analyst",   # ビジネスアナリスト
    "cust_market_research",    # 市場調査
    "cust_competitive_analyst",# 競合分析
    "cust_financial_analyst",  # 財務アナリスト
    "cust_uiux_designer",      # UI/UX
    "cust_legal_compliance",   # 法務/コンプラ
    "cust_tech_lead",          # 技術リード/アーキ
    # 既存の基本ロール
    "idea_ai",
    "product_manager_ai",
    "project_manager_ai",
    "architect_ai",
    "dev_ai",
    "pm_ai",
]

# 自動ブロードキャストから除外するロール（明示言及がある場合は参加可）
EXCLUDE_AUTO_ROLES = {
    "writer_ai",
    "proof_ai",
    "cust_25895571",
}

# ---- 統括（オーケストレーター）設定 ----
# runtimeで上書き可能な設定。roles_custom.json の統括ロールとは別に、
# 会話運用（開幕メッセージ/相槌/追質問回数/要約スタイル）を制御する。
ORCHSET_PATH = Path(__file__).parent / "orchestrator_settings.json"
ORCHSET: Dict[str, Any] = {
    "opening_message": "統括Mです。今日はどんな議題について進めますか？",
    "followup_turns": FOLLOWUP_TURNS_DEFAULT,
    # カテゴリ別の相槌テンプレート（任意）: research/plan/tech/gtm/general
    "acks": {},
    # default | short | none
    "summary_style": "default",
}

def _load_orchset() -> None:
    global ORCHSET
    try:
        if ORCHSET_PATH.exists():
            with ORCHSET_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    ORCHSET.update(data)
        # 型・範囲の軽い正規化
        ft_raw = ORCHSET.get("followup_turns", FOLLOWUP_TURNS_DEFAULT)
        if isinstance(ft_raw, int):
            ft_norm = ft_raw
        elif isinstance(ft_raw, str):
            try:
                ft_norm = int(ft_raw)
            except Exception:
                ft_norm = FOLLOWUP_TURNS_DEFAULT
        else:
            ft_norm = FOLLOWUP_TURNS_DEFAULT
        ORCHSET["followup_turns"] = max(1, min(8, int(ft_norm)))
        if ORCHSET.get("summary_style") not in ("default","short","none"):
            ORCHSET["summary_style"] = "default"
        if not isinstance(ORCHSET.get("acks"), dict):
            ORCHSET["acks"] = {}
    except Exception as e:
        print(f"[orchset] load failed: {e}")

def _save_orchset() -> None:
    try:
        with ORCHSET_PATH.open("w", encoding="utf-8") as f:
            json.dump(ORCHSET, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[orchset] save failed: {e}")

def get_followup_turns() -> int:
    try:
        return int(ORCHSET.get("followup_turns", FOLLOWUP_TURNS_DEFAULT))
    except Exception:
        return FOLLOWUP_TURNS_DEFAULT

def get_opening_message() -> str:
    msg = str(ORCHSET.get("opening_message") or "").strip()
    return msg or "統括Mです。今日はどんな議題について進めますか？"

# 起動時にロード
_load_orchset()

# ---- 会話の柔軟化（文脈に応じた挨拶/次アクション提案） ----

def _classify_topic(text: str) -> str:
    low = (text or "").lower()
    ja = text or ""
    if any(k in ja for k in ["市場","調査","競合","規模","統計","公開資料","リサーチ"]):
        return "research"
    if any(k in ja for k in ["wbs","スケジュール","工程","段取り","進捗","担当","体制"]):
        return "plan"
    if any(k in ja for k in ["設計","技術選定","アーキテクチャ","非機能","セキュリティ","スケーラビリティ"]):
        return "tech"
    # GTM（広告/集客も含む）
    if any(k in ja for k in ["kpi","効果測定","訴求","メッセージ","チャネル","価格","収益","広告","集客","リード","キャンペーン","LP","ランディング","CV","CVR","CPA"]):
        return "gtm"
    return "general"

ACK_TEMPLATES: Dict[str, List[str]] = {
    "research": [
        "『{head}』の当たりを付けるため、まず根拠の断片を1つ拾います（公開統計/競合事例/一次情報のどれか）。",
        "まずは『{head}』について、信頼できる出典を1つ押さえます。どの軸から確認しますか（市場規模/競合/一次情報）？",
        "『{head}』を絞り込むために、最初の参照点を1つ決めましょう。市場データ/競合/現場ヒアリングのどれにしますか？",
    ],
    "plan": [
        "『{head}』を進めるうえで、最初の一歩（誰が・何を・どこまで）を1行で置きます。",
        "段取りから入ります。『{head}』の初手を短く決めましょう（担当/期限/成果物）。",
        "『{head}』は粗いWBSを先に。最初のタスクを1つだけ固定しましょう。",
    ],
    "tech": [
        "『{head}』は構成か比較軸のどちらかを先に固めます。どちらを先に見ますか？",
        "技術面では『{head}』について、評価軸（性能/保守/コスト）を1つ選んで当たりを付けます。",
        "『{head}』の非機能（SLO/セキュリティ）に先に触れておくのも良さそうです。",
    ],
    "gtm": [
        "『{head}』の届け先を具体にし、打ち手を1点に絞りましょう。ターゲット/メッセージ/チャネル/価格のどれから？",
        "まずはターゲット像を1文で固定して『{head}』の打ち手を選びます。",
        "『{head}』のKPIを1つ先に置き、打ち手を選ぶ順にします。",
    ],
    "general": [
        "今の要点は『{head}』ですね。先に優先条件を1つだけ決めましょう。",
        "焦点は『{head}』。最初に譲れない条件を1つだけ共有してください。",
        "話題は『{head}』。次の一手を決めやすくするため、基準を1点だけ置きましょう。",
    ],
}

def _remember_ack(conv_id: str, text: str) -> None:
    hist = ACK_HISTORY.setdefault(conv_id, [])
    hist.append((text or "").strip())
    # 直近5件だけ保持
    if len(hist) > 5:
        del hist[:-5]

def _remember_key(conv_id: str, key: str) -> None:
    hist = ACK_KEY_HISTORY.setdefault(conv_id, [])
    hist.append(key)
    if len(hist) > 8:
        del hist[:-8]

def _last_ack(conv_id: str) -> str:
    hist = ACK_HISTORY.get(conv_id) or []
    return hist[-1] if hist else ""

def _recent_keys(conv_id: str, prefix: str) -> set[str]:
    keys = ACK_KEY_HISTORY.get(conv_id) or []
    return {k for k in keys if k.startswith(prefix)}

def _ack_for_conv(conv_id: str, text: str) -> str:
    """
    定型句を避けつつ、カテゴリ別テンプレートからバリエーション選択。
    同一会話での直近重複を避ける。
    ORCHSET.acks に上書きがあれば優先（空文字は無視）。
    """
    # グローバル抑制: 設定でサマリーを出さない場合や acks が空の場合は相槌を出さない
    try:
        if ORCHSET.get("summary_style") == "none" or not (ORCHSET.get("acks") or {}):
            return ""
    except Exception:
        pass
    # 質問メッセージの場合は相槌を出さず、統括が直接回答する
    t = (text or "").strip()
    if not t:
        return ""
    if any(mark in t for mark in ["?", "？"]) or any(k in t for k in ["教えて","どうやって","どうすれば","とは","というと","なぜ","理由","例","例えば"]):
        return ""
    cat = _classify_topic(text)
    # 設定の上書き（任意）
    try:
        acks = ORCHSET.get("acks", {}) or {}
        if isinstance(acks, dict):
            custom = acks.get(cat)
            if isinstance(custom, str) and custom.strip():
                cand = custom.strip()
                # 重複ならヘッドを添えて微変化
                if cand == _last_ack(conv_id):
                    head = (_extract_head(text) or (text or "").strip()[:40]).replace("\n", " ")
                    cand = f"{cand}（要点:『{head}』）"
                _remember_ack(conv_id, cand)
                return cand
    except Exception:
        pass
    head = (_extract_head(text) or (text or "").strip()[:40]).replace("\n", " ")
    tpl = ACK_TEMPLATES.get(cat, ACK_TEMPLATES["general"])[:]
    # インデックスはテキストの簡易ハッシュ＋履歴長で揺らす
    base = (sum(ord(c) for c in (text or "")) + len(ACK_HISTORY.get(conv_id, [])))
    used = _recent_keys(conv_id, f"ack:{cat}:")
    for i in range(len(tpl)):
        idx = (base + i) % len(tpl)
        key = f"ack:{cat}:{idx}"
        if key in used:
            continue
        cand = tpl[idx].format(head=head)
        if cand != _last_ack(conv_id):
            _remember_ack(conv_id, cand)
            _remember_key(conv_id, key)
            return cand
    # すべて衝突した場合のフォールバック
    cand = f"今の要点は『{head}』ですね。先に優先条件を1つだけ決めましょう。"
    if cand == _last_ack(conv_id):
        cand = f"『{head}』を進めるために、まず1点だけ基準を置きましょう。"
    _remember_ack(conv_id, cand)
    return cand

ORCH_MAIN_TEMPLATES: Dict[str, List[str]] = {
    "research": [
    "調べたいのは『{head}』ですね。まず見る軸を1つ決めましょう（市場規模/競合/一次情報）。",
    "『{head}』は最初の手掛かりを1つ押さえましょう。市場データ・競合・現場のどれから入りますか？",
    ],
    "plan": [
    "『{head}』を動かすなら、最初の一歩を決めましょう。誰が何をどこまで、を1行で。",
    "最初は小さく動きます。『{head}』の初手（担当/期限/成果物）を短く置いてみてください。",
    ],
    "tech": [
    "『{head}』は構成か比較軸のどちらから詰めますか？先に1つ決めましょう。",
        "技術面は『{head}』について、非機能（SLO/セキュリティ）か構成のどちらを先に。",
    ],
    "gtm": [
    "『{head}』の届け先を具体にし、打ち手を1つに絞りましょう。どこから入りますか（ターゲット/メッセージ/チャネル/価格）？",
        "まずは『{head}』の仮ターゲットを1文で置いて、対応する打ち手を選びましょう。",
    ],
    "general": [
    "今の話題は『{head}』ですね。いま重視したい基準はどれですか？（期間/コスト/既存活用 など）",
    "まず大事にしたい基準を1つ教えてください。例: 期間/コスト/既存活用。",
    ],
}

def _example_bullets(cat: str, head: str) -> list[str]:
    if cat == "research":
        return [
            "公開統計から規模感だけ先に押さえる（一次情報の当たりを付ける）",
            "競合3社の最近の打ち手をざっくり比較して差分を見る",
            "顧客インタビューの仮質問を5つだけ用意して現場に当たる",
        ]
    if cat == "plan":
        return [
            "2週間のスプリントでPoCを切る（担当/成果物/完了条件を1行で固定）",
            "役割を3つに絞って体制を仮置き（責任/権限/判断基準）",
            "重大リスクを1つ先に潰す短期タスクを先行させる",
        ]
    if cat == "tech":
        return [
            "構成A/Bの比較軸（性能/保守/コスト）を表で1枚にする",
            "SLOの最小値を1つだけ先に決めて選定を縛る",
            "スパイクで最難所の1点だけ検証してから設計を固める",
        ]
    if cat == "gtm":
        return [
            "既存顧客のリテンション改善（休止予兆に対する1施策）",
            "新規獲得の実験（1チャネル×1メッセージでAB）",
            "価格の見直し（1プランだけ追加/改定して様子を見る）",
        ]
    return [
        "コスト/効率の改善テーマを1つ（例: 業務の手戻り削減）",
        "売上/成長に直結するテーマを1つ（例: 既存客のアップセル）",
        "基盤/品質の底上げを1つ（例: 運用負荷の見える化）",
    ]

def _orchestrator_main_reply(conv_id: str, text: str) -> str:
    """統括のみモードの自然な応答（質問を最優先で直接回答／重複回避）。"""
    t = (text or "").strip()
    cat = _classify_topic(t)
    head = (_extract_head(t) or t[:60]).replace("\n", " ")

    # --- 先に“直接回答”を試みる -----------------------------------------
    # 1) 『〜というと？/とは？』等 → 用語の軽い定義
    if any(k in t for k in ["というと？","というと?","とは？","とは?","って何","ってなに","どういう意味"]):
        if any(k in t for k in ["優先","基準"]):
            cand = (
                "ここでの『優先条件』は、先に縛る基準を1つだけ決めることです。\n"
                "例: 期間/コスト/既存活用/リスク最小。どれを優先しますか？"
            )
        else:
            cand = (
                f"『{head}』の意味を短く合わせます。範囲/対象/期間/目的のどれか1つを先に固定すると話が進みます。どれを決めますか？"
            )
        _remember_ack(conv_id, cand)
        _remember_key(conv_id, f"orch:{cat}:define")
        return cand

    # 2) 『議題候補をNつ』『テーマをいくつか』等 → その場で列挙
    if any(k in t for k in ["議題候補","議題","テーマ","トピック","論点","アジェンダ"]) and any(k2 in t for k2 in ["挙げて","あげて","出して","教えて","リスト","一覧","ください"]):
        # 件数抽出（漢数字/アラビア数字）
        num_map = {"一":1,"１":1,"1":1,"二":2,"２":2,"2":2,"三":3,"３":3,"3":3,"四":4,"４":4,"4":4,"五":5,"５":5,"5":5,"六":6,"６":6,"6":6}
        n = 5
        for ch in t:
            if ch in num_map:
                n = num_map[ch]; break
        m = re.search(r"(\d{1,2})", t)
        if m:
            n = int(m.group(1))
        n = max(3, min(10, n))
        candidates = [
            "売上/成長: 既存顧客のアップセル実験（1メッセージ×1チャネルでAB）",
            "新規獲得: 広告キャンペーンの小規模テスト（Google/Metaどちらか×1週）",
            "LTV改善: 休眠直前ユーザーの再活性（1通だけのリマインド）",
            "効率/コスト: 手戻り多発業務のボトルネック解消（標準化/自動化の当たり）",
            "プロダクト: LP/画面の1点改善（最初の行動到達率アップ）",
            "基盤/品質: 可観測性の導入（主要KPIと異常検知だけ先に）",
            "リスク/法務: データ/権利の確認（利用規約/ライセンスの要点チェック）",
            "体制: 役割と判断基準の明確化（誰が何をどこまで）",
            "価格戦略: 1プランだけ追加/改定して様子を見る",
            "計測: イベント定義を1つ追加して穴を塞ぐ",
        ][:n]
        body = "\n- " + "\n- ".join(candidates)
        cand = f"候補を{len(candidates)}件並べます。{body}\nどれから手を付けますか？（番号でOK）"
        _remember_ack(conv_id, cand)
        _remember_key(conv_id, f"orch:{cat}:agenda:{len(candidates)}")
        return cand

    # 3) 直前に列挙した候補から『この中でどれ？』に直接答える
    if ("この中で" in t or "どれが" in t or "どれですか" in t or "どちら" in t) and ("?" in t or "？" in t):
        # 直近の統括メッセージから箇条書きを抽出
        bullets: list[str] = []
        for e in reversed(EVENTS):
            if e.get("conv_id") != conv_id:
                continue
            if e.get("lane") != "main" or e.get("role") != "motivator_ai":
                continue
            txt0 = (e.get("text") or "").strip()
            if "\n- " in txt0 or txt0.startswith("- "):
                for l in txt0.splitlines():
                    l = l.strip()
                    if l.startswith("- "):
                        bullets.append(l[2:].strip())
                if bullets:
                    break
        if bullets:
            def _score_ai(s: str) -> int:
                low = s.lower()
                score = 0
                for kw in ["AI","AI事業","生成AI","機械学習","自動化","モデル","推論","データ","計測","可観測","タグ","GA4","広告","キャンペーン","LP","CV","CVR","CPA"]:
                    if kw.lower() in low or kw in s:
                        score += 1
                return score
            if any(k in t for k in ["AI","AI事業","AI関連","生成AI","機械学習"]):
                best = max(bullets, key=_score_ai)
                reason = "AI/データ/自動化と結び付けやすく、効果検証までの距離が近いからです"
            else:
                best = bullets[0]
                reason = "小さく始めやすく、短期間で学びが得られるからです"
            cand = f"この中なら『{best}』を推します。理由: {reason}。次の一歩を決めましょう。"
            _remember_ack(conv_id, cand)
            _remember_key(conv_id, f"orch:{cat}:pick")
            return cand

    def _is_question(msg: str) -> bool:
        m = msg or ""
        return ("?" in m or "？" in m or any(k in m for k in ["教えて","どうやって","どうすれば","とは","というと","なぜ","理由","何ですか","とは？"]))

    def _is_example(msg: str) -> bool:
        return any(k in msg for k in ["例えば", "例", "サンプル", "具体例"]) and "例えばの例" not in msg

    def _is_list_request(msg: str) -> bool:
        return any(k in msg for k in ["議題","トピック","テーマ","候補","案"]) and any(n in msg for n in ["3つ","３つ","いくつか","複数","一覧","リスト"])

    def _is_define_request(msg: str) -> bool:
        return any(k in msg for k in ["とは","というと","って何","ってなに","どういう意味"]) or re.search(r"(何|なに)ですか[?？]?$", msg) is not None

    def _is_decision(msg: str) -> bool:
        return any(k in msg for k in ["ということです","それで行きます","それでお願いします","それにします","決めました","採用します"]) or msg.endswith("。")

    # 例示要求: 具体例で返す
    if _is_example(t):
        bullets = _example_bullets(cat, head)
        body = "\n- " + "\n- ".join(bullets)
        cand = f"例えばこう進められます。{body}\nどれが今の状況に近いですか？"
        if cand == _last_ack(conv_id):
            cand += "（完全一致でなくて構いません）"
        _remember_ack(conv_id, cand)
        _remember_key(conv_id, f"orch:{cat}:examples")
        return cand

    # 議題の列挙要求
    if _is_list_request(t) or any(k in t for k in ["どんな議題","どんなテーマ","何から","おすすめ","お勧め","テーマを"]):
        topics = _example_bullets("general", head)
        cand = "候補を3つ挙げます。\n- " + "\n- ".join(topics[:3]) + "\nまずは1つだけ選んで、短く理由を教えてください。"
        _remember_ack(conv_id, cand)
        _remember_key(conv_id, f"orch:{cat}:topics")
        return cand

    # 定義/意味の確認（例: 「優先というと？」）
    if _is_define_request(t):
        # 文中のキーワードに合わせた簡潔な定義を返す
        if any(k in t for k in ["優先","基準"]):
            cand = (
                "ここでの『優先条件』は、まず何を一番先に守るかを決める指針です。\n"
                "例: 期間（いつまでに）/ コスト（上限はいくら）/ 既存活用（今ある資産を使う）/ 品質（どの程度まで）\n"
                "この中から今は1つだけで十分です。どれにしますか？"
            )
        else:
            cand = (
                f"『{head}』の意味を短く整理します。用語の定義や前提が曖昧なら、まず1つだけ決めましょう。\n"
                "例: 範囲/対象/期間/目的 のいずれかを1行で固定。どれを先に決めますか？"
            )
        _remember_ack(conv_id, cand)
        _remember_key(conv_id, f"orch:{cat}:define")
        return cand

    # ユーザーが方針を確定・宣言（例: Google広告モデルにする）
    if _is_decision(t):
        plan_bullets: list[str] = []
        low = t.lower()
        if ("google" in low and "広告" in t) or ("google ads" in low):
            plan_bullets = [
                "目標: 1つだけ（CPA いくら/またはCV数）",
                "予算: まずは小額テスト（1〜2週間）",
                "LP/導線: 到達先を1つに絞る（計測タグも設置）",
                "計測: GA4/タグでCV計測を確認（テストで1件発火）",
                "運用: 1チャネル×1メッセージでAB、学びを記録",
            ]
        elif any(k in t for k in ["新規事業","新しい事業","新規の事業"]):
            plan_bullets = [
                "仮ターゲットを1文で固定",
                "提供価値の仮説を1文",
                "検証方法（誰に何をどう見せて測るか）を1つ",
                "2週間のPoCタスクを1本に分解",
            ]
        if plan_bullets:
            cand = "了解です。まずは小さく動きます。\n- " + "\n- ".join(plan_bullets) + "\n最初に決められるのはどれですか？"
            _remember_ack(conv_id, cand)
            _remember_key(conv_id, f"orch:{cat}:plan")
            return cand

    # ここまで該当しない場合は、LLM（統括ロール）に自由文で相談して返す
    try:
        ctx = _recent_main_context(conv_id, 6)
        user_text2 = t
        if ctx:
            user_text2 = t + "\n\n参考（直近のやり取り）:\n" + ctx
        # 利用可能ならカスタムの動的統括ロールを優先し、なければ既存の motivator_ai を使う
        dyn_id = "motivator_ai_dynamic" if "motivator_ai_dynamic" in ROLES_BY_ID else "motivator_ai"
        resp = (consult(dyn_id, user_text2) or "").strip()
        # モック応答（API未設定など）の場合はテンプレートにフォールバック
        if resp and not resp.startswith("（モック"):
            return resp
    except Exception:
        pass

    # フォールバック: 既存の軽い支援テンプレート（重複を避けつつ）
    tpl = ORCH_MAIN_TEMPLATES.get(cat, ORCH_MAIN_TEMPLATES["general"])[:]
    base = (sum(ord(c) for c in t) + len(ACK_HISTORY.get(conv_id, [])))
    used = _recent_keys(conv_id, f"orch:{cat}:")
    for i in range(len(tpl)):
        idx = (base + i) % len(tpl)
        key = f"orch:{cat}:{idx}"
        if key in used:
            continue
        cand = tpl[idx].format(head=head)
        if cand != _last_ack(conv_id):
            _remember_ack(conv_id, cand)
            _remember_key(conv_id, key)
            return cand
    cand = f"『{head}』について、次の一手を一緒に決めましょう。今は1点だけで十分です。"
    if cand == _last_ack(conv_id):
        cand = f"『{head}』を前に進めるため、まず1点だけ決めましょう。"
    _remember_ack(conv_id, cand)
    _remember_key(conv_id, f"orch:{cat}:fallback")
    return cand

def _next_action_prompt(user_text: str) -> str:
    cat = _classify_topic(user_text)
    if cat == "research":
        opts = [
            "一次情報の追加収集（業界団体/統計/IR）",
            "競合ベンチマーク（3社比較）",
            "市場規模の粗い試算",
            "仮説リスト化と検証計画",
            "調査結果の社内共有ドラフト",
        ]
    elif cat == "plan":
        opts = [
            "WBSの素案づくり",
            "役割/体制の割り付け",
            "スケジュール/マイルストーン設定",
            "依存関係とリスクの洗い出し",
            "着手タスクの優先度付け",
        ]
    elif cat == "tech":
        opts = [
            "設計詳細の詰め（構成/データ/インフラ）",
            "技術選定の比較表",
            "非機能要件の合意（SLO/セキュリティ）",
            "PoC/スパイクの実施",
            "概算見積りの作成",
        ]
    elif cat == "gtm":
        opts = [
            "ターゲット/ペルソナの明確化",
            "提供価値/メッセージの磨き込み",
            "チャネル/施策の当たり",
            "価格/収益モデルの仮置き",
            "効果測定(KPI)の定義",
        ]
    else:
        opts = [
            "実行計画の詳細化（WBS/役割/スケジュール）",
            "KPI/効果測定の定義",
            "リスクの洗い出しと対策",
            "代替案の比較",
            "関係者共有/稟議の下書き",
        ]
    body = "\n".join([f"- {x}" for x in opts])
    return (
        "了解しました。今回はここで区切りましょう。次に進むために優先したいことを教えてください。\n"
        + body + "\n（短く指定してください。例: 『KPI定義』や『実行計画』）"
    )

# ---- シンプルなWebクロール（URL抽出→本文抽出→要約用コンテキスト） ----

URL_RE = re.compile(r"https?://[^\s)]+", re.I)

def _extract_urls(text: str) -> list[str]:
    return list(dict.fromkeys(URL_RE.findall(text or "")))[:5]

def _fetch_url_text(url: str, timeout: int = 10) -> dict:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        html = r.text
        soup = BeautifulSoup(html, "html.parser")
        # title と本文テキスト
        title = (soup.title.string.strip() if soup.title and soup.title.string else url)
        # 不要なscript/styleを除去
        for tag in soup(["script","style","noscript"]):
            tag.decompose()
        text = soup.get_text("\n")
        # 正規化とトリム
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"\s{2,}", " ", text)
        text = text.strip()
        return {"url": url, "title": title, "text": text[:8000]}
    except Exception as e:
        return {"url": url, "title": url, "text": f"[fetch error] {e}"}

# ---- 外部検索連携（SerpAPI / Bing Web Search、利用可能な方を使用） ----

def _web_search_serpapi(query: str, num: int = 5) -> list[dict]:
    if not SERPAPI_API_KEY:
        return []
    try:
        params = {
            "engine": "google",
            "q": query,
            "api_key": SERPAPI_API_KEY,
            "num": max(1, min(10, num)),
            "hl": "ja",
            "gl": "jp",
        }
        r = requests.get("https://serpapi.com/search.json", params=params, timeout=12)
        r.raise_for_status()
        data = r.json()
        results = []
        for item in (data.get("organic_results") or [])[:num]:
            link = item.get("link") or item.get("url")
            title = item.get("title")
            if not link or not title:
                continue
            results.append({"title": title, "url": link})
        return results
    except Exception:
        return []

def _web_search_bing(query: str, num: int = 5) -> list[dict]:
    if not (BING_SEARCH_API_KEY and BING_SEARCH_ENDPOINT):
        return []
    try:
        headers = {"Ocp-Apim-Subscription-Key": BING_SEARCH_API_KEY}
        params = {"q": query, "count": max(1, min(10, num)), "mkt": "ja-JP"}
        r = requests.get(BING_SEARCH_ENDPOINT, headers=headers, params=params, timeout=12)
        r.raise_for_status()
        data = r.json()
        web = ((data.get("webPages") or {}).get("value") or [])
        results = []
        for item in web[:num]:
            link = item.get("url")
            title = item.get("name")
            if not link or not title:
                continue
            results.append({"title": title, "url": link})
        return results
    except Exception:
        return []

def web_search(query: str, num: int = 5) -> list[dict]:
    # 優先度: SerpAPI → Bing → 空
    res = _web_search_serpapi(query, num)
    if res:
        return res
    res = _web_search_bing(query, num)
    return res or []

def _gen_icon_from_style(style: Dict[str, Any], title: str) -> Dict[str, Any]:
    # 簡易に色と絵文字/頭文字を決定
    tone = (style.get("tone") or "").lower()
    persona = (style.get("persona") or "").lower()
    seed = (tone + "|" + persona + "|" + (title or "")).encode("utf-8")
    h = sum(seed) % 360
    color = f"hsl({h}, 60%, 35%)"
    # 絵文字候補
    emo_map = [
        ("やさ", "😊"), ("熱", "🔥"), ("冷静", "🧊"), ("論理", "🧠"), ("楽観", "🌞"), ("慎重", "🛡️"),
        ("アイデア", "💡"), ("技術", "🛠️"), ("管理", "📋"), ("進行", "🧭"), ("設計", "📐"), ("開発", "💻"),
    ]
    emoji = next((e for k,e in emo_map if k in persona or k in tone), None) or "💠"
    text = (title[:1].upper() if title else "A")
    return {"bg": color, "emoji": emoji, "text": text}


def _all_consult_roles() -> List[str]:
    """相談対象として扱う“主要ロール”一覧（順序あり）を返す。
    RECOMMEND_IDS を基準に、補助系（pm/writer/proof）も追加し、最後にカスタムロールを付与。"""
    base = list(RECOMMEND_IDS)
    # 進行補助のみ追加（執筆/校正は自動対象から除外）
    if "pm_ai" in ROLES_BY_ID and "pm_ai" not in base:
        base.append("pm_ai")
    # カスタムは自動対象に含めない（明示追加/言及時のみ）
    return base


def select_specialists(text: str, limit: int) -> List[str]:
    # 「全員で」「各担当で」「みんなで」などの依頼で“全ロール”を対象にする
    all_kw = ["全員", "みんな", "皆", "全体で", "各担当", "各自", "チーム全員", "それぞれ"]
    if any(k in text for k in all_kw):
        roles = _all_consult_roles()
        # 上限は全ロールに合わせて拡張
        limit = max(limit, len(roles))
        return roles[:limit]

    # 「多くの担当者」依頼の検出で上限を拡張
    many_keywords = ["多くの担当者", "多人数", "たくさんの意見", "幅広く", "多数のAI", "多方面", "多数意見", "多意見", "多様な視点"]
    if any(k in text for k in many_keywords):
        limit = max(limit, 8)
    hits = []
    low = text.lower()
    for role_id, keys in KEYMAP:
        for k in keys:
            if k in text or k.lower() in low:
                hits.append(role_id); break
    # 重複排除を保ちつつ先頭からlimit
    dedup = []
    for r in hits:
        if r not in dedup:
            dedup.append(r)
    if not dedup:
        # キーワードに当たらないが「多数意見」を求めている場合は推奨ロールから広めに選出
        if any(k in text for k in many_keywords):
            base = _all_consult_roles()
            dedup = [r for r in base if r not in EXCLUDE_AUTO_ROLES][:max(limit, 8)]
        else:
            # 初期状態での自動選定は行わない（統括のみを既定に）
            dedup = []
    # カスタムロールは自動選定に含めない（明示言及でのみ参加）
    return dedup[:limit]


def call_openai(system_prompt: str, user_text: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role":"system","content":system_prompt},{"role":"user","content":user_text}],
        temperature=0.6,
    )
    return (resp.choices[0].message.content or "").strip()

def call_anthropic(system_prompt: str, user_text: str) -> str:
    if not ANTHROPIC_API_KEY:
        return "（モック: Anthropic/Claude）設定が未構成です。"
    try:
        from anthropic import Anthropic  # type: ignore[reportMissingImports]
        client = Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1000,
            temperature=0.6,
            system=system_prompt,
            messages=[{"role": "user", "content": user_text}],
        )
        parts: list[str] = []
        for c in getattr(resp, "content", []) or []:
            if getattr(c, "type", "") == "text":
                parts.append(getattr(c, "text", ""))
        text = "".join(parts).strip()
        return text or "（Claude応答が空でした）"
    except Exception as e:
        return f"（Claude呼び出しエラー）{e}"

def call_gemini(system_prompt: str, user_text: str) -> str:
    if not GEMINI_API_KEY:
        return "（モック: Gemini）設定が未構成です。"
    try:
        import google.generativeai as genai  # type: ignore[reportMissingImports]
        genai.configure(api_key=GEMINI_API_KEY)  # type: ignore[attr-defined]
        model = genai.GenerativeModel(GEMINI_MODEL)  # type: ignore[attr-defined]
        prompt = f"[SYSTEM]\n{system_prompt}\n[/SYSTEM]\n\n{user_text}"
        resp = model.generate_content(prompt)  # type: ignore[attr-defined]
        text = getattr(resp, "text", "")
        return (text or "").strip() or "（Gemini応答が空でした）"
    except Exception as e:
        return f"（Gemini呼び出しエラー）{e}"


def consult(role_id: str, user_text: str) -> str:
    role = ROLES_BY_ID.get(role_id, {})
    sys_prompt = role.get("system_prompt", "")
    # 自然な会話文での返答を促す（定型の『1つだけ/1行だけ』などの縛りは与えない）
    user_text2 = (
        user_text.strip() +
    "\n\n返答スタイル: 自然な会話文で丁寧語。要点を分かりやすく、必要なら箇条書きも可。" \
    "挨拶や自己紹介（例:『こんにちは、◯◯AIです』など）は省き、いきなり要点から始めてください。役割名の名乗りも不要です。"
    )
    provider = (role.get("recommended_api") or "openai").lower()
    try:
        if provider in ("openai", "gpt", "chatgpt"):
            if OPENAI_API_KEY:
                return call_openai(sys_prompt, user_text2)
            else:
                return f"（モック: {role.get('title','')}・OpenAI未設定）…"
        elif provider in ("anthropic", "claude"):
            return call_anthropic(sys_prompt, user_text2)
        elif provider in ("gemini", "google"):
            return call_gemini(sys_prompt, user_text2)
        else:
            return f"（モック: {role.get('title','')}・未対応プロバイダ: {provider}）…"
    except Exception as e:
        return f"（モック: {role.get('title','')}）エラー: {e}"

def _conv_present_roles(conv_id: str) -> List[str]:
    roles: List[str] = []
    for ev in EVENTS:
        if ev.get("conv_id") != conv_id:
            continue
        lane = ev.get("lane") or ""
        if lane.startswith("consult:"):
            rid = lane.split(":",1)[1]
            if rid and rid not in roles:
                roles.append(rid)
        else:
            r = ev.get("role")
            if r and r not in ("user", "motivator_ai") and r not in roles:
                roles.append(r)
    return roles

def _missing_dependencies(current_roles: List[str], new_role: str) -> List[str]:
    deps = DEPENDENCIES.get(new_role, [])
    return [d for d in deps if d not in current_roles]


def choose_roles_for_message(conv_id: str, text: str, limit: int) -> List[str]:
    """メッセージごとに相談対象ロールを選ぶ。
    - 明示の全員キーワードがあれば全ロール
    - 既に参加しているロールがいて、かつ『案/提案/意見/アイデア』等のリクエストなら全参加ロールへブロードキャスト
    - それ以外は通常のキーワードベース選択
    """
    # まず全員検出（select_specialistsと同じ語彙）
    all_kw = ["全員", "みんな", "皆", "全体で", "各担当", "各自", "チーム全員", "それぞれ"]
    if any(k in text for k in all_kw):
        # 参加中メンバーのみ対象。メンバーがいない場合は空（『設定』から追加を促す想定）。
        members = _members_of(conv_id)
        roles = [r for r in members if r not in EXCLUDE_AUTO_ROLES]
        return roles[:max(limit, len(roles))]

    # 以降は会話メンバーを基準に扱う
    present = _members_of(conv_id)
    # 明示的なロール言及を拾う（日本語ラベル/英字ID）
    mentions: List[str] = []
    low = text.lower()
    for rid, label in ROLE_LABEL_JA.items():
        if (label and label in text) or (rid and rid in low):
            mentions.append(rid)
    # タイトル名（カスタムロールなど）も対象
    for rid, r in ROLES_BY_ID.items():
        title = (r.get("title") or "")
        if title and title in text and rid not in mentions:
            mentions.append(rid)
    ask_kw = ["案", "提案", "意見", "アイデア"]
    if (present or mentions) and any(k in text for k in ask_kw):
        # 既参加ロール全員へ（必要なら上限拡張）
        roles = list(dict.fromkeys(present + mentions))  # 順序維持の重複排除
        # 自動追加分は除外対象をフィルタ（ただし明示言及は残す）
        roles = [r for r in roles if (r in mentions) or (r not in EXCLUDE_AUTO_ROLES)]
        return roles[:max(limit, len(roles))]

    # 明示の言及があれば必ず含める
    base = select_specialists(text, limit)
    for rid in mentions:
        if rid not in base:
            base.append(rid)
    # 自動選定分は除外リストをフィルタ（言及は維持）
    filtered = [r for r in base if (r in mentions) or (r not in EXCLUDE_AUTO_ROLES)]
    return filtered[:max(limit, len(filtered))]


def motivate_followup(role_id: str, last_reply: str, turn_index: int, *, initial_head: str | None = None, asked: list[str] | None = None) -> tuple[str, str]:
    """次の明確化質問を返す。
    - 提案ヘッドライン（initial_head）は変更しない前提を明示
    - ラベル: one of [前提, 指標, リスク, 定義]
    - 既に asked 済みのラベルは避ける
    戻り値: (question, label)
    """
    head_lines = (last_reply or "").strip().splitlines()[:2]
    ref = (" ".join(head_lines)).strip()[:160]
    asked = asked or []

    labels = ["前提", "指標", "リスク", "定義"]
    # 役割による初手の優先度
    lead_pref = {
        "product_manager_ai": "指標",
        "project_manager_ai": "前提",
        "architect_ai": "リスク",
        "dev_ai": "リスク",
        "idea_ai": "定義",
    }.get(role_id)

    # 未質問の候補を順に決定（初手は役割優先、以降は残り）
    order: list[str] = []
    if turn_index == 0 and lead_pref and lead_pref not in asked:
        order.append(lead_pref)
    order += [l for l in labels if l not in order and l not in asked]
    if not order:
        order = labels[:]  # 念のため

    label = order[0]
    fixed = f"前提: 現在検討中の案のヘッドライン『{(initial_head or '').strip()}』は変えずに、補足のみ回答してください。1点だけ、短く。"
    qmap = {
        "前提": f"{fixed}\n前提共有の明確化: 『{ref}』。この提案が成立するための隠れた前提を1つだけ言語化してください。",
        "指標": f"{fixed}\n判断基準の明確化: 『{ref}』。採否を分ける評価指標（定量/定性どちらでも）を1つ提案し、測り方を1行で。",
        "リスク": f"{fixed}\nリスクの明確化: 『{ref}』。最も起こりやすい失敗を1つだけ挙げ、現実的な軽減策を1行で。",
        "定義": f"{fixed}\n用語/範囲の明確化: 『{ref}』。この中で曖昧そうな用語または対象範囲を1つ選び、定義を1行で。",
    }
    return qmap[label], label


def _extract_head(text: str) -> str:
    """
    提案名/企画名と思われる短い見出しを抽出。
    優先順位:
    1) 箇条書きの最初のアイテム（例: "1. 転職支援サービス"）
    2) 先頭行（定型の前置き語を除去）
    補足行（"補足"で始まる）や冗長な前置きは避ける。
    """
    t = (text or "").strip()
    if not t:
        return ""
    lines = [l.strip() for l in t.splitlines() if l.strip()]
    # 候補1: 箇条書き/番号付きの行
    bullet_re = re.compile(r"^(?:[-•●・\u30fb\u2022]|\d+[\.).]|\(\d+\))\s*(.+)")
    for l in lines:
        if l.startswith("補足"):
            continue
        m = bullet_re.match(l)
        if m:
            cand = m.group(1).strip()
            # 末尾の説明が長すぎる場合は先頭の名詞句を優先的に切り出し
            return cand[:80]
    # 候補2: 先頭行から定型の前置きを除去
    head = lines[0].lstrip("-•●・ 　")
    for pre in ("前提:", "提案:", "案:", "方針:"):
        if head.startswith(pre):
            head = head[len(pre):].strip()
    return head[:80]

# フォローアップの話題ローテーション（重複回避に利用）
TOPIC_ROTATION = [
    "具体化",
    "利益とコスト",
    "人材・体制",
    "リスク",
    "KPI",
]

# 追問のフォールバック候補（話題拡張用）
# topic名は「追問（topic）」の表記に使い、bodyは {head} を提案名で置換して生成
FALLBACK_QUESTIONS: list[tuple[str, str]] = [
    ("評価方法", "提案『{head}』の成功をどう評価しますか？主要な評価方法やサンプルKPIを1つ挙げ、測り方を1行で。"),
    ("企画の経緯", "この企画に至った経緯（背景の課題やトリガー）を1点だけ共有してください。判断の勘所が掴めます。"),
    ("差別化", "提案『{head}』の差別化ポイントを1つだけ挙げ、代替案との違いを短く説明してください。"),
    ("検証計画", "提案『{head}』を小さく検証する最短プラン（対象・期間・成功条件）を1行で教えてください。"),
    ("関係者", "実行に必要な関係者/部門を1つ挙げ、着手のために必要な合意事項を1行で。"),
    ("収益モデル", "提案『{head}』の収益化パスを1つだけ具体化してください（何に誰がいくら支払う？）。"),
]

def _last_followup_topic(conv_id: str, role_id: str) -> str | None:
    lane = f"consult:{role_id}"
    for e in reversed(EVENTS):
        if e.get("conv_id") == conv_id and e.get("lane") == lane and e.get("role") == "motivator_ai":
            txt = (e.get("text") or "")
            m = re.search(r"追問（([^）]+)）", txt)
            if m:
                return m.group(1)
            # 旧式の「補足のお願い」「追加の補足」もカウントだけはする
            if "補足のお願い" in txt or "追加の補足" in txt:
                return None
    return None

def _asked_followup_topics(conv_id: str, role_id: str) -> set[str]:
    """これまでにその役割へ投げた追問トピックを集合で返す。"""
    lane = f"consult:{role_id}"
    asked: set[str] = set()
    for e in EVENTS:
        if e.get("conv_id") != conv_id or e.get("lane") != lane or e.get("role") != "motivator_ai":
            continue
        txt = (e.get("text") or "")
        m = re.search(r"追問（([^）]+)）", txt)
        if m:
            asked.add(m.group(1))
    return asked

def _asked_followup_texts(conv_id: str, role_id: str) -> set[str]:
    """過去の追問全文（正規化）を集合で返す。重複防止に利用。"""
    lane = f"consult:{role_id}"
    seen: set[str] = set()
    for e in EVENTS:
        if e.get("conv_id") != conv_id or e.get("lane") != lane or e.get("role") != "motivator_ai":
            continue
        t = (e.get("text") or "").strip()
        if t:
            seen.add(t)
    return seen

def _next_followup_topic(conv_id: str, role_id: str, turn_index: int) -> str:
    last = _last_followup_topic(conv_id, role_id)
    if last in TOPIC_ROTATION:
        base = (TOPIC_ROTATION.index(last) + 1) % len(TOPIC_ROTATION)
    else:
        base = 0
    idx = (base + turn_index) % len(TOPIC_ROTATION)
    return TOPIC_ROTATION[idx]

def _score_for_adoption(text: str) -> int:
    """採用スコア（簡易ヒューリスティクス）。高いほど有力。
    目的: MVP性/実現容易性/効果測定/段階実行/低コストなどを優遇。
    外部APIなしで安定動作することを優先。
    """
    t = (text or "").lower()
    score = 0
    # MVP/小さく始める/検証
    for kw in ["mvp", "小さく", "スモール", "実験", "仮説", "検証", "prototype", "po c", "poc", "検証", "テスト"]:
        if kw in t:
            score += 3
    # 効果測定/KPI/計測
    for kw in ["kpi", "計測", "測定", "abテスト", "評価", "学び"]:
        if kw in t:
            score += 3
    # 期間短縮/早い/迅速
    for kw in ["短期", "迅速", "すぐ", "早く", "1週間", "2週間", "30分", "90分"]:
        if kw in t:
            score += 2
    # 実現容易性（既存SaaS/クラウド/ノーコード）
    for kw in ["saas", "クラウド", "既存", "ノーコード", "low-code", "ローコード", "テンプレート"]:
        if kw in t:
            score += 2
    # コスト抑制
    for kw in ["低コスト", "無料", "無償", "安価", "コスト", "費用対効果"]:
        if kw in t:
            score += 1
    # リスク/セキュリティ配慮
    for kw in ["リスク", "セキュリティ", "プライバシー", "ガバナンス"]:
        if kw in t:
            score += 1
    # 具体性（箇条書き/数字/見出し）
    for kw in ["- ", "•", "1.", "２", "3.", "# "]:
        if kw in t:
            score += 1
    # 文量が極端に短すぎる/長すぎる場合は減点
    n = len(t)
    if n < 40:
        score -= 1
    if n > 1200:
        score -= 1
    return score

def _gen_reason_from(text: str) -> str:
    t = (text or "").lower()
    reasons = []
    if any(k in t for k in ["kpi", "計測", "測定", "評価"]):
        reasons.append("効果測定の設計が含まれており、学びを素早く得られるため")
    if any(k in t for k in ["mvp", "小さく", "スモール", "実験", "poc", "prototype"]):
        reasons.append("小さく始めて検証を回せるため、リスクと期間を抑えられるため")
    if any(k in t for k in ["saas", "クラウド", "既存", "ノーコード", "low-code", "テンプレート"]):
        reasons.append("既存サービス/クラウド活用で実装が容易なため")
    if any(k in t for k in ["低コスト", "無償", "安価", "費用対効果"]):
        reasons.append("初期コストが小さく費用対効果が見込めるため")
    if any(k in t for k in ["セキュリティ", "プライバシー", "ガバナンス", "リスク"]):
        reasons.append("リスク/セキュリティへの言及があり現実的な運用に乗せやすいため")
    if not reasons:
        return "実現性・期間・費用対効果のバランスがよく、最初の検証として適しているため"
    # 2つまでに圧縮
    return "、".join(reasons[:2])

SUMMARY_PREFIXES = [
    "現時点の状況整理です。各担当から1案ずつ提案を受け、統括Mが要点を束ねました。",
    "いったんのまとめです。各担当の提案を要点で束ねました。",
    "ここまでの整理です。提案の要点を短く集約しました。",
]

def _pick_summary_prefix(conv_id: str) -> str:
    used = _recent_lane_texts(conv_id, "main")
    for pref in SUMMARY_PREFIXES:
        if pref not in used:
            return pref
    return SUMMARY_PREFIXES[0]

def motivate_summary(role_ids: List[str], role_to_last: Dict[str, str], role_initial: Dict[str, str] | None = None, role_clar: Dict[str, Dict[str, str]] | None = None) -> str:
    # 固定句ではなく、自然な現状報告＋採用案の明示にする
    bullets: List[str] = []
    scored: List[tuple[int, str]] = []  # (score, role_id)
    for r in role_ids:
        # 要約は初回提案（存在すれば）を基準にする
        base_text = (role_initial or {}).get(r) if role_initial else None
        text = (base_text or role_to_last.get(r) or "").strip()
        lines = [l.strip("- • ") for l in text.splitlines() if l.strip()]
        if not lines:
            continue
        # 1〜2行程度抜粋（冗長にならないように）
        snippet = "; ".join(lines[:2])
        name = ROLE_LABEL_JA.get(r) or ROLES_BY_ID.get(r, {}).get("title") or r
        note = ""
        if role_clar and r in role_clar:
            # 補足は最大2点だけ表示。既知キーが無ければ任意キーから拾う。
            pairs = []
            known_keys = ("指標", "前提", "リスク", "定義")
            for k in known_keys:
                v = (role_clar[r].get(k) or "").strip()
                if v:
                    v1 = v.splitlines()[0].strip().lstrip("-•●・ 　")
                    pairs.append(f"{k}={v1}")
                if len(pairs) >= 2:
                    break
            if len(pairs) < 2:
                for k, v in role_clar[r].items():
                    if k in known_keys:
                        continue
                    v1 = (v or "").strip()
                    if not v1:
                        continue
                    v1 = v1.splitlines()[0].strip().lstrip("-•●・ 　")
                    pairs.append(f"{k}={v1}")
                    if len(pairs) >= 2:
                        break
            if pairs:
                note = " （補足: " + ", ".join(pairs[:2]) + ")"
        bullets.append(f"{name}の提案: {snippet[:150]}{note}")
        scored.append((_score_for_adoption(text), r))

    if not bullets:
        return "現時点の整理: 特筆すべき提案はまだありません。必要に応じて追加で確認します。"

    body = "\n- " + "\n- ".join(bullets)

    # 採用案の選定
    best_role: str | None = None
    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        best_role = scored[0][1]

    adoption = ""
    if best_role:
        best_text = (role_initial or {}).get(best_role) if role_initial else None
        if not best_text:
            best_text = role_to_last.get(best_role, "")
        head = _extract_head(best_text)
        name = ROLE_LABEL_JA.get(best_role) or ROLES_BY_ID.get(best_role, {}).get("title") or best_role
        reason = _gen_reason_from(best_text)
        adoption = f"\n\n採用案\n- 候補: {name} の提案『{head[:60]}』\n- 選定理由: {reason}"

    # プレフィックスは呼出元で決定できないため、既定を返し、api_message 側で差し替え
    return f"{SUMMARY_PREFIXES[0]}\n{body}{adoption}"

def motivate_summary_short(role_ids: List[str], role_to_last: Dict[str, str], role_initial: Dict[str, str] | None = None, role_clar: Dict[str, Dict[str, str]] | None = None) -> str:
    bullets: List[str] = []
    for r in role_ids[:2]:
        text = (role_initial or {}).get(r) if role_initial else None
        t = (text or role_to_last.get(r) or "").strip()
        if not t:
            continue
        line = t.splitlines()[0].strip().lstrip("-•●・ 　")
        name = ROLE_LABEL_JA.get(r) or ROLES_BY_ID.get(r, {}).get("title") or r
        bullets.append(f"{name}: {line[:80]}")
    if not bullets:
        return "要点整理: 続きを確認します。"
    return "要点整理:\n- " + "\n- ".join(bullets[:2])

CONTINUE_PATTERNS = [
    "議論を継続しますか？（はい/いいえ/メンバー指定）例:『CFOとPMで継続』『CFOだけ継続』『統括だけで続けて』",
    "続けますか？（はい/いいえ/メンバー指定）例:『CFOとPMで継続』『統括だけで続けて』",
    "この先も進めますか？（はい/いいえ/メンバー指定）例:『CFOとPMで』『統括だけで』",
]

def _rotating_continue_prompt(conv_id: str) -> str:
    used = _recent_lane_texts(conv_id, "main")
    # 直近未使用のものを選択
    for i in range(len(CONTINUE_PATTERNS)):
        cand = CONTINUE_PATTERNS[i]
        if cand not in used:
            return cand
    # 全て出尽くしていたら最初の文面
    return CONTINUE_PATTERNS[0]

def _recent_main_context(conv_id: str, max_msgs: int = 6) -> str:
    """直近のメインレーン（あなた/統括M）のやり取りを短くまとめる。"""
    lines: List[str] = []
    mains = [e for e in EVENTS if e.get("conv_id") == conv_id and (e.get("lane") == "main") and (e.get("role") in ("user","motivator_ai"))]
    mains = mains[-max_msgs:]
    for e in mains:
        who = "あなた" if e.get("role") == "user" else "統括M"
        t = (e.get("text") or "").strip().replace("\n"," ")
        if len(t) > 120:
            t = t[:120] + "…"
        lines.append(f"{who}: {t}")
    return "\n".join(lines)

def _soft_followup_prompt(conv_id: str, role_id: str, initial_head: str, turn_index: int) -> str:
    """
    自然で短い追問を、話題ローテーションで重複を避けつつ提示する。
    例: 具体化/利益とコスト/人材・体制/リスク/KPI
    """
    head_disp = (initial_head or '').strip() or "先ほどの提案"
    asked_topics = _asked_followup_topics(conv_id, role_id)
    asked_texts = _asked_followup_texts(conv_id, role_id)

    # まずは基本ローテーションから未使用のトピックを優先
    topic = _next_followup_topic(conv_id, role_id, turn_index)
    if topic in asked_topics:
        # 未使用のトピックを探す
        for cand in TOPIC_ROTATION:
            if cand not in asked_topics:
                topic = cand
                break
        else:
            topic = None  # すべて使用済み

    body = None
    if topic == "具体化":
        body = f"提案『{head_disp}』をもう少し具体化してください。対象・チャネル・期間・地域・価格レンジなどから1点だけ決めて追記してください。"
    elif topic == "利益とコスト":
        body = f"提案『{head_disp}』について、現実的な利益やコストを1つずつ、簡潔な前提を添えて示してください。"
    elif topic == "人材・体制":
        body = f"提案『{head_disp}』の実行に必要な人材・体制を1点だけ挙げ、ロールと関与度を短く記してください。"
    elif topic == "リスク":
        body = f"提案『{head_disp}』で最も起こりやすい失敗と、その軽減策を1行で示してください。"
    elif topic == "KPI":
        body = f"提案『{head_disp}』の評価指標を1つ挙げ、測り方を1行で示してください。"

    # ローテーションが尽きた/完全重複する場合はフォールバックから選択
    if body is None:
        for fb_topic, fb_tpl in FALLBACK_QUESTIONS:
            if fb_topic in asked_topics:
                continue
            cand = f"追問（{fb_topic}）: " + fb_tpl.format(head=head_disp)
            if cand not in asked_texts:
                return cand
        # 全部使い切っていたら視点転換で最低限の変化
        fb_topic = "視点転換"
        fb_body = f"提案『{head_disp}』について、別の視点（顧客/現場/法務/運用/長期）から気になる点を1つだけ補足してください。"
        return f"追問（{fb_topic}）: {fb_body}"

    cand = f"追問（{topic}）: {body}"
    if cand in asked_texts:
        # テキストまで完全一致ならフォールバックへ
        for fb_topic, fb_tpl in FALLBACK_QUESTIONS:
            if fb_topic in asked_topics:
                continue
            alt = f"追問（{fb_topic}）: " + fb_tpl.format(head=head_disp)
            if alt not in asked_texts:
                return alt
        # それでも重複するなら微変化させる
        return f"追問（{topic}）: {body} 具体例を1つ添えてください。"
    # レーン内の直近重複も回避（同じ文面を連投しない）
    lane_hist = _recent_lane_texts(conv_id, f"consult:{role_id}")
    if cand in lane_hist:
        return f"追問（{topic}）: {body}（重複回避のため観点を1つ変えて）"
    return cand

def _is_substantive(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    low = t.lower()
    if any(ng in low for ng in ["補足なし", "特になし", "no additional", "なし"]):
        return False
    return len(t) >= 12

@app.post("/api/init", response_model=InitResponse)
def api_init():
    conv_id = str(uuid.uuid4())
    # 会話ごとの参加メンバーを初期化（明示追加で増える）
    CONV_MEMBERS[conv_id] = []
    ORCH_ONLY[conv_id] = False
    ev = push_event(conv_id, "motivator_ai", get_opening_message(), lane="main")
    return InitResponse(conversation_id=conv_id, events=[ev])

@app.post("/api/message", response_model=FeedResponse)
def api_message(payload: MessageRequest):
    if not payload.conversation_id: raise HTTPException(400, "conversation_id is required")
    if not payload.text.strip(): raise HTTPException(400, "text is empty")

    out_events: List[Dict[str, Any]] = []
    ev_user = push_event(payload.conversation_id, "user", payload.text.strip(), lane="main")
    out_events.append(ev_user)
    _remember_lane(payload.conversation_id, "main", ev_user.get("text") or "")

    # 直前に『継続しますか？』を出しており、今回の入力が「はい/いいえ」に該当する場合の分岐
    mains_prev = [e for e in EVENTS if e.get("conv_id") == payload.conversation_id and e.get("lane") == "main"]
    # 直前のシステム（司会）メッセージを参照（今入力したユーザー発言は除外）
    last_sys = ""
    if mains_prev:
        last = mains_prev[-1]
        if last.get("role") == "user" and len(mains_prev) >= 2:
            last_sys = mains_prev[-2].get("text") or ""
        else:
            last_sys = last.get("text") or ""
    # ユーザーのYes/No判定
    ans = payload.text.strip().lower()
    is_yes = ans in ("はい", "y", "yes", "つづけて", "続ける")
    is_no = ans in ("いいえ", "no", "終了", "終わり", "stop")

    # 直前に継続可否が出ており、今回はその回答だけだった場合のフラグ
    # （本当にYes/Noだけなら継続制御として扱う）
    def _is_yesno(s: str) -> tuple[bool,bool]:
        low = s.strip().lower()
        return (low in ("はい", "y", "yes", "つづけて", "続ける"), low in ("いいえ", "no", "終了", "終わり", "stop"))
    is_yes, is_no = _is_yesno(payload.text)

    # --- ユーティリティ: 言及/招集/統括のみの検出 ---
    def _extract_mentions(text: str) -> List[str]:
        m: List[str] = []
        low = (text or "").lower()
        for rid, label in ROLE_LABEL_JA.items():
            if (label and label in text) or (rid and rid in low):
                m.append(rid)
        for rid, r in ROLES_BY_ID.items():
            title = (r.get("title") or "")
            if title and title in text and rid not in m:
                m.append(rid)
        # 重複排除
        uniq: List[str] = []
        for x in m:
            if x not in uniq:
                uniq.append(x)
        return uniq

    def _detect_add_specialists(text: str) -> Dict[str, Any] | None:
        t = text or ""
        low = t.lower()
        verbs = ["参加させて","参加させる","参加して","呼んで","呼ぶ","招待","加えて","加わって","入れて"]
        if not any(v in t for v in verbs):
            # 明示の動詞がなくても『多数意見/多くの担当/広く意見を集めたい』等の意思があれば解釈
            many_intent = any(k in t for k in ["多数意見","多くの担当者","多人数","幅広く","広く意見","多様な視点","たくさんの意見"]) or (
                ("意見" in t and any(k in t for k in ["多く","多数","幅広く","広く"]))
            )
            if not many_intent:
                return None
            # 多数意見 intent として扱い、mentions は空のまま返す
            return {"mentions": [], "generic": True, "many_intent": True}
        mentions = _extract_mentions(t)
        generic = ("専門スタッフ" in t) or ("スタッフ" in t) or ("担当" in t)
        return {"mentions": mentions, "generic": generic}

    def _detect_orchestrator_only(text: str) -> bool:
        t = (text or "").strip()
        if not t:
            return False
        # 先頭呼びかけ or 『統括だけ/司会だけ』など
        if re.search(r"^(統括|司会|オーケストレーター)([、,。:：\s]|$)", t):
            return True
        if ("統括" in t or "司会" in t) and any(k in t for k in ["だけ","のみ","単独","一人で","だけで","だけと"]):
            return True
        # 『統括に相談』『司会に質問』など
        if any(phrase in t for phrase in ["統括に相談","統括に質問","統括お願いします","司会に相談","司会に質問"]):
            return True
        return False

    # コンサルト対象ロールの決定/分岐
    asked_continue = ("議論を継続しますか？" in last_sys)
    roles: List[str]
    # 継続質問に対する“メンバー指定”の解釈
    specified: List[str] = []
    if asked_continue:
        # ユーザー入力から役割の言及を抽出（日本語ラベル/英字ID/タイトル）
        low = payload.text.lower()
        for rid, label in ROLE_LABEL_JA.items():
            if (label and label in payload.text) or (rid and rid in low):
                specified.append(rid)
        for rid, r in ROLES_BY_ID.items():
            title = (r.get("title") or "")
            if title and title in payload.text and rid not in specified:
                specified.append(rid)
        # 現在の参加メンバーとの共通部分のみ継続対象にする
        members = [m for m in _members_of(payload.conversation_id) if m not in EXCLUDE_AUTO_ROLES]
        specified = [s for s in specified if s in members]

    # 継続確認に対する『統括だけで続けて』等の入力を優先解釈
    if asked_continue and _detect_orchestrator_only(payload.text):
        ORCH_ONLY[payload.conversation_id] = True
        ev_orch = push_event(payload.conversation_id, "motivator_ai", _orchestrator_main_reply(payload.conversation_id, payload.text), lane="main")
        out_events.append(ev_orch)
        _remember_lane(payload.conversation_id, "main", ev_orch.get("text") or "")
        return FeedResponse(events=out_events)

    if asked_continue and is_no:
        roles = []  # 相談しない
    elif asked_continue and (specified or is_yes):
        # 指定があれば指定メンバーのみ、無ければ全員
        if specified:
            roles = list(dict.fromkeys(specified))
            is_yes = True  # 後続ロジックではYes継続扱い
            # 指定されたが参加していない名称があれば軽く注意を出す
            try:
                mentioned_names: List[str] = []
                for rid in specified:
                    pass
                # 参加外の言及を検出（日本語名ベース）
                missing_mentions: List[str] = []
                # 収集: 入力に出た全言及（参加有無関係なく）
                all_mentions: List[str] = []
                for rid, label in ROLE_LABEL_JA.items():
                    if label and (label in payload.text):
                        all_mentions.append(rid)
                for rid, r in ROLES_BY_ID.items():
                    nm = (r.get("title") or "")
                    if nm and nm in payload.text and rid not in all_mentions:
                        all_mentions.append(rid)
                for rid in all_mentions:
                    if rid not in _members_of(payload.conversation_id):
                        nm = ROLE_LABEL_JA.get(rid) or ROLES_BY_ID.get(rid, {}).get("title") or rid
                        missing_mentions.append(nm)
                if missing_mentions:
                    ev_missing = push_event(payload.conversation_id, "motivator_ai", f"次の役割は現在の参加メンバーにいません: {', '.join(missing_mentions)}。必要なら『設定』から追加できます。", lane="main")
                    out_events.append(ev_missing)
                    _remember_lane(payload.conversation_id, "main", ev_missing.get("text") or "")
            except Exception:
                pass
        else:
            members = _members_of(payload.conversation_id)
            roles = [r for r in members if r not in EXCLUDE_AUTO_ROLES]
            if not roles:
                roles = choose_roles_for_message(payload.conversation_id, payload.text, SELECT_LIMIT)
    else:
        # まず『専門スタッフを参加させて』等の招集意図を優先的に解釈
        add_req = _detect_add_specialists(payload.text)
        if add_req:
            # 指定があればそれを優先。無ければ通常の自動選定。
            if add_req["mentions"]:
                roles = [r for r in add_req["mentions"] if (r in ROLES_BY_ID) and (r not in EXCLUDE_AUTO_ROLES)]
                # 何も有効でなければ通常選定へフォールバック
                if not roles:
                    roles = choose_roles_for_message(payload.conversation_id, payload.text, SELECT_LIMIT)
            else:
                roles = choose_roles_for_message(payload.conversation_id, payload.text, SELECT_LIMIT)
            # 統括のみモードは解除（専門スタッフを呼ぶ旨の明示）
            ORCH_ONLY[payload.conversation_id] = False
            # 招集アナウンス（main）
            try:
                if roles:
                    names = [ROLE_LABEL_JA.get(r) or ROLES_BY_ID.get(r, {}).get("title") or r for r in roles]
                    ev_added = push_event(payload.conversation_id, "motivator_ai", f"専門スタッフを参加させます: {', '.join(names)}", lane="main")
                    out_events.append(ev_added)
                    _remember_lane(payload.conversation_id, "main", ev_added.get("text") or "")
                else:
                    ev_noadd = push_event(payload.conversation_id, "motivator_ai", "参加可能な専門スタッフが見つかりませんでした。『設定』から追加してください。", lane="main")
                    out_events.append(ev_noadd)
                    _remember_lane(payload.conversation_id, "main", ev_noadd.get("text") or "")
            except Exception:
                pass
        else:
            # 統括のみモードの判定（持続 or 今回の発話での明示）
            orch_only_active = ORCH_ONLY.get(payload.conversation_id, False) or _detect_orchestrator_only(payload.text)
            # 『全員/各担当』などの明示やロール言及がある場合は通常選定に切り替え
            mentions_now = _extract_mentions(payload.text)
            all_kw = ["全員", "みんな", "皆", "全体で", "各担当", "各自", "チーム全員", "それぞれ"]
            if (orch_only_active or not _members_of(payload.conversation_id)) and (not any(k in payload.text for k in all_kw)) and (not mentions_now):
                # 統括のみ応答（main のみ/相談なし）
                ORCH_ONLY[payload.conversation_id] = True
                reply = _orchestrator_main_reply(payload.conversation_id, payload.text)
                ev_only = push_event(payload.conversation_id, "motivator_ai", reply, lane="main")
                out_events.append(ev_only)
                _remember_lane(payload.conversation_id, "main", ev_only.get("text") or "")
                return FeedResponse(events=out_events)
            # 通常選定
            roles = choose_roles_for_message(payload.conversation_id, payload.text, SELECT_LIMIT)
    role_to_last: Dict[str, str] = {}
    role_initial: Dict[str, str] = {}
    role_initial_head: Dict[str, str] = {}
    role_clar: Dict[str, Dict[str, str]] = {}

    ctx = _recent_main_context(payload.conversation_id, 6)
    if roles:
        # Yes継続時は新規アナウンスを出さない。通常時のみ簡潔アナウンス。
        if not (asked_continue and is_yes):
            ack = _ack_for_conv(payload.conversation_id, payload.text)
            if ack and ack.strip():
                ev_ack = push_event(payload.conversation_id, "motivator_ai", ack, lane="main")
                out_events.append(ev_ack)
                _remember_lane(payload.conversation_id, "main", ev_ack.get("text") or "")
    else:
        # 相談対象がゼロなら、統括のみで自然に継続（注意喚起は出さない）
        if not (asked_continue and is_no):
            ev_orch_only = push_event(payload.conversation_id, "motivator_ai", _orchestrator_main_reply(payload.conversation_id, payload.text), lane="main")
            out_events.append(ev_orch_only)
            _remember_lane(payload.conversation_id, "main", ev_orch_only.get("text") or "")
            return FeedResponse(events=out_events)
    # 継続時は過去の初回提案ヘッド/直近返信をイベントから復元
    if asked_continue and is_yes:
        for rid in roles:
            lane_key = f"consult:{rid}"
            # 最初の専門職返信
            first = next((e for e in EVENTS if e.get("conv_id") == payload.conversation_id and e.get("lane") == lane_key and e.get("role") == rid), None)
            if first:
                role_initial[rid] = first.get("text") or ""
                role_initial_head[rid] = _extract_head(role_initial[rid])
                role_clar[rid] = {}
            # 直近の専門職返信
            last = None
            for e in reversed(EVENTS):
                if e.get("conv_id") == payload.conversation_id and e.get("lane") == lane_key and e.get("role") == rid:
                    last = e; break
            if last:
                role_to_last[rid] = last.get("text") or ""

    for role_id in roles:
        lane = f"consult:{role_id}"
        if not (asked_continue and is_yes):
            # 研究系は担当を明示
            preface = f"専門職に意見を聞きます。議題: {payload.text.strip()}"
            if role_id == "cust_25895571":
                preface += "（公開情報の収集→要点抽出→統括で取りまとめ）"
            ev_pref = push_event(payload.conversation_id, "motivator_ai", preface, lane=lane)
            out_events.append(ev_pref)
            _remember_lane(payload.conversation_id, lane, ev_pref.get("text") or "")

        # メンバー登録（自動選定であっても参加扱いにする）
        _add_member(payload.conversation_id, role_id)

        # 入力文: 通常はユーザー入力＋直近文脈（URLがあれば簡易クロール結果を添付）
        utext = payload.text
        urls = _extract_urls(payload.text)
        # URLが無く、研究系ならWeb検索から候補を拾う
        if not urls and _classify_topic(payload.text) == "research":
            hits = web_search(payload.text, num=5)
            urls = [h.get("url") for h in hits if h.get("url")]  # type: ignore
        if urls:
            fetched: list[dict] = []
            for u in [x for x in urls if isinstance(x, str) and x]:
                fetched.append(_fetch_url_text(u))
            if fetched:
                snippet = "\n\n".join([f"[参照:{i+1}] {x['title']}\n{x['url']}\n---\n{x['text'][:800]}" for i,x in enumerate(fetched)])
                utext += "\n\n参考URL抜粋:\n" + snippet
        if ctx:
            utext = payload.text + "\n\n参考（直近のやり取り）:\n" + ctx
        if asked_continue and is_yes:
            # 継続時は“新規の初回提案”を要求せず、以降のフォローアップのみ実施
            last_reply = role_to_last.get(role_id, "")
        else:
            reply = consult(role_id, utext)
            ev_reply = push_event(payload.conversation_id, role_id, reply, lane=lane)
            out_events.append(ev_reply)
            _remember_lane(payload.conversation_id, lane, ev_reply.get("text") or "")
            last_reply = reply
            # 初回提案を固定（未設定時のみ）
            if role_id not in role_initial:
                role_initial[role_id] = reply
                role_initial_head[role_id] = _extract_head(reply)
                role_clar[role_id] = {}

        role_to_last[role_id] = last_reply

        # 柔らかいフォローアップ（非定型）。
        # 通常: 初回提案 + (FOLLOWUP_TURNS-1) 回の追質問（上限4）
        # YES継続: 新規初回なしなので FOLLOWUP_TURNS 回の追質問（上限5）
        turns = get_followup_turns()
        _loops = (turns if (asked_continue and is_yes) else max(0, turns-1))
        _cap = (5 if (asked_continue and is_yes) else 4)
        loops_to_run = max(0, min(_loops, _cap))
        for t in range(loops_to_run):
            ask = _soft_followup_prompt(payload.conversation_id, role_id, role_initial_head.get(role_id, ""), t)
            ev_ask = push_event(payload.conversation_id, "motivator_ai", ask, lane=lane)
            out_events.append(ev_ask)
            _remember_lane(payload.conversation_id, lane, ev_ask.get("text") or "")
            fup = consult(role_id, ask)
            # 補足として保存（ラベル付けなし／最大2点）
            lab = f"補足{t+1}"
            role_clar.setdefault(role_id, {})
            if _is_substantive(fup) and len(role_clar[role_id]) < 2:
                role_clar[role_id][lab] = fup
            ev_fup = push_event(payload.conversation_id, role_id, fup, lane=lane)
            out_events.append(ev_fup)
            _remember_lane(payload.conversation_id, lane, ev_fup.get("text") or "")
            role_to_last[role_id] = fup

    # まとめ報告（統括M→ユーザー）。Noでの継続否定時はスキップ。
    try:
        if not (asked_continue and is_no):
            style = ORCHSET.get("summary_style", "default")
            if style == "none":
                pass  # まとめを出さない
            elif style == "short":
                summary = motivate_summary_short(roles, role_to_last, role_initial=role_initial, role_clar=role_clar)
                # 見出しローテーション（短縮版は先頭行を書き換え）
                if summary.startswith("要点整理:"):
                    # 要点整理: はそのまま利用
                    pass
                ev_sum_s = push_event(payload.conversation_id, "motivator_ai", summary, lane="main")
                out_events.append(ev_sum_s)
                _remember_lane(payload.conversation_id, "main", ev_sum_s.get("text") or "")
            else:
                summary = motivate_summary(roles, role_to_last, role_initial=role_initial, role_clar=role_clar)
                # 先頭固定句をローテーションプレフィックスに差し替え
                pref = _pick_summary_prefix(payload.conversation_id)
                if summary.startswith(SUMMARY_PREFIXES[0]):
                    summary = summary.replace(SUMMARY_PREFIXES[0], pref, 1)
                ev_sum = push_event(payload.conversation_id, "motivator_ai", summary, lane="main")
                out_events.append(ev_sum)
                _remember_lane(payload.conversation_id, "main", ev_sum.get("text") or "")
    except Exception:
        pass
    # 司会より継続確認/分岐
    try:
        if asked_continue and is_no:
            # 今回は『いいえ』の回答。まとめ依頼は不適切なので出さない→自然な誘導質問に置換。
            prompt = _next_action_prompt(payload.text)
            ev_prompt = push_event(payload.conversation_id, "motivator_ai", prompt, lane="main")
            out_events.append(ev_prompt)
            _remember_lane(payload.conversation_id, "main", ev_prompt.get("text") or "")
        elif roles:
            # 相談と所定回数のフォローアップが完了した後にのみ継続可否を確認
            cont_msg = _rotating_continue_prompt(payload.conversation_id)
            ev_cont = push_event(payload.conversation_id, "motivator_ai", cont_msg, lane="main")
            out_events.append(ev_cont)
            _remember_lane(payload.conversation_id, "main", ev_cont.get("text") or "")
    except Exception:
        pass
    return FeedResponse(events=out_events)

# レコメンド（暫定: 既存ロールから上限limitを返す）
@app.get("/api/recommend")
def api_recommend(limit: int = 12):
    limit = max(1, min(30, int(limit)))
    items: list[dict] = []
    seen: set[str] = set()
    # まず定義順（RECOMMEND_IDS）
    for rid in RECOMMEND_IDS:
        r = ROLES_BY_ID.get(rid)
        if not r:
            continue
        if r.get("id") == "motivator_ai":
            continue
        if r.get("id") in EXCLUDE_AUTO_ROLES:
            continue
        items.append({
            "id": r.get("id"),
            "title": r.get("title"),
            "personality": r.get("personality"),
            "recommended_api": r.get("recommended_api"),
            # プロフィール用の任意項目
            "tone": r.get("tone"),
            "catchphrase": r.get("catchphrase"),
            "domain": r.get("domain"),
            "description": r.get("description") or (r.get("title","")+"の役割カード")
        })
        seen.add(rid)
    # 次にカスタムロール（cust_*）を補完
    for rid, r in ROLES_BY_ID.items():
        if rid in seen or not rid.startswith("cust_"):
            continue
        if rid in EXCLUDE_AUTO_ROLES:
            continue
        if rid == "motivator_ai":
            continue
        items.append({
            "id": r.get("id"),
            "title": r.get("title"),
            "personality": r.get("personality"),
            "recommended_api": r.get("recommended_api"),
            "tone": r.get("tone"),
            "catchphrase": r.get("catchphrase"),
            "domain": r.get("domain"),
            "description": r.get("description") or (r.get("title","")+"の役割カード")
        })
        seen.add(rid)
    # 最後にその他の標準ロール（除外対象を除く）
    for rid, r in ROLES_BY_ID.items():
        if rid in seen:
            continue
        if rid in EXCLUDE_AUTO_ROLES or rid == "motivator_ai":
            continue
        items.append({
            "id": r.get("id"),
            "title": r.get("title"),
            "personality": r.get("personality"),
            "recommended_api": r.get("recommended_api"),
            "tone": r.get("tone"),
            "catchphrase": r.get("catchphrase"),
            "domain": r.get("domain"),
            "description": r.get("description") or (r.get("title","")+"の役割カード")
        })
    return {"version": "v2", "roles": items[:limit]}

# 互換性のための新エンドポイント（フロントは基本こちらを利用）
@app.get("/api/recommend_v2")
def api_recommend_v2(limit: int = 12):
    limit = max(1, min(30, int(limit)))
    items: list[dict] = []
    seen: set[str] = set()
    # まず定義順（RECOMMEND_IDS）
    for rid in RECOMMEND_IDS:
        r = ROLES_BY_ID.get(rid)
        if not r:
            continue
        if r.get("id") in EXCLUDE_AUTO_ROLES:
            continue
        items.append({
            "id": r.get("id"),
            "title": r.get("title"),
            "personality": r.get("personality"),
            "recommended_api": r.get("recommended_api"),
            "description": r.get("description") or (r.get("title","")+"の役割カード"),
            "icon": r.get("icon"),
            "tone": r.get("tone"),
            "catchphrase": r.get("catchphrase"),
            "domain": r.get("domain"),
            "addable": True,
        })
        seen.add(rid)
    # 次にカスタムロール（cust_*）を補完
    for rid, r in ROLES_BY_ID.items():
        if rid in seen or not rid.startswith("cust_"):
            continue
        if rid in EXCLUDE_AUTO_ROLES:
            continue
        items.append({
            "id": r.get("id"),
            "title": r.get("title"),
            "personality": r.get("personality"),
            "recommended_api": r.get("recommended_api"),
            "description": r.get("description") or (r.get("title","")+"の役割カード"),
            "icon": r.get("icon"),
            "tone": r.get("tone"),
            "catchphrase": r.get("catchphrase"),
            "domain": r.get("domain"),
            "addable": True,
        })
        seen.add(rid)
    # 統括Mを設定専用として含める
    r = ROLES_BY_ID.get("motivator_ai")
    if r:
        items.append({
            "id": r.get("id"),
            "title": r.get("title"),
            "personality": r.get("personality"),
            "recommended_api": r.get("recommended_api"),
            "description": "議論の司会と橋渡し（設定のみ。追加は不要）",
            "icon": r.get("icon"),
            "tone": r.get("tone"),
            "catchphrase": r.get("catchphrase"),
            "domain": r.get("domain"),
            "addable": False,
            "orchestrator": True,
        })
    # 最後に標準ロールの残りも補完（除外対象は出さない）
    for rid, r in ROLES_BY_ID.items():
        if rid in seen or rid in EXCLUDE_AUTO_ROLES or rid == "motivator_ai":
            continue
        items.append({
            "id": r.get("id"),
            "title": r.get("title"),
            "personality": r.get("personality"),
            "recommended_api": r.get("recommended_api"),
            "description": r.get("description") or (r.get("title","")+"の役割カード"),
            "icon": r.get("icon"),
            "tone": r.get("tone"),
            "catchphrase": r.get("catchphrase"),
            "domain": r.get("domain"),
            "addable": True,
        })

    return {"version": "v2", "roles": items[:limit]}

# ---- 統括（オーケストレーター）専用設定 API ----
class OrchestratorSettings(BaseModel):
    opening_message: str | None = None
    followup_turns: int | None = None
    acks: Dict[str, str] | None = None  # keys: research/plan/tech/gtm/general
    summary_style: str | None = None    # default | short | none

@app.get("/api/orchestrator")
def api_get_orchestrator():
    r = ROLES_BY_ID.get("motivator_ai", {})
    role_view = {
        "id": r.get("id"),
        "title": r.get("title"),
        "recommended_api": r.get("recommended_api"),
        "personality": r.get("personality"),
        "tone": r.get("tone"),
        "catchphrase": r.get("catchphrase"),
        "domain": r.get("domain"),
        "icon": r.get("icon"),
    }
    return {
        "settings": {
            "opening_message": get_opening_message(),
            "followup_turns": get_followup_turns(),
            "acks": ORCHSET.get("acks", {}),
            "summary_style": ORCHSET.get("summary_style", "default"),
        },
        "role": role_view,
    }

@app.put("/api/orchestrator")
def api_update_orchestrator(payload: OrchestratorSettings):
    changed = False
    if payload.opening_message is not None:
        ORCHSET["opening_message"] = str(payload.opening_message or "").strip()
        changed = True
    if payload.followup_turns is not None:
        try:
            ft = max(1, min(8, int(payload.followup_turns)))
            ORCHSET["followup_turns"] = ft
            changed = True
        except Exception:
            raise HTTPException(400, "followup_turns must be int 1..8")
    if payload.summary_style is not None:
        if payload.summary_style not in ("default","short","none"):
            raise HTTPException(400, "summary_style must be one of default|short|none")
        ORCHSET["summary_style"] = payload.summary_style
        changed = True
    if payload.acks is not None:
        if not isinstance(payload.acks, dict):
            raise HTTPException(400, "acks must be object")
        # 既存にマージ（空文字は削除）
        cur = ORCHSET.get("acks", {}) or {}
        if not isinstance(cur, dict):
            cur = {}
        for k, v in payload.acks.items():
            if not isinstance(k, str):
                continue
            if isinstance(v, str) and v.strip():
                cur[k] = v.strip()
            else:
                if k in cur:
                    del cur[k]
        ORCHSET["acks"] = cur
        changed = True
    if changed:
        _save_orchset()
    return {
        "ok": True,
        "settings": {
            "opening_message": get_opening_message(),
            "followup_turns": get_followup_turns(),
            "acks": ORCHSET.get("acks", {}),
            "summary_style": ORCHSET.get("summary_style", "default"),
        }
    }
    return {"version": "v2", "roles": items[:limit]}

@app.get("/api/presets")
def api_presets():
    # 返却: 定義済みフェーズプリセット
    # 公開面では除外対象（writer/proofや特定customなど）をあらかじめ取り除く
    sanitized = []
    for p in PRESETS:
        roles = [rid for rid in p.get("roles", []) if (rid in ROLES_BY_ID) and (rid not in EXCLUDE_AUTO_ROLES)]
        # 役割が空になってもプレースホルダとして返す（フロントで扱えるように）
        q = p.copy(); q["roles"] = roles
        sanitized.append(q)
    return {"presets": sanitized}

@app.get("/api/health")
def api_health():
    return {
        "ok": True,
        "roles": len(ROLES),
        # OpenAI 既定モデル
        "openai_model": os.getenv("OPENAI_MODEL", OPENAI_MODEL),
        # 各プロバイダのキー有無（trueなら.env から読めている）
        "openai_configured": bool(OPENAI_API_KEY),
        "anthropic_configured": bool(ANTHROPIC_API_KEY),
        "gemini_configured": bool(GEMINI_API_KEY),
    # 参考: フォローアップ設定（現在の有効値）
    "followup_turns": get_followup_turns(),
        "select_limit": SELECT_LIMIT,
    }

class AddAgentRequest(BaseModel):
    conversation_id: str
    role_id: str

@app.post("/api/add-agent", response_model=FeedResponse)
def api_add_agent(payload: AddAgentRequest):
    if payload.role_id not in ROLES_BY_ID: raise HTTPException(404, "unknown role")
    # 公開側からは除外対象の追加を許可しない
    if payload.role_id in EXCLUDE_AUTO_ROLES:
        raise HTTPException(403, "role not allowed")
    lane = f"consult:{payload.role_id}"
    evs = []
    present = _conv_present_roles(payload.conversation_id)
    missing = _missing_dependencies(present, payload.role_id)
    name = ROLE_LABEL_JA.get(payload.role_id) or ROLES_BY_ID[payload.role_id].get("title") or payload.role_id
    evs.append(push_event(payload.conversation_id, "motivator_ai", f"{name} をチームに追加しました（統括M）。必要に応じて相談します。", lane="main"))
    # 参加メンバーに登録
    _add_member(payload.conversation_id, payload.role_id)
    if missing:
        miss_names = [ROLE_LABEL_JA.get(m) or ROLES_BY_ID.get(m, {}).get("title") or m for m in missing]
    evs.append(push_event(payload.conversation_id, "motivator_ai", f"警告: {name} の前提ロールが不足しています → {', '.join(miss_names)}", lane="main"))
    return FeedResponse(events=evs)

class AddAgentsRequest(BaseModel):
    conversation_id: str
    role_ids: List[str]

@app.post("/api/add-agents", response_model=FeedResponse)
def api_add_agents(payload: AddAgentsRequest):
    if not payload.role_ids:
        raise HTTPException(400, "role_ids is empty")
    evs: List[Dict[str, Any]] = []
    added = []
    present = _conv_present_roles(payload.conversation_id)
    # 除外対象ははじく
    role_ids = [rid for rid in payload.role_ids if (rid in ROLES_BY_ID) and (rid not in EXCLUDE_AUTO_ROLES)]
    for rid in role_ids:
        if rid not in ROLES_BY_ID:
            continue
        lane = f"consult:{rid}"
        name = ROLE_LABEL_JA.get(rid) or ROLES_BY_ID[rid].get("title") or rid
        evs.append(push_event(payload.conversation_id, "motivator_ai", f"{name} をチームに追加しました（統括M）。必要に応じて相談します。", lane="main"))
        missing = _missing_dependencies(present + added, rid)
        if missing:
            miss_names = [ROLE_LABEL_JA.get(m) or ROLES_BY_ID.get(m, {}).get("title") or m for m in missing]
            evs.append(push_event(payload.conversation_id, "motivator_ai", f"警告: {name} の前提ロールが不足しています → {', '.join(miss_names)}", lane="main"))
        added.append(rid)
        _add_member(payload.conversation_id, rid)
    if not added:
        raise HTTPException(404, "no valid roles")
    return FeedResponse(events=evs)

@app.get("/api/feed", response_model=FeedResponse)
def api_feed(since: int = 0):
    return FeedResponse(events=[e for e in EVENTS if e["id"] > since])

FRONT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "frontend"))

# ===== ロール管理API =====
class RoleConfig(BaseModel):
    id: str
    title: str
    recommended_api: str | None = None
    description: str | None = None
    # キャラ設定
    persona: str | None = None         # 性格/キャラ
    tone: str | None = None            # 口調/文体
    catchphrase: str | None = None     # 口癖（任意）
    domain: str | None = None          # 専門領域

def _build_system_prompt(cfg: RoleConfig) -> str:
    base = [
        f"あなたは{cfg.title}です。",
        "ユーザーの議題に対し、自然な会話文で端的に助言します。",
        "挨拶や自己紹介は省き、要点から始めます。",
    ]
    if cfg.domain:
        base.append(f"専門領域: {cfg.domain}。この範囲の判断・助言を優先します。")
    if cfg.persona:
        base.append(f"性格/キャラ: {cfg.persona}。")
    if cfg.tone:
        base.append(f"口調/文体: {cfg.tone}。")
    if cfg.catchphrase:
        base.append(f"口癖: 必要なときだけ『{cfg.catchphrase}』を短く使います。乱用しません。")
    return "\n".join(base)

class CreateRoleRequest(BaseModel):
    id: str | None = None
    title: str
    recommended_api: str | None = None
    description: str | None = None
    persona: str | None = None
    tone: str | None = None
    catchphrase: str | None = None
    domain: str | None = None

@app.post("/api/roles", status_code=201)
def api_create_role(payload: CreateRoleRequest):
    rid = payload.id or ("cust_" + uuid.uuid4().hex[:8])
    if rid in ROLES_BY_ID or rid in CUSTOM_ROLES:
        raise HTTPException(409, "role id exists")
    cfg = RoleConfig(
        id=rid,
        title=payload.title,
        recommended_api=payload.recommended_api or "openai",
        description=payload.description,
        persona=payload.persona,
        tone=payload.tone,
        catchphrase=payload.catchphrase,
        domain=payload.domain,
    )
    sys_prompt = _build_system_prompt(cfg)
    icon = _gen_icon_from_style(cfg.dict(), payload.title)
    role_obj = {
        "id": rid,
        "title": payload.title,
        "recommended_api": cfg.recommended_api,
        "description": payload.description,
        "personality": cfg.persona,
    # 任意の詳細プロパティ（プロフィール用にも返却）
    "tone": cfg.tone,
    "catchphrase": cfg.catchphrase,
    "domain": cfg.domain,
        "system_prompt": sys_prompt,
        "icon": icon,
    }
    CUSTOM_ROLES[rid] = role_obj
    ROLES_BY_ID[rid] = role_obj
    _save_custom_roles()
    return {"role": role_obj}

class UpdateRoleRequest(BaseModel):
    title: str | None = None
    recommended_api: str | None = None
    description: str | None = None
    persona: str | None = None
    tone: str | None = None
    catchphrase: str | None = None
    domain: str | None = None

@app.put("/api/roles/{role_id}")
def api_update_role(role_id: str, payload: UpdateRoleRequest):
    role = ROLES_BY_ID.get(role_id)
    if not role:
        raise HTTPException(404, "unknown role")
    # 更新
    merged = role.copy()
    if payload.title is not None: merged["title"] = payload.title
    if payload.recommended_api is not None: merged["recommended_api"] = payload.recommended_api
    if payload.description is not None: merged["description"] = payload.description
    # 任意項目は値が指定されたときのみ上書き（None 指定で消すことも許容）
    if payload.tone is not None: merged["tone"] = payload.tone
    if payload.catchphrase is not None: merged["catchphrase"] = payload.catchphrase
    if payload.domain is not None: merged["domain"] = payload.domain
    # キャラ反映
    cfg = RoleConfig(
        id=role_id,
        title=merged.get("title") or role.get("title") or role_id,
        recommended_api=merged.get("recommended_api"),
        description=merged.get("description"),
        persona=(payload.persona if payload.persona is not None else role.get("personality")),
        tone=merged.get("tone"),
        catchphrase=merged.get("catchphrase"),
        domain=merged.get("domain"),
    )
    merged["system_prompt"] = _build_system_prompt(cfg)
    merged["personality"] = cfg.persona
    # 明示的に保持
    merged["tone"] = cfg.tone
    merged["catchphrase"] = cfg.catchphrase
    merged["domain"] = cfg.domain
    merged["icon"] = _gen_icon_from_style(cfg.dict(), merged.get("title") or role_id)
    # 保存
    ROLES_BY_ID[role_id] = merged
    CUSTOM_ROLES[role_id] = merged
    _save_custom_roles()
    return {"role": merged}

# すべてのAPI定義の後に静的ファイルをマウント
app.mount("/", StaticFiles(directory=FRONT, html=True), name="frontend")
