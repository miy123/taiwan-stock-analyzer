"""
回測實證資料 —— 讓 App 顯示「這個策略歷史上到底有沒有用」。

資料來源：backtest_results.json（由 backtest_research.py 產生）。
重點結論（近3年139期 + 近1年39期，兩期間排名一致）：
  · 長線／中線的趨勢結構分是唯一穩健有效的訊號（超額報酬顯著為正）
  · 「低基期／還沒漲」系列（潛力潛伏、攻守兼備）超額報酬**顯著為負**，輸給隨便買
  · ATR 風報比篩選無效
因此 App 不應把「潛力潛伏／攻守兼備」當成有實證支持的選股法，需明確標示。
"""

import json
import os

_FILE = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backtest_results.json")
)


# ⚠️ 這裡曾有一份「App 策略鍵 → 回測模型名稱」對照表（STRATEGY_TO_MODEL /
# MODEL_TO_STRATEGY）以及依賴它的 model_for / get_stats / regime_stats /
# best_strategies_for_regime。策略整併為 5 個之後它們全部沒有呼叫端，
# 鍵名（momentum / bestproven / sleeper / balanced）也都是已刪除的策略——
# 留著就是第二份會與策略表不一致的對照關係。
# 現在策略表自己帶 `evidence_model` + `evidence_run`，一律用
# get_stats_for_model() / regime_stats_for_model() 直接查。
#
# 同時移除了 score_bucket_stats / buy_threshold / load_thresholds：它們讀的是
# `score_thresholds.json`（**舊離散長線分**的分桶表）。分數換成連續趨勢分之後
# 那張表就不是同一個量表，UI 一律改讀 trend_score_thresholds.json
# （trend_bucket_stats / trend_threshold / trend_bucket_rows）。


def load_evidence() -> dict:
    try:
        with open(_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def get_stats_for_model(model_name, hold_days: int = 20, run: str = "main_3y"):
    """直接以回測模型名稱查詢（策略表已帶 evidence_model，不必再經 STRATEGY_TO_MODEL）。"""
    ev = load_evidence()
    if not ev or not model_name:
        return {}
    models = ev.get("runs", {}).get(run, {}).get("models", {})
    return dict(models.get(model_name, {}).get(str(hold_days), {}) or
                models.get(model_name, {}).get(hold_days, {}) or {})


def regime_stats_for_model(model_name, app_regime: str = "neutral") -> dict:
    """指定模型在目前大盤環境下的歷史超額報酬。"""
    ev = load_evidence()
    if not ev or not model_name:
        return {}
    table = ev.get("runs", {}).get("main_3y", {}).get("by_regime_1m", {})
    bucket = _REGIME_MAP.get(app_regime, "震盪")
    d = (table.get(model_name) or {}).get(bucket)
    return dict(d, regime_label=bucket) if d else {}


def verdict(stats: dict) -> dict:
    """Turn raw stats into a display verdict."""
    if not stats:
        return {"label": "未回測", "color": "#78909c", "icon": "❔",
                "note": "此策略尚未納入回測驗證"}
    exc = stats.get("excess_return", 0)
    sig = stats.get("significant", False)
    if exc > 0 and sig:
        return {"label": "實證有效", "color": "#4caf50", "icon": "✅",
                "note": "歷史超額報酬顯著為正"}
    if exc < 0 and sig:
        return {"label": "實證為負", "color": "#f44336", "icon": "⛔",
                "note": "歷史上顯著輸給『隨便買』，請謹慎"}
    if exc > 0:
        return {"label": "略優但不顯著", "color": "#a9e34b", "icon": "➕",
                "note": "超額報酬為正但統計上與運氣難以區分"}
    return {"label": "無明顯優勢", "color": "#ff9800", "icon": "➖",
            "note": "超額報酬為負或接近零"}


def all_model_rows(hold_days: int = 20, run: str = "main_3y") -> list:
    """Every backtested model at a holding period, best excess first."""
    ev = load_evidence()
    models = ev.get("runs", {}).get(run, {}).get("models", {})
    rows = []
    for name, hz in models.items():
        d = hz.get(str(hold_days)) or hz.get(hold_days)
        if d:
            rows.append({"name": name, **d})
    rows.sort(key=lambda r: -r["excess_return"])
    return rows


def benchmark_return(hold_days: int = 20, run: str = "main_3y"):
    ev = load_evidence()
    b = ev.get("runs", {}).get(run, {}).get("benchmark_return", {})
    return b.get(str(hold_days), b.get(hold_days))


def meta() -> dict:
    return load_evidence().get("meta", {})


# ── 分大盤環境（重要修正）────────────────────────────────────────────────────
# 5年179期的分環境檢驗推翻了「潛力/低基期一律有害」的早期結論：
#   動能類（長線/量能確認）：多頭 +3.95%、空頭仍 +0.62%（全環境皆可用）
#   低基期類（潛力潛伏/攻守兼備）：多頭 -0.98%/-1.26%，但**空頭 +2.23%/+1.13%**
# 因此策略優劣取決於當前市場環境，App 應依大盤狀態推薦，而非一概否定。

# App 大盤 regime → 回測環境分類
_REGIME_MAP = {
    "bull": "多頭", "mild_bull": "多頭",
    "neutral": "震盪",
    "mild_bear": "空頭", "bear": "空頭",
}


# ── 四個週期分數的實證效力 ──────────────────────────────────────────────────
# ⚠️ 命名澄清：這些分數的差別是「**用多長的指標計算**」，不是「**建議抱多久**」。
# 兩者是獨立的：你可以用長線分選股、然後只抱一週（實測這樣也最好）。
# 179期實證（超額報酬 / t值）：
#   長線   1週+0.53%(2.61✳) 1個月+2.62%(4.71✳) 3個月+11.57%(6.32✳)
#   中線   1週+0.28%(1.25 ) 1個月+1.90%(4.23✳) 3個月 +7.45%(6.00✳)
#   短線   1週+0.39%(2.07✳) 1個月+1.29%(3.30✳) 3個月 +2.25%(3.04✳)
#   極短線 1週-0.00%(-0.01) 1個月+0.23%(0.74 ) 3個月 +0.71%(1.00 ) ← 全不顯著
HORIZON_EFFICACY = {
    "long": {
        "tag": "✅", "label": "實證最強", "color": "#4caf50",
        "basis": "季線MA120、MA60>MA120、半年報酬",
        "note": "三種持有期都顯著（t=2.61/4.71/6.32），**不論你打算抱多久，這都是最好的選股依據**",
    },
    "medium": {
        "tag": "✅", "label": "有效", "color": "#a9e34b",
        "basis": "MA20/MA60 排列與斜率、近月報酬",
        "note": "1個月以上顯著（t=4.23/6.00）；當『第二確認』（≥60分）可小幅提升長線模型",
    },
    "short": {
        "tag": "🟡", "label": "偏弱", "color": "#ff9800",
        "basis": "MA5/MA10、MACD交叉、量能",
        "note": "顯著但強度低；是除長線外唯一在『持有1週』也顯著者（t=2.07）",
    },
    "ultra_short": {
        "tag": "🔴", "label": "無預測力", "color": "#f44336",
        "basis": "當日量價、KD/RSI極值、MA5",
        "note": "三種持有期全部不顯著（t=-0.01/0.74/1.00），分桶亦無規律——"
                "**視為雜訊，只當盤中氣氛參考，勿作買賣依據**。實測把它當過濾條件"
                "反而會傷害長線模型（3個月超額 11.57%→5.62%）",
    },
}


def horizon_efficacy(key: str) -> dict:
    return HORIZON_EFFICACY.get(key, {})


# ── 連續趨勢分的分桶實證（trend_score_buckets.py 產生）─────────────────────
# UI 一律讀這裡，不要在畫面上寫死任何分桶數字——寫死的那份已經過期兩次：
# 第一次是換成純技術長線分時，第二次是換成連續趨勢分時。
_trend_buckets = None


def load_trend_buckets() -> dict:
    global _trend_buckets
    if _trend_buckets is None:
        try:
            import json
            from services.scoring import BUCKET_PATH
            _trend_buckets = json.loads(BUCKET_PATH.read_text())
        except Exception:
            _trend_buckets = {}
    return _trend_buckets


def trend_bucket_stats(score, hold_days: int = 20) -> dict:
    """某個趨勢分落在哪個實證區間 → 該區間的歷史超額與勝率。"""
    if score is None:
        return {}
    data = load_trend_buckets().get("buckets", {}).get(str(hold_days))
    if not data:
        return {}
    for row in data["rows"]:
        if row["lo"] <= score < row["hi"] or (row["hi"] >= 100 and score >= row["lo"]):
            return row
    return {}


def trend_threshold(hold_days: int = 20):
    """超額報酬轉正的最低分數區間（實測值，不是猜的）。"""
    data = load_trend_buckets().get("buckets", {}).get(str(hold_days))
    return data.get("turns_positive_at") if data else None


def trend_monotonicity(hold_days: int = 20):
    data = load_trend_buckets().get("buckets", {}).get(str(hold_days))
    return data.get("spearman") if data else None


def trend_bucket_rows(hold_days: int = 20) -> list:
    data = load_trend_buckets().get("buckets", {}).get(str(hold_days))
    return data["rows"] if data else []


# ── 策略對照（strategy_comparison.py 產生）─────────────────────────────────
# **唯一可以拿來比較策略強弱的來源。** 其他 run 的數字是不同批日期、
# 不同方法量出來的，跨 run 比名次無效（全距 0.6~0.76% > 模型間差異）。
_strat_cmp = None


def load_strategy_comparison() -> dict:
    global _strat_cmp
    if _strat_cmp is None:
        try:
            import json
            from pathlib import Path
            p = Path(__file__).resolve().parent.parent / "strategy_comparison.json"
            _strat_cmp = json.loads(p.read_text())
        except Exception:
            _strat_cmp = {}
    return _strat_cmp


def strategy_stats(key: str, hold_days: int = 60) -> dict:
    d = load_strategy_comparison().get("strategies", {}).get(key) or {}
    return d.get(f"h{hold_days}") or {}


def strategy_walk_forward(key: str, hold_days: int = 60) -> dict:
    d = load_strategy_comparison().get("strategies", {}).get(key) or {}
    return (d.get("walk_forward") or {}).get(f"h{hold_days}") or {}


def strategy_ranking(hold_days: int = 60) -> list:
    """依超額報酬排名，回傳 [(key, stats)]，最強在前。"""
    cmp = load_strategy_comparison().get("strategies", {})
    rows = [(k, v.get(f"h{hold_days}")) for k, v in cmp.items()
            if v.get(f"h{hold_days}")]
    rows.sort(key=lambda kv: -kv[1]["excess"])
    return rows


def cross_stats(key_a: str, key_b: str, hold_days: int = 60) -> dict:
    """兩個策略「交集」的實證表現（strategy_comparison.py 的 cross_screen 段）。"""
    cs = load_strategy_comparison().get("cross_screen", {}).get("pairs", {})
    for kk in (f"{key_a}+{key_b}", f"{key_b}+{key_a}"):
        if kk in cs:
            d = cs[kk]
            out = dict(d.get(f"h{hold_days}") or {})
            out["avg_picks"] = d.get("avg_picks")
            out["vs_best_solo_h60"] = d.get("vs_best_solo_h60")
            return out
    return {}


def cross_top_n() -> int:
    return load_strategy_comparison().get("cross_screen", {}).get(
        "top_n_per_strategy", 20)


def run_periods(run: str = "main_3y"):
    """某次回測的期數 —— 說明文字要顯示期數時從這裡取，不要寫死。"""
    r = load_evidence().get("runs", {}).get(run) or {}
    return r.get("periods")
