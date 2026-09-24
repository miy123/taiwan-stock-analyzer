"""
回測統計的**唯一實作** —— 五支回測腳本一律 import 這裡，不要各寫一份。

為什麼要抽出來：`_t()` 曾經在 `strategy_comparison.py` / `backtest_research.py` /
`trend_score_buckets.py` / `score_threshold_analysis.py` / `factor_model_research.py`
裡**各有一份**完全相同的程式碼。2026-09-24 發現那個算法對重疊視窗會系統性高估
t 值時，等於要在五個地方各修一次——漲停的定義就是這樣漂移掉的
（見 CLAUDE.md 第五輪稽核）。

同樣地，「每期的可投資範圍」也在五個檔案裡各寫一份
`sorted(snap, key=turnover)[:N]`，而那一行正是前視偏誤的來源。
`pit_universe()` 是它的替代品。
"""

import math

import numpy as np


def t_stat(xs):
    """
    一般 t 值 —— **只在視窗不重疊時可以照字面解讀**。

    ⚠️ 回測通常每 `every` 個交易日換一次股卻持有 `h` 天，相鄰 `h/every` 期的
    持有區間大幅重疊，觀測**不是獨立樣本**。對外報告一律用 `t_newey_west()`。
    保留這個函式只是為了把未修正的數字存成 `t_raw` 供對照。
    """
    if len(xs) < 3:
        return 0.0
    m, sd = float(np.mean(xs)), float(np.std(xs, ddof=1))
    return m / (sd / math.sqrt(len(xs))) if sd else 0.0


def t_newey_west(xs, lag):
    """
    Newey-West t 值（Bartlett kernel）—— 重疊視窗的標準修正。

    `lag` 取 `持有天數 / 換股間隔 − 1`：那正是還會與當期重疊的期別數
    （每 5 天換股、持有 60 天 → lag=11）。自我相關被算進標準誤，
    t 因此比 `t_stat()` 小，那才是誠實的數字。

    **平均值是無偏的**，重疊不影響它；被高估的只有顯著性。

    實測（白噪音做 12 期移動平均、真實訊號為 0）：
    `t_stat` 0.69 ／ `t_newey_west` 0.26 ／不重疊子樣本中位 0.20。
    """
    n = len(xs)
    if n < 3:
        return 0.0
    x = np.asarray(xs, dtype=float)
    e = x - x.mean()
    var = float(e @ e) / n
    for l in range(1, min(int(lag), n - 1) + 1):
        w = 1.0 - l / (lag + 1.0)
        var += 2.0 * w * float(e[l:] @ e[:-l]) / n
    if var <= 0:
        return 0.0
    return float(x.mean()) / math.sqrt(var / n)


def nonoverlap(xs, stride):
    """
    完全不重疊的子樣本檢驗 —— Newey-West 之外的第二道佐證。

    每隔 `stride` 期取一個（stride = 持有天數 / 換股間隔）得到互不重疊的序列；
    `stride` 種起始位移各是一個獨立子樣本，**全部都算**再報 t 的中位與範圍。
    只報其中一組等於挑對自己有利的那一條。

    回傳 {n, subsamples, t_min, t_med, t_max}；平均值與全樣本相同，不重複回傳。
    """
    stride = max(1, int(stride))
    ts, ns = [], []
    for off in range(stride):
        sub = xs[off::stride]
        if len(sub) < 3:
            continue
        ts.append(t_stat(sub))
        ns.append(len(sub))
    if not ts:
        return {}
    ts.sort()
    return {"n": int(np.median(ns)), "subsamples": len(ts),
            "t_min": round(ts[0], 2),
            "t_med": round(float(np.median(ts)), 2),
            "t_max": round(ts[-1], 2)}


def pit_universe(enriched, date, min_history, min_turnover, top_n):
    """
    **當日**的可投資範圍 —— 回傳 [(turnover, code, pos), ...]，已依成交金額排序。

    ⚠️ 這是用來取代 `sorted(snap, key=今天的turnover)[:N]` 的。那一行是直接與
    報酬相關的前視偏誤：2023 年冷門、後來才變熱門的股票會被放進池子，
    而「後來變熱門」常常就是因為它漲了很多。

    改成每個換股日各自用**當日**往前 20 日的平均成交金額（Vol_MA20 × 收盤價）
    排序取前 `top_n` 檔。下載的池子可以開到全部上市，反正這裡會再篩一次。

    `pos` 是該檔在自己 DataFrame 裡對應這一天的列號，呼叫端直接用，
    不必再 searchsorted 一次。
    """
    cand = []
    for code, d in enriched.items():
        pos = d.index.searchsorted(date, side="right") - 1
        if pos < min_history or pos >= len(d):
            continue
        # 超過 7 天代表這檔當時還沒上市／已停牌，不能算進當日範圍
        if abs((d.index[pos] - date).days) > 7:
            continue
        v20 = d["Vol_MA20"].iloc[pos] if "Vol_MA20" in d else None
        if v20 != v20 or not v20:
            continue
        to = float(d["Close"].iloc[pos]) * float(v20)
        if to < min_turnover:
            continue
        cand.append((to, code, pos))
    cand.sort(reverse=True)
    return cand[:top_n] if top_n and top_n > 0 else cand


def listed_pool(pool=0):
    """
    下載用的候選池。`pool<=0` ＝全部上市。

    ⚠️ 這一步仍然用今天的快照（拿不到歷史上市清單），所以**池子開越大、
    前視偏誤越小**；真正決定每期可投資範圍的是 `pit_universe()`。
    生存者偏誤修不掉——已下市的公司不在今天的快照裡，回測數字因此偏樂觀。
    """
    from services.universe import get_listed_snapshot
    snap = get_listed_snapshot()
    ranked = [c for c, _ in sorted(snap.items(),
                                   key=lambda kv: -(kv[1].get("turnover") or 0))]
    return ranked if pool <= 0 else ranked[:pool]
