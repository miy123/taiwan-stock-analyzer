"""
驗證「半年漲 >60% 只給 +4」這個扣分該不該拿掉。

背景（score_value_analysis.py 的發現）：
  94 = …… + 10（半年漲 10~60%）
  88 = …… +  4（半年漲 >60%，「漲幅已大」扣 6 分）
  配對比較 139 期：3個月超額 88 是 +6.43%、94 是 +2.07%，差 -4.36%，t=-3.33。
  **扣分方向是反的** —— 漲最兇的那群後續反而賺最多。

但「單一分數桶的平均」不等於「照策略選前 10 名的績效」，因為：
  · 現行排序是 (長線分, 距季線幅度)，94 那群有 156 檔，前 10 名永遠抽不到 88 那群
  · 88 群勝率較低（61% vs 70%），平均賺較多但比較顛簸

所以這裡直接比 **實際選股結果**：同樣選前 10 名，只差計分公式。
  A 現行     ：>60% 給 +4
  B 取消扣分 ：>60% 也給 +10（88 群併入 94 群）
  C 反向加碼 ：>60% 給 +14（明確偏好強動能）

方法與 backtest_research.py 一致：同一次執行內比較、同日等權基準算超額、
以「換股日」為觀測單位算 t 值。
"""

import argparse
import math
from collections import defaultdict

import numpy as np
import pandas as pd

from services.universe import get_listed_snapshot, download_history_bulk
from services.technical import (
    calculate_indicators, calculate_horizon_scores, analyze_volume_price,
)

MIN_HISTORY = 260
FWD = [20, 60]
VARIANTS = {"A 現行(+4)": 4, "B 取消扣分(+10)": 10, "C 反向加碼(+14)": 14}


def _pct(c, n):
    if len(c) <= n:
        return None
    p = float(c.iloc[-n - 1])
    return ((float(c.iloc[-1]) / p - 1) * 100) if p else None


def long_score_variant(sl, runup_bonus):
    """複製 technical.calculate_horizon_scores 的長線分，但『漲幅已大』獎懲可調。"""
    lg = 50
    c = sl["Close"]
    close = float(c.iloc[-1])
    ma120 = sl["MA120"].iloc[-1]
    ma60 = sl["MA60"].iloc[-1]
    if ma120 == ma120:
        lg += 14 if close > float(ma120) else -14
        if ma60 == ma60:
            lg += 12 if float(ma60) > float(ma120) else -12
        prev = sl["MA120"].iloc[-21] if len(sl) > 21 else None
        if prev is not None and prev == prev:
            lg += 8 if float(ma120) > float(prev) else -8
    r120 = _pct(c, 120)
    if r120 is not None:
        if r120 > 60:
            lg += runup_bonus
        elif r120 > 10:
            lg += 10
        elif r120 < -15:
            lg -= 10
    return max(0, min(100, int(lg)))


def _t(xs):
    if len(xs) < 3:
        return 0.0
    m, sd = float(np.mean(xs)), float(np.std(xs, ddof=1))
    return m / (sd / math.sqrt(len(xs))) if sd else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=400)
    ap.add_argument("--every", type=int, default=5)
    ap.add_argument("--years", type=float, default=3.0)
    ap.add_argument("--topn", type=int, default=10)
    args = ap.parse_args()

    print(f"設定: 前{args.stocks}檔 / 每{args.every}日換股 / 近{args.years}年 / 選前{args.topn}名\n")
    snap = get_listed_snapshot()
    ranked = sorted(snap.items(), key=lambda kv: -(kv[1].get("turnover") or 0))
    frames = download_history_bulk([c for c, _ in ranked[:args.stocks]],
                                   period="5y", chunk=120)
    enriched = {}
    for c, d in frames.items():
        if len(d) >= MIN_HISTORY + max(FWD):
            try:
                enriched[c] = calculate_indicators(d)
            except Exception:
                pass
    print(f"可用 {len(enriched)} 檔")

    common = None
    for d in enriched.values():
        common = d.index if common is None else common.union(d.index)
    common = common.sort_values()
    start_i = max(MIN_HISTORY, len(common) - int(args.years * 252))
    end_i = len(common) - max(FWD) - 1
    dates = list(range(start_i, end_i, args.every))
    print(f"換股日 {len(dates)} 個（{common[start_i].date()} ~ {common[end_i].date()}）\n")

    # res[variant][hold] = [每個換股日的超額報酬]
    res = {v: {h: [] for h in FWD} for v in VARIANTS}
    # 同時記錄「有量價確認」版本（bestproven 策略）
    res_bp = {v: {h: [] for h in FWD} for v in VARIANTS}

    for k, di in enumerate(dates):
        date = common[di]
        if k % 20 == 0:
            print(f"  {k}/{len(dates)}  {date.date()}")
        day = []
        for code, d in enriched.items():
            pos = d.index.searchsorted(date, side="right") - 1
            if pos < MIN_HISTORY or pos >= len(d):
                continue
            if abs((d.index[pos] - date).days) > 7:
                continue
            sl = d.iloc[:pos + 1]
            try:
                ma = sl["MA120"].iloc[-1]
                above = ((float(sl["Close"].iloc[-1]) / float(ma) - 1) * 100
                         if ma == ma and float(ma) > 0 else -999)
                v_adj = analyze_volume_price(sl).get("score_adj", 0)
                scores = {v: long_score_variant(sl, b) for v, b in VARIANTS.items()}
            except Exception:
                continue
            fwd = {}
            ok = True
            for h in FWD:
                if pos + h >= len(d):
                    ok = False
                    break
                a, b = float(d["Close"].iloc[pos]), float(d["Close"].iloc[pos + h])
                fwd[h] = (b / a - 1) * 100 if a else None
                if fwd[h] is None:
                    ok = False
            if not ok:
                continue
            day.append({"scores": scores, "above": above, "v": v_adj, "fwd": fwd})

        if len(day) < 30:
            continue
        bench = {h: float(np.mean([x["fwd"][h] for x in day])) for h in FWD}
        for v in VARIANTS:
            picks = sorted(day, key=lambda x: (x["scores"][v], x["above"]),
                           reverse=True)[:args.topn]
            for h in FWD:
                res[v][h].append(float(np.mean([p["fwd"][h] for p in picks])) - bench[h])
            bp = [x for x in day if x["v"] >= 0]
            if len(bp) >= args.topn:
                picks_bp = sorted(bp, key=lambda x: (x["scores"][v], x["above"]),
                                  reverse=True)[:args.topn]
                for h in FWD:
                    res_bp[v][h].append(
                        float(np.mean([p["fwd"][h] for p in picks_bp])) - bench[h])

    for title, R in (("純長線分排序", res), ("長線分 + 量價未轉弱（bestproven）", res_bp)):
        print("\n" + "=" * 74)
        print(f"{title} —— 選前 {args.topn} 名的超額報酬")
        print("=" * 74)
        print(f"{'公式':<18} {'持有':>5} {'期數':>5} {'超額報酬':>9} {'t值':>7} {'贏基準率':>8}")
        for v in VARIANTS:
            for h in FWD:
                xs = R[v][h]
                if not xs:
                    continue
                win = 100 * sum(1 for x in xs if x > 0) / len(xs)
                print(f"{v:<18} {h:>4}日 {len(xs):>5} {np.mean(xs):>+8.2f}% "
                      f"{_t(xs):>7.2f} {win:>7.1f}%")
        # 配對檢定：B/C 相對 A 是否顯著更好
        for v in ("B 取消扣分(+10)", "C 反向加碼(+14)"):
            for h in FWD:
                a, b = R["A 現行(+4)"][h], R[v][h]
                if len(a) != len(b) or not a:
                    continue
                d = [y - x for x, y in zip(a, b)]
                t = _t(d)
                verd = ("顯著較優 ✅" if t > 1.96 else
                        "顯著較差 ❌" if t < -1.96 else "無顯著差異")
                print(f"   {v} vs A，持有{h}日：{np.mean(d):+.2f}%  t={t:+.2f}  {verd}")

        # ── 走查：前半段挑到的結論，後半段是否還成立？────────────────────
        # CLAUDE.md 的規矩：不能用全期資料挑模型再用全期驗證。
        print(f"\n  【走查】{title} —— 前半段 vs 後半段各自獨立檢定")
        for v in ("B 取消扣分(+10)", "C 反向加碼(+14)"):
            for h in FWD:
                a, b = R["A 現行(+4)"][h], R[v][h]
                if len(a) != len(b) or len(a) < 40:
                    continue
                mid = len(a) // 2
                out = []
                for tag, sl in (("前半", slice(0, mid)), ("後半", slice(mid, None))):
                    d = [y - x for x, y in zip(a[sl], b[sl])]
                    t = _t(d)
                    mark = "✅" if t > 1.96 else "❌" if t < -1.96 else "—"
                    out.append(f"{tag} {np.mean(d):+.2f}% (t={t:+.2f}){mark}")
                print(f"    {v} 持有{h}日： " + "　｜　".join(out))


if __name__ == "__main__":
    main()
