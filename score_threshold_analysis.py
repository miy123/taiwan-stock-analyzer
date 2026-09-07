"""
分數門檻分析 —— 「幾分以上才值得買？幾分以下別碰？」

前面的回測都是「排名取前N名」，但實務上你看到的是一個**分數**。本分析改為
依分數分桶（不是排名），回答：
  · 某個分數區間的歷史勝率與報酬是多少
  · 分數低到哪裡開始，超額報酬轉負 → 建議別買
  · 同一個模型，持有多久勝率最高

用法:
    python3 score_threshold_analysis.py [--stocks N] [--every N] [--years N]
"""

import argparse
import statistics as stat
import sys

import yfinance as yf

sys.path.insert(0, ".")

from services.technical import (
    calculate_indicators, calculate_technical_score, calculate_horizon_scores,
    analyze_volume_price,
)
from services.potential import calculate_potential_score
from services.universe import get_listed_snapshot, download_history_bulk

FWD = (5, 20, 60)
HZ_NAME = {5: "1週", 20: "1個月", 60: "3個月"}
MIN_HISTORY = 260
ROUND_TRIP_COST = 0.585

# 分數桶邊界（左閉右開）
BUCKETS = [(0, 35), (35, 45), (45, 50), (50, 55), (55, 60),
           (60, 65), (65, 70), (70, 75), (75, 80), (80, 101)]

# 要分析的分數類型： key → (取值函式, 說明)
SCORE_TYPES = {
    "長線+量能確認": (lambda s: s["h_long"] if s["vol_adj"] >= 0 else None,
                  "長線結構分（僅取量價未轉弱者）"),
    "長線結構分": (lambda s: s["h_long"], "四週期中的長線分"),
    "中線分": (lambda s: s["h_medium"], "四週期中的中線分"),
    "短線分": (lambda s: s["h_short"], "四週期中的短線分"),
    "極短線分": (lambda s: s["h_ultra"], "四週期中的極短線分"),
    "綜合技術分": (lambda s: s["tech"], "calculate_technical_score"),
    "潛力分": (lambda s: s["pot"], "低基期為主（話題/前瞻中性）"),
    "低基期分": (lambda s: s["low_base"], "純粹『還沒漲』的程度"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=450)
    ap.add_argument("--every", type=int, default=5)
    ap.add_argument("--years", type=float, default=5.0)
    args = ap.parse_args()

    print("① 下載資料…")
    snap = get_listed_snapshot()
    ranked = sorted(snap.items(), key=lambda kv: -(kv[1].get("turnover") or 0))
    codes = [c for c, _ in ranked[:args.stocks]]
    frames = download_history_bulk(codes, period="5y", chunk=120)

    print("② 計算指標…")
    enriched = {}
    for c, d in frames.items():
        try:
            if len(d) >= MIN_HISTORY + max(FWD):
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
    rebal = list(range(start_i, end_i, args.every))
    print(f"③ 換股日 {len(rebal)} 個（{common[start_i].date()} ~ {common[end_i].date()}）\n")

    # obs[score_type][bucket_idx][horizon] = list of (ret, excess)
    obs = {k: {i: {h: [] for h in FWD} for i in range(len(BUCKETS))}
           for k in SCORE_TYPES}

    for n, i in enumerate(rebal, 1):
        date = common[i]
        day = []
        for code, d in enriched.items():
            try:
                sl = d.loc[:date]
                if len(sl) < MIN_HISTORY:
                    continue
                if float((sl["Close"] * sl["Volume"]).tail(20).mean()) < 2e7:
                    continue
                base = float(sl["Close"].iloc[-1])
                if base <= 0:
                    continue
                pos = d.index.get_indexer([date], method="ffill")[0]
                fwd, ok = {}, True
                for h in FWD:
                    if pos + h < len(d):
                        fwd[h] = (float(d["Close"].iloc[pos + h]) / base - 1) * 100
                    else:
                        ok = False
                if not ok:
                    continue
                hz = calculate_horizon_scores(sl)
                tech, _ = calculate_technical_score(sl)
                vol = analyze_volume_price(sl)
                pot = calculate_potential_score(
                    sl, {}, {}, 50, {"positive": [], "negative": []}, None)
                day.append({
                    "h_long": hz["long"]["score"], "h_medium": hz["medium"]["score"],
                    "h_short": hz["short"]["score"], "h_ultra": hz["ultra_short"]["score"],
                    "tech": tech, "pot": pot["total"], "low_base": pot["low_base"],
                    "vol_adj": vol.get("score_adj", 0), "fwd": fwd,
                })
            except Exception:
                continue

        if len(day) < 30:
            continue
        bench = {h: stat.mean(x["fwd"][h] for x in day) for h in FWD}
        for st_name, (getter, _) in SCORE_TYPES.items():
            for x in day:
                sc = getter(x)
                if sc is None:
                    continue
                for bi, (lo, hi) in enumerate(BUCKETS):
                    if lo <= sc < hi:
                        for h in FWD:
                            obs[st_name][bi][h].append(
                                (x["fwd"][h], x["fwd"][h] - bench[h]))
                        break
        if n % 20 == 0 or n == len(rebal):
            print(f"   進度 {n}/{len(rebal)}")

    # ── 報表 ──────────────────────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("■ 各分數區間的歷史表現（勝率＝該區間個股上漲比率；超額＝報酬 − 當日全體平均）")
    print("=" * 100)

    summary = {}
    for st_name, (_, desc) in SCORE_TYPES.items():
        print(f"\n【{st_name}】{desc}")
        print(f"{'分數區間':<12}{'樣本':>8}" +
              "".join(f"{HZ_NAME[h] + '勝率':>10}{HZ_NAME[h] + '超額':>10}" for h in FWD))
        rows = []
        for bi, (lo, hi) in enumerate(BUCKETS):
            cells, n_any = [], 0
            rec = {"range": f"{lo}-{hi if hi <= 100 else 100}"}
            for h in FWD:
                data = obs[st_name][bi][h]
                if len(data) < 50:
                    cells.append(("—", "—"))
                    continue
                n_any = len(data)
                wr = sum(1 for r, _ in data if r > 0) / len(data) * 100
                ex = stat.mean(e for _, e in data)
                cells.append((f"{wr:.1f}%", f"{ex:+.2f}%"))
                rec[h] = {"win_rate": round(wr, 1), "excess": round(ex, 2),
                          "n": len(data)}
            if n_any:
                line = f"{rec['range']:<12}{n_any:>8}"
                for wr, ex in cells:
                    line += f"{wr:>10}{ex:>10}"
                print(line)
                rows.append(rec)
        summary[st_name] = rows

        # 建議門檻：超額報酬（1個月）由負轉正的分數
        cut = None
        for rec in rows:
            d = rec.get(20)
            if d and d["excess"] > 0:
                cut = rec["range"].split("-")[0]
                break
        neg = [rec["range"] for rec in rows
               if rec.get(20) and rec[20]["excess"] < 0]
        if cut:
            print(f"  → 1個月超額轉正的門檻約在 **{cut} 分**；"
                  f"以下區間為負：{'、'.join(neg) if neg else '無'}")
        else:
            print("  → 各區間 1個月超額報酬皆非正，無明確可買門檻")

    # 哪個持有期勝率最高
    print("\n" + "=" * 100)
    print("■ 「長線+量能確認」各持有期比較（取高分區間 70 分以上）")
    print("=" * 100)
    print(f"{'持有期':<10}{'樣本':>8}{'勝率':>10}{'平均報酬':>12}{'扣成本後':>12}{'超額報酬':>12}")
    for h in FWD:
        pool = []
        for bi, (lo, hi) in enumerate(BUCKETS):
            if lo >= 70:
                pool.extend(obs["長線+量能確認"][bi][h])
        if len(pool) < 50:
            continue
        wr = sum(1 for r, _ in pool if r > 0) / len(pool) * 100
        mr = stat.mean(r for r, _ in pool)
        ex = stat.mean(e for _, e in pool)
        print(f"{HZ_NAME[h]:<10}{len(pool):>8}{wr:>9.1f}%{mr:>11.2f}%"
              f"{mr - ROUND_TRIP_COST:>11.2f}%{ex:>11.2f}%")

    import json
    with open("score_thresholds.json", "w", encoding="utf-8") as f:
        json.dump({"buckets": [f"{lo}-{hi}" for lo, hi in BUCKETS],
                   "periods": len(rebal), "scores": summary}, f,
                  ensure_ascii=False, indent=2)
    print("\n已存出 score_thresholds.json")


if __name__ == "__main__":
    main()
