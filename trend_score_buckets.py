"""
連續趨勢結構分的「分數區間 → 後續表現」。

為什麼要單獨測：
  舊的 70 分門檻是在**離散長線分**上量出來的，那是完全不同的量表
  （離散分最高 94、有 49% 並列；連續分是百分位、0~100 連續）。
  直接把 70 沿用過來只是「數字剛好一樣」，不是證據。持股頁上那段
  「70 分門檻怎麼來的」引用的就是舊表——本檔就是要把它換成新分數自己的數字。

也順便回答「分數越高是不是真的越好」：對每個十分位算後續超額報酬，
看單調性，而不是只看前 10 名。
"""

import argparse
import json
import math
from collections import defaultdict

import numpy as np

from services.universe import get_listed_snapshot, download_history_bulk
from services.technical import calculate_indicators
from services.scoring import raw_factors, pct_rank_column, FACTOR_WEIGHTS

MIN_HISTORY = 260
FWD = [20, 40, 60]


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
    ap.add_argument("--min-turnover", type=float, default=1e7)
    args = ap.parse_args()

    snap = get_listed_snapshot()
    codes = [c for c, _ in sorted(snap.items(),
                                  key=lambda kv: -(kv[1].get("turnover") or 0))
             ][:args.stocks]
    frames = download_history_bulk(codes, period="5y", chunk=120)
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
    print(f"取樣日 {len(dates)} 個（{common[start_i].date()} ~ {common[end_i].date()}）\n")

    BUCKETS = [(0, 10), (10, 20), (20, 30), (30, 40), (40, 50),
               (50, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
    # obs[hold][bucket][date] = 當日該區間的平均超額；先日內平均再跨日統計，
    # 否則同一天幾百檔高度相關的觀測會被當成獨立樣本，t 值虛胖。
    obs = {h: defaultdict(dict) for h in FWD}
    absr = {h: defaultdict(dict) for h in FWD}

    for k, di in enumerate(dates):
        date = common[di]
        if k % 20 == 0:
            print(f"  {k}/{len(dates)}  {date.date()}", flush=True)
        day = []
        for code, d in enriched.items():
            pos = d.index.searchsorted(date, side="right") - 1
            if pos < MIN_HISTORY or pos >= len(d):
                continue
            if abs((d.index[pos] - date).days) > 7:
                continue
            sl = d.iloc[:pos + 1]
            close = float(sl["Close"].iloc[-1])
            v20 = sl["Vol_MA20"].iloc[-1] if "Vol_MA20" in sl else None
            if v20 != v20 or not v20 or close * float(v20) < args.min_turnover:
                continue
            fwd, ok = {}, True
            for h in FWD:
                if pos + h >= len(d):
                    ok = False
                    break
                fwd[h] = (float(d["Close"].iloc[pos + h]) / close - 1) * 100
            if not ok:
                continue
            day.append({"f": raw_factors(sl), "fwd": fwd})

        if len(day) < 50:
            continue
        ranks = {f: pct_rank_column([x["f"].get(f) for x in day])
                 for f in FACTOR_WEIGHTS}
        tw = sum(FACTOR_WEIGHTS.values())
        scores = [sum(ranks[f][i] * w for f, w in FACTOR_WEIGHTS.items()) / tw
                  for i in range(len(day))]
        bench = {h: float(np.mean([x["fwd"][h] for x in day])) for h in FWD}

        per = {h: defaultdict(list) for h in FWD}
        pera = {h: defaultdict(list) for h in FWD}
        for i, x in enumerate(day):
            b = next((bb for bb in BUCKETS if bb[0] <= scores[i] < bb[1]), None)
            if not b:
                continue
            for h in FWD:
                per[h][b].append(x["fwd"][h] - bench[h])
                pera[h][b].append(x["fwd"][h])
        for h in FWD:
            for b, xs in per[h].items():
                obs[h][b][date] = float(np.mean(xs))
            for b, xs in pera[h].items():
                absr[h][b][date] = float(np.mean(xs))

    print("\n" + "=" * 88)
    print("連續趨勢結構分：分數區間 → 後續表現（超額 = 減去當日全市場等權平均）")
    print("=" * 88)
    for h in FWD:
        lab = {20: "1個月", 40: "2個月", 60: "3個月"}[h]
        print(f"\n── 持有 {h} 交易日（約{lab}）" + "─" * 50)
        print(f"{'分數區間':>10} {'期數':>6} {'絕對報酬':>9} {'超額報酬':>9} {'t值':>7} "
              f"{'贏大盤期數比':>11}")
        for b in BUCKETS:
            series = list(obs[h][b].values())
            if len(series) < 20:
                continue
            a = list(absr[h][b].values())
            win = 100 * sum(1 for x in series if x > 0) / len(series)
            mark = "  ⬅ 買進線" if b[0] == 70 else ""
            print(f"{b[0]:>4}-{b[1] if b[1] <= 100 else 100:<5} {len(series):>6} "
                  f"{np.mean(a):>+8.2f}% {np.mean(series):>+8.2f}% {_t(series):>7.2f} "
                  f"{win:>10.0f}%{mark}")
        # 單調性
        pts = [(b[0], float(np.mean(list(obs[h][b].values()))))
               for b in BUCKETS if len(obs[h][b]) >= 20]
        if len(pts) >= 5:
            xs = np.argsort(np.argsort([p[0] for p in pts]))
            ys = np.argsort(np.argsort([p[1] for p in pts]))
            print(f"  單調性 Spearman ρ = {float(np.corrcoef(xs, ys)[0, 1]):+.3f}")
        # 轉正門檻
        pos = [b[0] for b in BUCKETS
               if len(obs[h][b]) >= 20 and np.mean(list(obs[h][b].values())) > 0]
        if pos:
            print(f"  超額報酬轉正的最低區間：{min(pos)} 分以上")

    # 落地供 App 引用 —— UI 不該再寫死任何分桶數字（寫死的那份已經過期兩次了）
    out = {"periods": len(dates), "buckets": {}}
    for h in FWD:
        rows = []
        for b in BUCKETS:
            series = list(obs[h][b].values())
            if len(series) < 20:
                continue
            rows.append({
                "range": f"{b[0]}-{min(b[1], 100)}", "lo": b[0], "hi": min(b[1], 100),
                "excess": round(float(np.mean(series)), 2),
                "abs_return": round(float(np.mean(list(absr[h][b].values()))), 2),
                "t": round(_t(series), 2),
                "beat_rate": round(100 * sum(1 for x in series if x > 0) / len(series), 1),
                "periods": len(series),
            })
        pts = [(r["lo"], r["excess"]) for r in rows]
        rho = None
        if len(pts) >= 5:
            xs = np.argsort(np.argsort([q[0] for q in pts]))
            ys = np.argsort(np.argsort([q[1] for q in pts]))
            rho = round(float(np.corrcoef(xs, ys)[0, 1]), 3)
        first_pos = next((r["lo"] for r in rows if r["excess"] > 0), None)
        out["buckets"][str(h)] = {"rows": rows, "spearman": rho,
                                  "turns_positive_at": first_pos}
    with open("trend_score_thresholds.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n已存出 trend_score_thresholds.json")


if __name__ == "__main__":
    main()
