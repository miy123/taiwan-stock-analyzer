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
import os

import numpy as np

from services.universe import download_history_bulk
from services.technical import calculate_indicators
from services.scoring import raw_factors, pct_rank_column, FACTOR_WEIGHTS
from services.strategies import STRATEGIES, select
from services.backtest_stats import pit_universe, listed_pool
from services.scoring import BUY_BAR

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

    # ── 共用：一段期間內等權持有這批股票的報酬 ────────────────────────────
    def _ret(codes, a, b):
        rs = []
        for code in codes:
            p0 = (close_at[a] or {}).get(code)
            p1 = (close_at[b] or {}).get(code)
            if p0 and p1:
                rs.append(p1 / p0 - 1)
        return float(np.mean(rs)) if rs else 0.0

    years = (common[grid[-1]] - common[grid[0]]).days / 365.25

    # 基準只跟網格有關，與換股頻率／出場規則無關 —— 算一次就好。
    # （先前每個 step、每個 offset 各算一遍，同樣的結果算了 27 次。）
    bench_eq, bcurve = 1.0, [1.0]
    for k in range(len(grid) - 1):
        if picks_at[k] is None or close_at[k] is None or close_at[k + 1] is None:
            continue
        bench_eq *= (1 + _ret(universe_at[k] or [], k, k + 1))
        bcurve.append(bench_eq)
    bench_mdd = _max_drawdown(bcurve)

    def _ann(eq):
        """
        年化超額。⚠️ 不滿一年不要年化 —— 0.5 年的 1/years 次方會把雜訊放大成
        −100% 這種長多部位不可能出現的數字（實測短視窗印出過 −127%）。
        """
        if years < 1.0:
            return None
        return ((eq ** (1 / years)) - (bench_eq ** (1 / years))) * 100

    def _simulate(step, offset=0, exit_mode="rank", hold_days=None, buf_mult=2):
        """
        跑一條淨值曲線。

        exit_mode：
          rank   —— 只持有當期前 N 名（＝單純每 step 換股一次）
          buffer —— 掉出前 buf_mult×N 名才賣
          bar    —— 跌破買進線才賣
          time   —— **持滿 hold_days 個交易日就賣**

        ⚠️ `time` 的實作曾經是錯的：到期的部位從 keep 拿掉之後，
           又被「補滿前 N 名」那一步原封不動加回來，而且 `entered` 用
           `setdefault` 沒有重設進場日，於是它**永遠不會真的被賣掉**。
           量出來的「滿 60 日就賣」其實是「至少鎖 60 日、之後只要還在前 N 名
           就一直抱」——兩種規則都不是。症狀是中位持有天數**剛好等於**
           hold_days（同期 rank 規則是 20 日），正常的時間出場不可能這麼整齊。
           現在到期就真的賣掉，而且**當期不補回同一檔**，否則只是左手換右手付稅。
        """
        held, eq, cost_sum = [], 1.0, 0.0
        sold_f, bought_f = [], []
        entered, curve, lives = {}, [1.0], []

        for k in range(len(grid) - 1):
            if picks_at[k] is None or close_at[k] is None or close_at[k + 1] is None:
                continue
            if k >= offset and (k - offset) % step == 0 and picks_at[k]:
                top = picks_at[k]
                expired = set()
                if exit_mode == "rank":
                    keep = [c for c in held if c in top]
                elif exit_mode == "buffer":
                    lim = args.topn * buf_mult
                    keep = [c for c in held if (rank_at[k] or {}).get(c, 10**9) < lim]
                elif exit_mode == "bar":
                    keep = [c for c in held if (score_at[k] or {}).get(c, 0) >= BUY_BAR]
                elif exit_mode == "time":
                    keep, expired = [], set()
                    for c in held:
                        if (k - entered.get(c, k)) * args.grid >= hold_days:
                            expired.add(c)          # 到期：真的賣掉
                        else:
                            keep.append(c)
                else:
                    keep = []
                # 到期的這一期不補回來，否則等於原地換手只付稅
                fill = [c for c in top if c not in keep and c not in expired]
                new_set = (keep + fill)[:args.topn]

                sold = [c for c in held if c not in new_set]
                bought = [c for c in new_set if c not in held]
                n = max(len(new_set), 1)
                sf, bf = len(sold) / n, len(bought) / n
                sold_f.append(sf); bought_f.append(bf)
                # ⚠️ 開倉那一次只有買進成本：證交稅是**賣出**才課的。
                #    先前一律用 round trip，等於對一個從沒持有過的部位課了賣出稅。
                cost = (sf * SELL_COST + bf * BUY_COST) / 100
                eq *= (1 - cost); cost_sum += cost

                for c in sold:
                    lives.append((k - entered.pop(c, k)) * args.grid)
                for c in new_set:
                    entered.setdefault(c, k)
                held = new_set
            if held:
                eq *= (1 + _ret(held, k, k + 1))
            curve.append(eq)

        # ⚠️ 右設限：模擬結束時還開著的部位必然是活最久的那些，
        #    只統計已平倉的會讓中位持有天數偏低。把兩個數字都報出來。
        open_n = len(entered)
        return {"eq": eq, "cost": cost_sum, "curve": curve, "lives": lives,
                "turnover": float(np.mean([(s + b) / 2 for s, b in
                                           zip(sold_f, bought_f)])) if sold_f else 0.0,
                "rebalances": len(sold_f), "open_at_end": open_n}

    def _med_days(lives):
        return float(np.median(lives)) if lives else None

    # ── 換股頻率 ──────────────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("實際操作模擬：成本只對換掉的部位收取，基準為同池等權（不收成本）")
    print("=" * 100)
    print(f"{'換股頻率':<13}{'換手':>6}{'成本':>7}{'策略報酬(中位)':>15}"
          f"{'年化超額 中位':>14}{'[所有起始位移]':>22}{'最大回檔':>9}")
    out = {}
    for step in (1, 2, 4, 8, 12):
        runs = [_simulate(step, off) for off in range(step)]
        runs = [r for r in runs if len(r["curve"]) >= 3]
        if not runs:
            continue
        anns = [a for a in (_ann(r["eq"]) for r in runs) if a is not None]
        tots = [(r["eq"] - 1) * 100 for r in runs]
        mdds = [_max_drawdown(r["curve"]) for r in runs]
        label = f"每 {args.grid * step} 交易日"
        ann_txt = (f"{np.median(anns):>13.1f}%"
                   f"{'[' + f'{min(anns):+.1f} ~ {max(anns):+.1f}' + ']':>22}"
                   if anns else f"{'不滿一年不年化':>35}")
        print(f"{label:<13}{np.mean([r['turnover'] for r in runs]) * 100:>5.0f}%"
              f"{np.mean([r['cost'] for r in runs]) * 100:>6.1f}%"
              f"{np.median(tots):>14.1f}%" + ann_txt + f"{np.median(mdds):>8.1f}%")
        out[args.grid * step] = {
            "offsets": len(runs),
            "avg_turnover": round(float(np.mean([r["turnover"] for r in runs])), 3),
            "avg_cost_pct": round(float(np.mean([r["cost"] for r in runs])) * 100, 2),
            "total_median_pct": round(float(np.median(tots)), 2),
            "excess_annual_median_pct": round(float(np.median(anns)), 2) if anns else None,
            "excess_annual_min_pct": round(float(min(anns)), 2) if anns else None,
            "excess_annual_max_pct": round(float(max(anns)), 2) if anns else None,
            "max_drawdown_median_pct": round(float(np.median(mdds)), 2),
        }
    print(f"\n  基準（同池等權、不收成本）：總報酬 {(bench_eq - 1) * 100:+.1f}%　"
          f"最大回檔 {bench_mdd:.1f}%")

    # ── 出場規則 ──────────────────────────────────────────────────────────
    EXIT_STEP = 2
    print("\n" + "=" * 100)
    print(f"出場規則比較（每 {args.grid * EXIT_STEP} 交易日重新評估、"
          f"買進一律取前 {args.topn} 名、所有起始位移都跑）")
    print("=" * 100)
    print(f"{'出場規則':<26}{'換手':>6}{'成本':>7}{'年化超額':>10}"
          f"{'[範圍]':>20}{'最大回檔':>9}{'中位持有':>9}{'未平倉':>7}")
    exits = [("rank   掉出前 N 名就賣", "rank", None),
             ("buffer 掉出前 2N 名才賣", "buffer", None),
             ("bar    跌破買進線才賣", "bar", None),
             ("time   持滿 20 日就賣", "time", 20),
             ("time   持滿 60 日就賣", "time", 60)]
    exit_out = {}
    for label, mode, hd in exits:
        runs = [_simulate(EXIT_STEP, off, exit_mode=mode, hold_days=hd)
                for off in range(EXIT_STEP)]
        runs = [r for r in runs if len(r["curve"]) >= 3]
        if not runs:
            continue
        anns = [a for a in (_ann(r["eq"]) for r in runs) if a is not None]
        mdds = [_max_drawdown(r["curve"]) for r in runs]
        lives = [x for r in runs for x in r["lives"]]
        med = _med_days(lives)
        rng = (f"[{min(anns):+.1f} ~ {max(anns):+.1f}]" if anns else "—")
        # 不滿一年時 anns 是空的，印「—」而不是 nan%
        ann_txt = (f"{np.median(anns):>9.1f}%" if anns else f"{'—':>10}")
        print(f"{label:<26}{np.mean([r['turnover'] for r in runs]) * 100:>5.0f}%"
              f"{np.mean([r['cost'] for r in runs]) * 100:>6.1f}%"
              + ann_txt + f"{rng:>20}"
              f"{np.median(mdds):>8.1f}%"
              f"{(f'{med:.0f}日' if med is not None else '—'):>9}"
              f"{int(np.mean([r['open_at_end'] for r in runs])):>6}檔")
        exit_out[mode + (str(hd) if hd else "")] = {
            "label": label,
            "avg_turnover": round(float(np.mean([r["turnover"] for r in runs])), 3),
            "cost_pct": round(float(np.mean([r["cost"] for r in runs])) * 100, 2),
            "excess_annual_pct": round(float(np.median(anns)), 2) if anns else None,
            "excess_annual_min_pct": round(float(min(anns)), 2) if anns else None,
            "excess_annual_max_pct": round(float(max(anns)), 2) if anns else None,
            "max_drawdown_pct": round(float(np.median(mdds)), 2),
            # ⚠️ None 而不是 NaN：`float("nan")` 會被 json.dump 寫成裸 NaN，
            #    那不是合法 JSON（Python 讀得回來，但 node 的 JSON.parse 會炸），
            #    而且畫面會印出「中位持有 nan 日」。
            "median_holding_days": round(med, 1) if med is not None else None,
            "closed_positions": len(lives),
            "open_at_end": int(np.mean([r["open_at_end"] for r in runs])),
        }
    print("\n  ⚠️ `rank` 這一列與上表「每 "
          f"{args.grid * EXIT_STEP} 交易日」是同一組模擬（只持有當期前 N 名"
          "＝每期換股一次），列在這裡是當作其他出場規則的對照基準，不是第五個策略。")
    print("  ⚠️ 中位持有天數只統計**已平倉**的部位；仍開著的那幾檔必然活最久，"
          "所以這個數字是偏低的（右設限）。")

    print("\n讀表說明：")
    print("  · 換手 = 每次換股時，前 N 名裡有多少比例換掉（買賣兩邊取平均）")
    print(f"  · 成本 = 賣出比例 × {SELL_COST}%（含證交稅）＋ 買進比例 × {BUY_COST}%。"
          "開倉那一次只付買進成本")
    print("  · 基準 = 同一批可投資股票等權，同日換股但**不收成本**（刻意對策略不利）")
    print("  · 最大回檔在**每個網格點**按市值計算，不是只在換股日取樣")
    print("  · ⚠️ 每個頻率與每個出場規則都把**所有起始位移**跑過。")
    print("       看範圍再看中位數——如果範圍互相重疊，代表那個差異是雜訊。")
    if years < 1.0:
        print(f"  · ⚠️ 本次只有 {years:.2f} 年，**不年化**（不滿一年年化會把雜訊"
              "放大成長多部位不可能出現的數字）")
    payload = {
        "strategy": args.strategy, "topn": args.topn,
        "years": round(years, 2), "grid": args.grid,
        # 基準一起存檔：畫面要講「策略回檔 X% 而基準只有 Y%」時才有得讀，
        # 不必在 UI 字串裡寫死（那是本專案犯過三次的錯）。
        "benchmark": {"total_pct": round((bench_eq - 1) * 100, 2),
                      "max_drawdown_pct": round(bench_mdd, 2)},
        "by_interval": out, "by_exit_rule": exit_out,
    }
    # ⚠️ 用 __file__ 錨定，不要用 CWD：讀取端（services/evidence.load_portfolio_sim）
    #    是用 __file__ 定位的，從別的目錄執行會寫到別處，而畫面照樣顯示舊數字。
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "portfolio_sim.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n已存出 {out_path}")


def _pct(c, n):
    if len(c) <= n:
        return None
    p = float(c.iloc[-n - 1])
    return ((float(c.iloc[-1]) / p - 1) * 100) if p else None


if __name__ == "__main__":
    main()
