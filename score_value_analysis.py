"""
長線分「每一個分數值」的後續表現 —— 回答兩個問題：

  Q1 為什麼分數卡在 94、88 這些值？
     → 長線分 = 50 + 四個離散跳點，全部組合只有 40 種可能值，
       89~93 沒有任何組合到得了。94 與 88 只差最後一項（半年報酬 +10 vs +4）。

  Q2 分數越高，勝率真的越高嗎？特別是「94 真的贏 88 嗎」？
     → 88 的意思是「所有結構條件都滿足，但半年已漲超過 60%」，
       模型為此扣了 6 分。這個扣分到底是對的還是反了？本檔就是要測這件事。

方法（與 backtest_research.py 一致，避免前視偏誤）：
  · 只用「截至當日」的切片算分數
  · 超額報酬 = 個股報酬 − 當日全體等權平均（多頭時人人都漲，絕對報酬會騙人）
  · **先把同一天同分數的股票平均成一個觀測值**，再對「日期序列」算 t 值。
    不這樣做會把 5 萬筆高度相關的觀測當成獨立樣本，t 值會虛胖十幾倍。
"""

import argparse
import math
from collections import defaultdict

import numpy as np

from services.universe import get_listed_snapshot, download_history_bulk
from services.technical import calculate_indicators, calculate_horizon_scores

MIN_HISTORY = 260
FWD = [20, 60]


def _fwd(close, i, n):
    if i + n >= len(close):
        return None
    a, b = float(close.iloc[i]), float(close.iloc[i + n])
    return (b / a - 1) * 100 if a else None


def _tstat(xs):
    if len(xs) < 3:
        return 0.0
    m, sd = float(np.mean(xs)), float(np.std(xs, ddof=1))
    return m / (sd / math.sqrt(len(xs))) if sd else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=400)
    ap.add_argument("--every", type=int, default=5)
    ap.add_argument("--years", type=float, default=3.0)
    args = ap.parse_args()

    print(f"設定: 前{args.stocks}檔流動股 / 每{args.every}交易日取樣 / 近{args.years}年\n")
    print("① 下載歷史資料…")
    snap = get_listed_snapshot()
    ranked = sorted(snap.items(), key=lambda kv: -(kv[1].get("turnover") or 0))
    codes = [c for c, _ in ranked[:args.stocks]]
    frames = download_history_bulk(codes, period="5y", chunk=120)

    enriched = {}
    for c, d in frames.items():
        if len(d) >= MIN_HISTORY + max(FWD):
            try:
                enriched[c] = calculate_indicators(d)
            except Exception:
                pass
    print(f"   {len(enriched)} 檔可用")

    common = None
    for d in enriched.values():
        common = d.index if common is None else common.union(d.index)
    common = common.sort_values()
    start_i = max(MIN_HISTORY, len(common) - int(args.years * 252))
    end_i = len(common) - max(FWD) - 1
    dates = list(range(start_i, end_i, args.every))
    print(f"② 取樣日 {len(dates)} 個（{common[start_i].date()} ~ {common[end_i].date()}）\n")

    # obs[hold][score][date] = [超額報酬, ...]
    obs = {h: defaultdict(lambda: defaultdict(list)) for h in FWD}
    # 額外記錄 88 vs 94 這兩群的半年報酬，確認它們真的只差在漲幅
    r120_by_score = defaultdict(list)

    print("③ 逐日計分…")
    for k, di in enumerate(dates):
        date = common[di]
        if k % 20 == 0:
            print(f"   {k}/{len(dates)}  {date.date()}")
        day = []
        for code, d in enriched.items():
            pos = d.index.searchsorted(date, side="right") - 1
            if pos < MIN_HISTORY or pos >= len(d):
                continue
            if abs((d.index[pos] - date).days) > 7:
                continue
            sl = d.iloc[:pos + 1]
            try:
                sc = calculate_horizon_scores(sl)["long"]["score"]
            except Exception:
                continue
            fwd = {h: _fwd(d["Close"], pos, h) for h in FWD}
            if any(v is None for v in fwd.values()):
                continue
            c = sl["Close"]
            r120 = ((float(c.iloc[-1]) / float(c.iloc[-121]) - 1) * 100
                    if len(c) > 121 else None)
            day.append((sc, fwd, r120))

        if len(day) < 30:
            continue
        # 當日等權基準：超額 = 個股 − 全體平均
        bench = {h: float(np.mean([x[1][h] for x in day])) for h in FWD}
        per_score = {h: defaultdict(list) for h in FWD}
        for sc, fwd, r120 in day:
            for h in FWD:
                per_score[h][sc].append(fwd[h] - bench[h])
            if r120 is not None:
                r120_by_score[sc].append(r120)
        # 同日同分先平均 → 一個日期一個觀測值
        for h in FWD:
            for sc, xs in per_score[h].items():
                obs[h][sc][date] = float(np.mean(xs))

    print("\n" + "=" * 78)
    print("長線分「每個分數值」的後續超額報酬（vs 當日全市場等權平均）")
    print("=" * 78)
    for h in FWD:
        label = "1個月" if h == 20 else "3個月"
        print(f"\n── 持有 {h} 交易日（約{label}）" + "─" * 45)
        print(f"{'分數':>5} {'出現期數':>7} {'平均檔數':>7} {'超額報酬':>9} {'t值':>7} "
              f"{'贏基準率':>8}  {'半年報酬中位':>11}")
        rows = []
        for sc in sorted(obs[h].keys()):
            series = list(obs[h][sc].values())
            if len(series) < 20:
                continue
            n_stocks = np.mean([len(v) for v in [series]])  # placeholder
            mean = float(np.mean(series))
            win = 100 * sum(1 for x in series if x > 0) / len(series)
            r120m = (float(np.median(r120_by_score[sc]))
                     if r120_by_score.get(sc) else float("nan"))
            rows.append((sc, len(series), mean, _tstat(series), win, r120m))
        for sc, n, mean, t, win, r120m in rows:
            star = " ⬅" if sc in (88, 94) else ""
            print(f"{sc:>5} {n:>7} {'':>7} {mean:>+8.2f}% {t:>7.2f} {win:>7.1f}% "
                  f"{r120m:>10.0f}%{star}")

    # ── 核心對照：94 vs 88（同結構，只差「半年是否漲超過 60%」）────────────
    print("\n" + "=" * 78)
    print("關鍵對照：94 vs 88 —— 兩者結構條件完全相同，只差半年報酬是否 >60%")
    print("=" * 78)
    for h in FWD:
        s94 = obs[h].get(94, {})
        s88 = obs[h].get(88, {})
        both = sorted(set(s94) & set(s88))     # 同一天都有出現，才是公平比較
        if len(both) < 20:
            print(f"  {h}日：共同期數不足（{len(both)}），略過")
            continue
        d = [s94[dt] - s88[dt] for dt in both]
        a94 = float(np.mean([s94[dt] for dt in both]))
        a88 = float(np.mean([s88[dt] for dt in both]))
        t = _tstat(d)
        win = 100 * sum(1 for x in d if x > 0) / len(d)
        verdict = ("94 顯著較優" if t > 1.96 else
                   "88 顯著較優" if t < -1.96 else "**沒有顯著差異**")
        print(f"\n  持有 {h} 日（同時出現的 {len(both)} 期配對比較）")
        print(f"    分數 94 超額 {a94:+.2f}%   分數 88 超額 {a88:+.2f}%")
        print(f"    差值 {np.mean(d):+.2f}%   t={t:+.2f}   94 贏過 88 的期數比例 {win:.1f}%")
        print(f"    → {verdict}")

    # ── 單調性檢定：分數越高，超額報酬真的越高嗎？────────────────────────
    print("\n" + "=" * 78)
    print("單調性：分數與後續超額報酬的相關性（Spearman 等級相關）")
    print("=" * 78)
    for h in FWD:
        pts = [(sc, float(np.mean(list(v.values()))))
               for sc, v in obs[h].items() if len(v) >= 20]
        if len(pts) < 5:
            continue
        pts.sort()
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        rx = np.argsort(np.argsort(xs))
        ry = np.argsort(np.argsort(ys))
        rho = float(np.corrcoef(rx, ry)[0, 1])
        print(f"  持有 {h} 日：ρ = {rho:+.3f}（{len(pts)} 個分數值）"
              f"　{'✅ 單調性良好' if rho > 0.6 else '🟡 部分單調' if rho > 0.3 else '❌ 幾乎無單調性'}")


if __name__ == "__main__":
    main()
