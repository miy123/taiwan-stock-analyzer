"""
把 App 的**每一個策略**放在同一次回測裡比較 —— 這樣「哪個比較強」才有答案。

## 為什麼非做不可

選股頁的策略說明本來寫的是形容詞（「✅實證最強」「✅族群動能有效」），
使用者根本無從判斷哪個強。而且更糟的是：那些數字來自**不同次回測**
（趨勢分來自 139 期的 factor_3y，族群來自 179 期的 main_3y），
本專案自己的結論就是「跨 run 名次會變，全距 0.6~0.76% 大於模型間差異，
只能在同一次執行內比較」。所以先前就算把數字寫上去，比較也是無效的。

## 做法

**直接呼叫 `services/strategies.select()`** —— 測的就是 App 實際在跑的那份定義，
而不是在這裡複製一份篩選邏輯（複製一份就會漂移，這個坑本專案踩過很多次）。

每個換股日建出與掃描結果同構的 row（stock_id / trend_score / pe_ratio /
potential / is_limit_up / max_streak），交給 select() 挑前 10 名，
再用 +20／+40／+60 交易日的實際報酬驗收。所有策略共用同一批日期、
同一個等權基準 → 直接可比。

Point-in-time：本益比用月度 EPS 快照 × 當日股價；漲停由當日價量判定；
族群動能由當日往前 60 日報酬算。都沒有用到未來資料。
"""

import argparse
import json
import math
from collections import defaultdict

import numpy as np

from services.universe import get_listed_snapshot, download_history_bulk
from services.technical import calculate_indicators
from services.scoring import raw_factors, pct_rank_column, FACTOR_WEIGHTS, BUY_BAR
from services.potential import calculate_potential_score
from services.histdata import build_eps_timeline, pe_from_timeline, CACHE_DIR
from services.strategies import STRATEGIES, select
from itertools import combinations

MIN_HISTORY = 260
FWD = [20, 40, 60]
ROUND_TRIP_COST = 0.585
TOPN_DEFAULT = 10


def _t(xs):
    if len(xs) < 3:
        return 0.0
    m, sd = float(np.mean(xs)), float(np.std(xs, ddof=1))
    return m / (sd / math.sqrt(len(xs))) if sd else 0.0


def _pct(c, n):
    if len(c) <= n:
        return None
    p = float(c.iloc[-n - 1])
    return ((float(c.iloc[-1]) / p - 1) * 100) if p else None


def _limit_up_streak(d, pos, lookback=10):
    """近 lookback 日的漲停紀錄（台股漲跌幅上限 10%，取 9.5% 為門檻）。"""
    streak = best = 0
    for i in range(max(1, pos - lookback + 1), pos + 1):
        prev, cur = float(d["Close"].iloc[i - 1]), float(d["Close"].iloc[i])
        if prev and (cur / prev - 1) * 100 >= 9.5:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=400)
    ap.add_argument("--every", type=int, default=5)
    ap.add_argument("--years", type=float, default=3.0)
    ap.add_argument("--topn", type=int, default=TOPN_DEFAULT)
    ap.add_argument("--min-turnover", type=float, default=1e7)
    args = ap.parse_args()

    print(f"設定: 前{args.stocks}檔 / 每{args.every}日換股 / 近{args.years}年 / "
          f"選前{args.topn}名\n")

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

    import pandas as pd

    def close_on(code, ds):
        df = enriched.get(code)
        if df is None or df.empty:
            return None
        sub = df.loc[:pd.Timestamp(f"{ds[:4]}-{ds[4:6]}-{ds[6:]}")]
        return float(sub["Close"].iloc[-1]) if len(sub) else None

    val_dates = sorted(p.stem.replace("val_", "")
                       for p in CACHE_DIR.glob("val_*.json"))
    eps_tl = build_eps_timeline(val_dates, close_on)
    print(f"估值快照 {len(val_dates)} 個日期，EPS 涵蓋 {len(eps_tl)} 檔\n")

    common = None
    for d in enriched.values():
        common = d.index if common is None else common.union(d.index)
    common = common.sort_values()
    start_i = max(MIN_HISTORY, len(common) - int(args.years * 252))
    end_i = len(common) - max(FWD) - 1
    rebal = list(range(start_i, end_i, args.every))
    print(f"換股日 {len(rebal)} 個（{common[start_i].date()} ~ {common[end_i].date()}）\n")

    keys = [s["key"] for s in STRATEGIES]
    # 交叉篩選：同時擠進兩個策略前 N 名的股票。使用者會用這個選股，
    # 那就必須知道它到底有沒有比單一策略好——本專案已三次驗證「多加一層過濾更差」，
    # 交集本質上就是再加一層過濾，不能只憑「兩個都選中應該更可靠」的直覺。
    CROSS_N = 20
    pairs = list(combinations(keys, 2))
    res = {k: {h: [] for h in FWD} for k in keys}
    cross = {f"{a}+{b}": {h: [] for h in FWD} for a, b in pairs}
    cross_n = defaultdict(list)
    picked_n = defaultdict(list)
    dates_used = []

    for k, di in enumerate(rebal):
        date = common[di]
        ds = date.strftime("%Y%m%d")
        if k % 20 == 0:
            print(f"  {k}/{len(rebal)}  {date.date()}", flush=True)

        rows, facts = [], []
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
            try:
                pot = calculate_potential_score(
                    sl, {}, {}, 50, {"positive": [], "negative": []}, None)
            except Exception:
                continue
            streak = _limit_up_streak(d, pos)
            rows.append({
                "stock_id": code,
                "pe_ratio": pe_from_timeline(eps_tl, code, ds, close),
                "potential": pot,
                "is_limit_up": streak > 0,
                "max_streak": streak,
                "total_score": 50,          # 綜合評分無法還原（含新聞/基本面），給中性值
                "_fwd": fwd,
            })
            facts.append(raw_factors(sl))

        if len(rows) < 50:
            continue
        # 連續趨勢分：與 App 完全同一套（當日橫斷面百分位）
        ranks = {f: pct_rank_column([x.get(f) for x in facts])
                 for f in FACTOR_WEIGHTS}
        tw = sum(FACTOR_WEIGHTS.values())
        for i, r in enumerate(rows):
            r["trend_score"] = sum(ranks[f][i] * w
                                   for f, w in FACTOR_WEIGHTS.items()) / tw
            r["above_ma120"] = facts[i].get("dist_ma120")

        dates_used.append(date)
        bench = {h: float(np.mean([r["_fwd"][h] for r in rows])) for h in FWD}
        ctx = {"buy_bar": 58, "horizon_key": None, "trend_bar": BUY_BAR}

        topsets = {}
        for sdef in STRATEGIES:
            try:
                sel = select(sdef, rows, ctx)
            except Exception:
                sel = []
            topsets[sdef["key"]] = sel[:CROSS_N]
            sel = sel[:args.topn]
            picked_n[sdef["key"]].append(len(sel))
            if not sel:
                continue
            for h in FWD:
                r = float(np.mean([x["_fwd"][h] for x in sel]))
                res[sdef["key"]][h].append(r - bench[h])

        for a, b in pairs:
            ida = {x["stock_id"] for x in topsets.get(a, [])}
            both = [x for x in topsets.get(b, []) if x["stock_id"] in ida]
            cross_n[f"{a}+{b}"].append(len(both))
            if len(both) < 2:       # 只有 1 檔的組合是雜訊，不計入
                continue
            for h in FWD:
                r = float(np.mean([x["_fwd"][h] for x in both]))
                cross[f"{a}+{b}"][h].append(r - bench[h])

    print(f"\n有效換股日 {len(dates_used)} 個\n")
    print("=" * 100)
    print(f"同一次回測、同一批日期、同一個基準 —— 選前 {args.topn} 名的超額報酬")
    print("=" * 100)
    label_of = {s["key"]: s["label"] for s in STRATEGIES}
    print(f"{'策略':<22}{'有標的期數':>9}{'平均檔數':>8}"
          + "".join(f"{'+' + str(h) + '日':>10}{'t':>7}{'贏率':>6}" for h in FWD))
    out = {}
    for kk in keys:
        line = f"{label_of[kk]:<22}{len(res[kk][20]):>9}{np.mean(picked_n[kk]):>8.1f}"
        rec = {"periods_with_picks": len(res[kk][20]),
               "avg_picks": round(float(np.mean(picked_n[kk])), 1)}
        for h in FWD:
            xs = res[kk][h]
            if not xs:
                line += f"{'—':>10}{'—':>7}{'—':>6}"
                rec[f"h{h}"] = None
                continue
            win = 100 * sum(1 for x in xs if x > 0) / len(xs)
            line += f"{np.mean(xs):>+9.2f}%{_t(xs):>7.2f}{win:>5.0f}%"
            rec[f"h{h}"] = {"excess": round(float(np.mean(xs)), 2),
                            "t": round(_t(xs), 2),
                            "beat_rate": round(win, 1),
                            "periods": len(xs),
                            "net_excess": round(float(np.mean(xs)) - ROUND_TRIP_COST, 2)}
        print(line)
        out[kk] = rec

    # ── 交叉篩選：同時進兩個策略前 N 名 ──────────────────────────────────
    print("\n" + "=" * 100)
    print(f"交叉篩選：同時擠進兩個策略前 {CROSS_N} 名的股票（≥2檔才計入）")
    print("=" * 100)
    print(f"{'組合':<30}{'有交集期數':>10}{'平均檔數':>8}"
          + "".join(f"{'+' + str(h) + '日':>10}{'t':>7}" for h in FWD))
    cross_out = {}
    for a, b in pairs:
        kk = f"{a}+{b}"
        xs20 = cross[kk][20]
        if len(xs20) < 20:
            print(f"{kk:<30}{len(xs20):>10}{np.mean(cross_n[kk]):>8.1f}"
                  f"   期數不足，不下結論")
            continue
        line = f"{kk:<30}{len(xs20):>10}{np.mean(cross_n[kk]):>8.1f}"
        rec = {"periods": len(xs20), "avg_picks": round(float(np.mean(cross_n[kk])), 1)}
        for h in FWD:
            xs = cross[kk][h]
            line += f"{np.mean(xs):>+9.2f}%{_t(xs):>7.2f}"
            rec[f"h{h}"] = {"excess": round(float(np.mean(xs)), 2), "t": round(_t(xs), 2),
                            "periods": len(xs)}
        print(line)
        cross_out[kk] = rec
        # 與「單獨用較強的那一個」比
        for h in (60,):
            solo = max(float(np.mean(res[a][h])), float(np.mean(res[b][h])))
            d = float(np.mean(cross[kk][h])) - solo
            print(f"{'':<30}  → +{h}日 比單獨用較強的那個 {d:+.2f}%"
                  f"（{'交集較優' if d > 0 else '交集較差'}）")
            cross_out[kk]["vs_best_solo_h60"] = round(d, 2)

    # 走查：前後半段各自獨立，看名次穩不穩
    print("\n" + "=" * 100)
    print("走查：前半段 vs 後半段（名次會不會翻盤）")
    print("=" * 100)
    mid = len(dates_used) // 2
    for h in FWD:
        print(f"\n  +{h}日")
        for kk in keys:
            xs = res[kk][h]
            if len(xs) < 20:
                print(f"    {label_of[kk]:<22} 樣本不足")
                continue
            m = len(xs) // 2
            a, b = xs[:m], xs[m:]
            print(f"    {label_of[kk]:<22} 前半 {np.mean(a):+6.2f}%"
                  f"（t={_t(a):+5.2f}）　後半 {np.mean(b):+6.2f}%（t={_t(b):+5.2f}）")
            out[kk].setdefault("walk_forward", {})[f"h{h}"] = {
                "first_half": round(float(np.mean(a)), 2),
                "second_half": round(float(np.mean(b)), 2),
            }

    payload = {
        "generated_periods": len(dates_used),
        "date_range": [str(dates_used[0].date()), str(dates_used[-1].date())],
        "topn": args.topn,
        "method": ("同一次回測、同一批換股日、同一個當日等權基準；"
                   "直接呼叫 services/strategies.select()，測的就是 App 實跑的定義"),
        "strategies": out,
        "cross_screen": {"top_n_per_strategy": CROSS_N, "pairs": cross_out},
    }
    with open("strategy_comparison.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("\n已存出 strategy_comparison.json")


if __name__ == "__main__":
    main()
