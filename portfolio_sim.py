"""
滾動持倉模擬 —— 回答「照這個策略實際操作，賺不賺得到錢」。

## 為什麼非做不可

本專案**所有**回測量的都是同一件事：「在第 t 天買進前 10 名，持有 h 天，
超額報酬多少」。那回答的是「訊號有沒有預測力」，**不是「實際操作會賺多少」**。
兩者差在三個地方：

1. **成本被當成固定一次 0.585%**。實際上每次換股只有「換掉的那幾檔」要付錢，
   但如果你每 5 天換一次，一季就付了 12 次——原本 +5.4% 的季超額會被吃光。
   換股頻率是**使用者每天都要做的決定**，而專案從來沒量過它。
2. **重疊的進場點不能相加**。139 個進場點各自持有 60 天，彼此重疊 12 層，
   那是統計樣本，不是一條可以實現的報酬曲線。
3. **最大回檔完全沒量過**。一個年化 +15% 但中途 -40% 的策略，多數人抱不住。

## 做法

一次把最細的網格（每 5 個交易日）算好，粗網格直接取子集——所以測 5 種換股頻率
只付一次計算成本。成本只對**真正換掉的部位**收取：
賣出 0.1425%+0.3%（證交稅）、買進 0.1425%，換掉比例 f 就付 f × 0.585%。

基準：同一批可投資股票的等權組合，同樣的日期換股，**不收成本**
（對策略不利，是刻意的——寧可低估自己）。
"""

import argparse
import json

import numpy as np

from services.universe import download_history_bulk
from services.technical import calculate_indicators
from services.scoring import raw_factors, pct_rank_column, FACTOR_WEIGHTS
from services.strategies import STRATEGIES, select
from services.backtest_stats import pit_universe, listed_pool

MIN_HISTORY = 260
SELL_COST = 0.1425 + 0.3      # 手續費 + 證交稅（賣出才課）
BUY_COST = 0.1425
ROUND_TRIP = SELL_COST + BUY_COST      # 0.585%


def _max_drawdown(curve):
    """最大回檔（%）—— 抱不抱得住比年化報酬更決定真實績效。"""
    peak, mdd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return mdd * 100


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", type=int, default=0)
    ap.add_argument("--stocks", type=int, default=400)
    ap.add_argument("--years", type=float, default=5.0)
    ap.add_argument("--topn", type=int, default=10)
    ap.add_argument("--grid", type=int, default=5, help="最細的換股網格（交易日）")
    ap.add_argument("--strategy", default="trend")
    ap.add_argument("--min-turnover", type=float, default=1e7)
    args = ap.parse_args()

    sdef = next(s for s in STRATEGIES if s["key"] == args.strategy)
    print(f"策略：{sdef['label']}　選前 {args.topn} 名　近 {args.years} 年\n")

    frames = download_history_bulk(listed_pool(args.pool), period="5y", chunk=120)
    enriched = {}
    for c, d in frames.items():
        if len(d) >= MIN_HISTORY + 60:
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
    grid = list(range(start_i, len(common) - 1, args.grid))
    print(f"網格 {len(grid)} 點（{common[grid[0]].date()} ~ {common[grid[-1]].date()}）\n")

    # ── 一次算好每個網格點的「選中名單」與「當日全池收盤」──────────────────
    picks_at, universe_at, close_at, rank_at, score_at = [], [], [], [], []
    ctx = {"buy_bar": 58, "horizon_key": None, "trend_bar": 50}
    for k, di in enumerate(grid):
        date = common[di]
        if k % 20 == 0:
            print(f"  評分 {k}/{len(grid)}  {date.date()}", flush=True)
        cand = pit_universe(enriched, date, MIN_HISTORY,
                            args.min_turnover, args.stocks)
        rows, facts, closes = [], [], {}
        for _to, code, pos in cand:
            sl = enriched[code].iloc[:pos + 1]
            closes[code] = float(sl["Close"].iloc[-1])
            rows.append({"stock_id": code, "mom250": _pct(sl["Close"], 250),
                         "potential": {}, "pe_ratio": None, "is_limit_up": False,
                         "max_streak": 0, "total_score": 50})
            facts.append(raw_factors(sl))
        if len(rows) < 50:
            picks_at.append(None); universe_at.append(None); close_at.append(None)
            rank_at.append(None); score_at.append(None)
            continue
        ranks = {f: pct_rank_column([x.get(f) for x in facts]) for f in FACTOR_WEIGHTS}
        tw = sum(FACTOR_WEIGHTS.values())
        for i, r in enumerate(rows):
            r["trend_score"] = sum(ranks[f][i] * w for f, w in FACTOR_WEIGHTS.items()) / tw
        try:
            sel = select(sdef, rows, ctx)[:args.topn]
        except Exception:
            sel = []
        picks_at.append([r["stock_id"] for r in sel])
        universe_at.append([r["stock_id"] for r in rows])
        close_at.append(closes)
        # 出場規則要用到「這一天的完整排序與分數」，不是只有前 N 名
        ordered = sorted(rows, key=lambda r: -r.get("trend_score", 0))
        rank_at.append({r["stock_id"]: i for i, r in enumerate(ordered)})
        score_at.append({r["stock_id"]: r.get("trend_score", 0) for r in rows})

    # ── 用同一份選股結果，模擬不同換股頻率 ────────────────────────────────
    print("\n" + "=" * 96)
    print("實際操作模擬：成本只對換掉的部位收取，基準為同池等權（不收成本）")
    print("=" * 96)

    years = (common[grid[-1]] - common[grid[0]]).days / 365.25

    def _simulate(step, offset):
        """跑一條淨值曲線。offset ＝ 從第幾個網格點開始換股。"""
        held, eq, bench_eq, cost_sum, turns = [], 1.0, 1.0, 0.0, []
        curve, bcurve = [1.0], [1.0]

        def _ret(codes, a, b):
            rs = []
            for code in codes:
                p0 = (close_at[a] or {}).get(code)
                p1 = (close_at[b] or {}).get(code)
                if p0 and p1:
                    rs.append(p1 / p0 - 1)
            return float(np.mean(rs)) if rs else 0.0

        for k in range(len(grid) - 1):
            if picks_at[k] is None or close_at[k] is None or close_at[k + 1] is None:
                continue
            if (k - offset) % step == 0 and k >= offset and picks_at[k]:
                new_set = picks_at[k]
                keep = (len(set(new_set) & set(held)) / len(new_set)) if held else 0.0
                turn = 1.0 - keep
                turns.append(turn)
                c = turn * ROUND_TRIP / 100
                eq *= (1 - c)
                cost_sum += c
                held = new_set
            if held:
                eq *= (1 + _ret(held, k, k + 1))
            bench_eq *= (1 + _ret(universe_at[k] or [], k, k + 1))
            curve.append(eq)
            bcurve.append(bench_eq)
        return eq, bench_eq, cost_sum, turns, curve, bcurve

    print(f"{'換股頻率':<13}{'換手':>6}{'成本':>7}{'策略報酬(中位)':>15}"
          f"{'年化超額 中位':>14}{'[所有起始位移的範圍]':>24}{'最大回檔':>9}")
    out, bench_mdd = {}, None
    for step in (1, 2, 4, 8, 12):
        runs = [_simulate(step, off) for off in range(step)]   # 所有起始位移都跑
        anns, mdds, turns_all, costs, tots = [], [], [], [], []
        for eq, beq, cost, turns, curve, bcurve in runs:
            if len(curve) < 3:
                continue
            anns.append(((eq ** (1 / years)) - (beq ** (1 / years))) * 100)
            mdds.append(_max_drawdown(curve))
            turns_all.extend(turns); costs.append(cost * 100); tots.append((eq - 1) * 100)
            if bench_mdd is None:
                bench_mdd = _max_drawdown(bcurve)
                bench_tot = (beq - 1) * 100
        if not anns:
            continue
        label = f"每 {args.grid * step} 交易日"
        print(f"{label:<13}{np.mean(turns_all) * 100:>5.0f}%{np.mean(costs):>6.1f}%"
              f"{np.median(tots):>14.1f}%{np.median(anns):>13.1f}%"
              f"{'[' + f'{min(anns):+.1f} ~ {max(anns):+.1f}' + ']':>24}"
              f"{np.median(mdds):>8.1f}%")
        out[args.grid * step] = {
            "offsets": len(anns),
            "avg_turnover": round(float(np.mean(turns_all)), 3),
            "avg_cost_pct": round(float(np.mean(costs)), 2),
            "total_median_pct": round(float(np.median(tots)), 2),
            "excess_annual_median_pct": round(float(np.median(anns)), 2),
            "excess_annual_min_pct": round(float(min(anns)), 2),
            "excess_annual_max_pct": round(float(max(anns)), 2),
            "max_drawdown_median_pct": round(float(np.median(mdds)), 2),
        }
    print(f"\n  基準（同池等權、不收成本）：總報酬 {bench_tot:+.1f}%　"
          f"最大回檔 {bench_mdd:.1f}%")
    bench_eq_ref = 1 + bench_tot / 100

    # ── 出場規則比較 ──────────────────────────────────────────────────────
    #
    # 使用者問「能不能順便建議出場時間」。**資料不支持給一個固定日期**：
    # 每 5~10 日重新評估的績效遠勝每 20~60 日，代表該做的是「條件出場」
    # 而不是「時間到了就賣」。這裡把四種出場規則放在同一條路徑上比。
    #
    # buffer（掉出前 2N 才賣）是刻意測的：買進用嚴格門檻、賣出用寬鬆門檻，
    # 可以在不放棄訊號的前提下把換手壓下來——這是本專案沒試過的方向，
    # 而且它**不是**「多加一層過濾」（那個已經被否定四次），是放寬而非收緊。
    from services.scoring import BUY_BAR

    def _simulate_exit(mode, step=2, buf_mult=2, hold_days=None):
        held, eq, cost_sum, turns = [], 1.0, 0.0, []
        entered = {}                      # code → 進場時的網格點
        curve, lives = [1.0], []

        def _ret(codes, a, b):
            rs = []
            for code in codes:
                p0 = (close_at[a] or {}).get(code)
                p1 = (close_at[b] or {}).get(code)
                if p0 and p1:
                    rs.append(p1 / p0 - 1)
            return float(np.mean(rs)) if rs else 0.0

        for k in range(len(grid) - 1):
            if picks_at[k] is None or close_at[k] is None or close_at[k + 1] is None:
                continue
            if k % step == 0 and picks_at[k]:
                top = picks_at[k]
                if mode == "rank":
                    keep = [c for c in held if c in top]
                elif mode == "buffer":
                    lim = args.topn * buf_mult
                    keep = [c for c in held if (rank_at[k] or {}).get(c, 10**9) < lim]
                elif mode == "bar":
                    keep = [c for c in held
                            if (score_at[k] or {}).get(c, 0) >= BUY_BAR]
                elif mode == "time":
                    keep = [c for c in held
                            if (k - entered.get(c, k)) * args.grid < hold_days]
                else:
                    keep = []
                new_set = keep + [c for c in top if c not in keep]
                new_set = new_set[:args.topn]
                for c in held:
                    if c not in new_set:
                        lives.append((k - entered.pop(c, k)) * args.grid)
                for c in new_set:
                    entered.setdefault(c, k)
                turn = 1 - (len(set(new_set) & set(held)) / len(new_set)
                            if held and new_set else 0.0)
                turns.append(turn)
                c_ = turn * ROUND_TRIP / 100
                eq *= (1 - c_); cost_sum += c_
                held = new_set
            if held:
                eq *= (1 + _ret(held, k, k + 1))
            curve.append(eq)
        return eq, cost_sum, turns, curve, lives

    print("\n" + "=" * 96)
    print(f"出場規則比較（每 {args.grid * 2} 交易日重新評估、買進一律取前 {args.topn} 名）")
    print("=" * 96)
    print(f"{'出場規則':<26}{'換手':>6}{'成本':>7}{'總報酬':>10}"
          f"{'年化超額':>10}{'最大回檔':>9}{'平均持有':>10}")
    exits = [("rank   掉出前 N 名就賣", "rank", None),
             ("buffer 掉出前 2N 名才賣", "buffer", None),
             ("bar    跌破買進線才賣", "bar", None),
             ("time   滿 20 個交易日就賣", "time", 20),
             ("time   滿 60 個交易日就賣", "time", 60)]
    exit_out = {}
    for label, mode, hd in exits:
        eq, cost, turns, curve, lives = _simulate_exit(mode, hold_days=hd)
        if len(curve) < 3:
            continue
        ann = ((eq ** (1 / years)) - (bench_eq_ref ** (1 / years))) * 100
        life = float(np.median(lives)) if lives else float("nan")
        print(f"{label:<26}{np.mean(turns) * 100:>5.0f}%{cost * 100:>6.1f}%"
              f"{(eq - 1) * 100:>9.1f}%{ann:>9.1f}%"
              f"{_max_drawdown(curve):>8.1f}%{life:>9.0f}日")
        exit_out[mode + (str(hd) if hd else "")] = {
            "label": label, "avg_turnover": round(float(np.mean(turns)), 3),
            "cost_pct": round(cost * 100, 2),
            "total_pct": round((eq - 1) * 100, 2),
            "excess_annual_pct": round(ann, 2),
            "max_drawdown_pct": round(_max_drawdown(curve), 2),
            "median_holding_days": None if lives != lives else round(life, 1),
        }

    print("\n讀表說明：")
    print("  · 換手 = 每次換股時，前 N 名裡有多少比例是新面孔（100% 代表整批換掉）")
    print(f"  · 成本 = 換手比例 × {ROUND_TRIP}%（賣出 {SELL_COST}% + 買進 {BUY_COST}%）")
    print("  · 基準 = 同一批可投資股票等權，同日換股但**不收成本**（刻意對策略不利）")
    print("  · 最大回檔在**每個網格點**按市值計算，不是只在換股日取樣")
    print("  · ⚠️ 每個頻率都把**所有起始位移**跑過（每 20 日換股＝4 條路徑）。")
    print("       看範圍再看中位數——如果範圍互相重疊，代表「最佳換股頻率」是雜訊。")
    with open("portfolio_sim.json", "w", encoding="utf-8") as f:
        json.dump({"strategy": args.strategy, "topn": args.topn,
                   "years": round(years, 2), "by_interval": out,
                   "by_exit_rule": exit_out}, f,
                  ensure_ascii=False, indent=2)
    print("\n已存出 portfolio_sim.json")


def _pct(c, n):
    if len(c) <= n:
        return None
    p = float(c.iloc[-n - 1])
    return ((float(c.iloc[-1]) / p - 1) * 100) if p else None


if __name__ == "__main__":
    main()
