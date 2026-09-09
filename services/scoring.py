"""
連續趨勢結構分 —— 全站唯一的排序分數。

## 為什麼要有這個檔

舊的長線分是 `50 ± 14 ± 12 ± 8 + 10` 這種**離散跳點**，全市場只有 40 種可能值，
滿分 94 有 277 檔並列（49%）。「取前 10 名」實際上是在同分群裡亂數挑，
後來只好加「距季線幅度」當平手鍵——但那等於**真正在排序的是平手鍵，不是分數**。

這裡改用 **橫斷面百分位**（cross-sectional percentile rank）：
每個因子在「當天所有股票」中排名 → 0~100 百分位 → 加權平均。
天生連續、天生同日可比、缺資料給中性 50。實測最大並列數 = 1.0。

## 用哪些因子：139 期回測選出來的，不是拍腦袋

測了 22 個模型（趨勢／動能／量能／資券／估值／全因子各種組合），
以 +20／+40／+60 交易日超額報酬驗收，並做滾動 4 折走查。結論：

| 模型 | +60日超額 | t值 | 勝率 |
|---|---|---|---|
| **趨勢(連續) ← 本檔採用** | **+7.18%** | **5.52** | **66%** |
| 純動能 | +6.66% | 4.82 | 60% |
| 12-1動能 | +6.08% | 4.39 | 60% |
| 趨勢+動能+量能 | +5.33% | 3.81 | 54% |
| 趨勢+動能+資券 | +5.16% | 4.11 | 54% |
| 趨勢+資券完整 | +3.31% | 2.88 | 58% |
| 趨勢+估值 | +1.21% | 1.34 | 50% |
| **純低本益比** | **−4.48%** | **−3.82** | 49% |

**加量能、加資券、加估值全部讓成績變差。** 這不是省略，是實測結果——
詳見 BACKTEST_FINDINGS.md「第十三輪」。資券與本益比改用在**風險警示**上
（融資過熱、利多已反映），不進排序訊號。

⚠️ 走查另一個結論：**「看最近哪個模型強就換哪個」會賠錢**——
滾動走查換模型 +5.97%，同期間從頭到尾固定用趨勢分 +8.16%。不要做模型切換。
"""

import json
from pathlib import Path

import numpy as np

DIST_PATH = Path(__file__).resolve().parent.parent / "market_distribution.json"
BUCKET_PATH = Path(__file__).resolve().parent.parent / "trend_score_thresholds.json"

# 買進線 —— **全站唯一定義**。先前 app.py 有一個常數、strategies.py 的說明文字
# 另外寫死一個數字，改了常數卻沒改文字，畫面就出現「門檻 ≥70」但實際用 50。
# 依 trend_score_buckets.py 實測（139期）：0–40 分超額穩定為負（−0.79% ~ −1.60%），
# 40–50 約為零（+0.04%, t=0.2，與 0 無異），50 以上才明確轉正（t≥2.1）。
# 取 50 而非轉正點 40：轉正那一格 t 值太低，拿它當門檻是把雜訊當訊號。
BUY_BAR = 50.0

# 回測選出的權重。改這裡等於改掉全站排序，動之前請先用
# factor_model_research.py 驗證，並確認走查前後段都不變差。
FACTOR_WEIGHTS = {
    "dist_ma120": 1.0,        # 收盤價高出季線幾 %
    "ma60_vs_ma120": 1.0,     # 季線之上的中期均線乖離
    "ma120_slope": 1.0,       # 季線 20 日斜率
}

FACTOR_LABELS = {
    "dist_ma120": "距季線幅度",
    "ma60_vs_ma120": "中長期均線排列",
    "ma120_slope": "季線斜率",
}


def _safe(x):
    try:
        v = float(x)
        return v if v == v else None
    except Exception:
        return None


def raw_factors(df):
    """
    由 K 線（需含 MA60/MA120）算出各因子原始值。與回測用的完全同一套定義——
    回測算一套、App 算另一套，是這個專案已經踩過的坑。
    """
    if df is None or df.empty:
        return {}
    close = _safe(df["Close"].iloc[-1])
    ma120 = _safe(df["MA120"].iloc[-1]) if "MA120" in df else None
    ma60 = _safe(df["MA60"].iloc[-1]) if "MA60" in df else None
    prev120 = (_safe(df["MA120"].iloc[-21])
               if "MA120" in df and len(df) > 21 else None)
    return {
        "dist_ma120": (close / ma120 - 1) * 100 if (close and ma120) else None,
        "ma60_vs_ma120": (ma60 / ma120 - 1) * 100 if (ma60 and ma120) else None,
        "ma120_slope": (ma120 / prev120 - 1) * 100 if (ma120 and prev120) else None,
    }


def pct_rank_column(values):
    """
    一整欄的橫斷面百分位（0~100）。None 給中性 50，不參與排名。

    用排名而非 z 分數：台股因子分布厚尾（漲停股、雞蛋水餃股），
    z 分數會被離群值主宰，排名不會。
    """
    idx = [i for i, v in enumerate(values) if v is not None]
    out = [50.0] * len(values)
    if len(idx) < 5:
        return out
    vals = np.array([values[i] for i in idx], dtype=float)
    scaled = vals.argsort().argsort() / max(len(vals) - 1, 1) * 100
    for k, i in enumerate(idx):
        out[i] = float(scaled[k])
    return out


def score_cross_section(rows_factors):
    """
    一次算一整批（智能選股用）—— 百分位直接由當批股票算出，最精確。

    rows_factors: [{factor: value}] → 回傳 [{score, percentiles}]
    """
    n = len(rows_factors)
    if not n:
        return []
    ranks = {f: pct_rank_column([r.get(f) for r in rows_factors])
             for f in FACTOR_WEIGHTS}
    total_w = sum(FACTOR_WEIGHTS.values())
    out = []
    for i in range(n):
        s = sum(ranks[f][i] * w for f, w in FACTOR_WEIGHTS.items()) / total_w
        out.append({
            "score": round(float(s), 1),
            "percentiles": {f: round(ranks[f][i], 1) for f in FACTOR_WEIGHTS},
        })
    return out


# ── 單檔評分：對照「上次全市場掃描」的分布 ────────────────────────────────
# 個股分析與我的持股一次只看幾檔，沒有當日橫斷面可用。若各自用手邊那幾檔
# 算百分位，同一檔股票在不同頁會得到不同分數——這正是本專案反覆出過的錯。
# 因此固定對照一份**全市場分布快照**，三頁共用同一把尺。

def save_distribution(rows_factors):
    """全市場掃描完呼叫，把各因子的分布落地，供單檔頁面對照。"""
    dist = {}
    for f in FACTOR_WEIGHTS:
        vals = sorted(v for v in (r.get(f) for r in rows_factors) if v is not None)
        if len(vals) >= 50:
            # 存 101 個分位點就夠還原百分位，不必存整份原始資料
            dist[f] = [float(np.percentile(vals, p)) for p in range(101)]
    if dist:
        try:
            DIST_PATH.write_text(json.dumps(
                {"factors": dist, "n": len(rows_factors)}))
        except Exception:
            pass
    return dist


_dist_cache = None


def load_distribution():
    global _dist_cache
    if _dist_cache is None:
        try:
            _dist_cache = json.loads(DIST_PATH.read_text())
        except Exception:
            _dist_cache = {}
    return _dist_cache


def _pct_of(breaks, v):
    """v 落在分位點陣列的第幾百分位。"""
    if v is None or not breaks:
        return 50.0
    return float(np.searchsorted(breaks, v, side="right"))


def score_single(df):
    """
    單檔評分（個股分析／我的持股用）。對照最近一次全市場掃描的分布。

    回傳 {score, percentiles, factors, stale}。
    stale=True 代表還沒跑過全市場掃描，分數只能給中性 50——
    寧可明講也不要拿手邊 9 檔股票算百分位、給出一個假的排名。
    """
    raw = raw_factors(df)
    dist = load_distribution().get("factors") or {}
    if not dist:
        return {"score": None, "percentiles": {}, "factors": raw, "stale": True}
    pcts = {f: _pct_of(dist.get(f), raw.get(f)) for f in FACTOR_WEIGHTS}
    total_w = sum(FACTOR_WEIGHTS.values())
    s = sum(pcts[f] * w for f, w in FACTOR_WEIGHTS.items()) / total_w
    return {"score": round(float(s), 1),
            "percentiles": {f: round(v, 1) for f, v in pcts.items()},
            "factors": raw, "stale": False}


def explain(percentiles):
    """把百分位翻成人話，讓使用者知道分數是怎麼來的。"""
    out = []
    for f, label in FACTOR_LABELS.items():
        p = percentiles.get(f)
        if p is None:
            continue
        tag = ("領先全市場" if p >= 80 else "中上" if p >= 60
               else "中等" if p >= 40 else "落後" if p >= 20 else "明顯落後")
        out.append(f"{label} 贏過 {p:.0f}% 的股票（{tag}）")
    return out
