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

Point-in-time：本益比用月度 EPS 快照 × 當日股價，**同業中位數也用同一天的那批
本益比現算**（`sector.pe_medians`，與 App 同一個實作），不是拿今天的中位數回套；
漲停由當日價量判定
（呼叫 `limit_up.streaks_from_closes`，與 App 同一份定義與同一組常數）；
族群動能由當日往前 60 日報酬算。都沒有用到未來資料。
"""

import argparse
import json
from collections import defaultdict

import numpy as np

from services.universe import download_history_bulk
from services.technical import calculate_indicators
from services.scoring import raw_factors, pct_rank_column, FACTOR_WEIGHTS, BUY_BAR
from services.potential import calculate_potential_score
from services.histdata import build_eps_timeline, pe_from_timeline, CACHE_DIR
from services.strategies import STRATEGIES, select
from services.limit_up import streaks_from_closes, MAX_DAYS_AGO, MIN_STREAK
from services.sector import pe_medians, rel_pe_pct, get_industry_map
from services.strategies import PE_DISCOUNT_PCT
from services.backtest_stats import (
    t_stat, t_newey_west, nonoverlap, pit_universe, listed_pool,
)
from itertools import combinations

MIN_HISTORY = 260
FWD = [20, 40, 60]
ROUND_TRIP_COST = 0.585
TOPN_DEFAULT = 10


# 統計工具一律走 services/backtest_stats（唯一實作），這裡不再自己寫一份。
_t, _t_nw, _nonoverlap = t_stat, t_newey_west, nonoverlap


def _pct(c, n):
    if len(c) <= n:
        return None
    p = float(c.iloc[-n - 1])
    return ((float(c.iloc[-1]) / p - 1) * 100) if p else None


def _limit_up_at(d, pos):
    """
    當日往前看的連日漲停指標 —— **直接呼叫 App 的唯一實作**
    （`services/limit_up.streaks_from_closes`），不要在這裡再寫一份。

    ⚠️ 這裡原本自己寫了一份：往回看 **10** 日、`is_limit_up = 漲停過就算`、
       **沒有** recency 條件。而 App 的定義是「15 日內出現過 ≥2 連、且最後一次
       在 5 個交易日內」（`limit_up.LOOKBACK_DAYS / MIN_STREAK / MAX_DAYS_AGO`）。
       兩者選出來的根本不是同一批股票，於是畫面上那個「漲停動能」的實證數字，
       量的並不是 App 實際在跑的策略——正是本模組開頭警告的「複製一份就會漂移」。
    """
    return streaks_from_closes(d["Close"].iloc[:pos + 1])


def main():
    ap = argparse.ArgumentParser()
    # --pool  下載幾檔（0 ＝ 全部上市）。這一步用的是**今天**的成交金額排序，
    #         所以池子開越大、前視偏誤越小；預設全抓。
    # --stocks 每個換股日實際可投資幾檔，改由**當日**流動性排序決定（見下方）。
    ap.add_argument("--pool", type=int, default=0)
    ap.add_argument("--stocks", type=int, default=400)
    ap.add_argument("--every", type=int, default=5)
    # 預設 5 年（約 179 期）而不是 3 年（139 期）。
    # ⚠️ 理由：修正重疊視窗（Newey-West）之後標準誤變大，3 年的樣本讓每個
    #    正向策略的 t 都掉到 1.6 以下，分不出誰有效。同一批資料拉到 5 年後
    #    趨勢結構分 t 從 1.59 回到 2.03——**差別是樣本量，不是模型**。
    #    5 年是 yfinance 這條路徑拿得到的上限（download_history_bulk period="5y"）。
    ap.add_argument("--years", type=float, default=5.0)
    ap.add_argument("--topn", type=int, default=TOPN_DEFAULT)
    ap.add_argument("--min-turnover", type=float, default=1e7)
    args = ap.parse_args()

    print(f"設定: 前{args.stocks}檔 / 每{args.every}日換股 / 近{args.years}年 / "
          f"選前{args.topn}名\n")

    codes = listed_pool(args.pool)
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

    imap = get_industry_map()
    keys = [s["key"] for s in STRATEGIES]
    # 交叉篩選：同時擠進兩個策略前 N 名的股票。使用者會用這個選股，
    # 那就必須知道它到底有沒有比單一策略好——本專案已三次驗證「多加一層過濾更差」，
    # 交集本質上就是再加一層過濾，不能只憑「兩個都選中應該更可靠」的直覺。
    CROSS_N = 20
    pairs = list(combinations(keys, 2))
    res = {k: {h: [] for h in FWD} for k in keys}
    addon = {k: {h: [] for h in FWD} for k in keys}      # 疊上估值門檻後
    addon_n = defaultdict(list)
    cross = {f"{a}+{b}": {h: [] for h in FWD} for a, b in pairs}
    cross_n = defaultdict(list)
    picked_n = defaultdict(list)
    dates_used = []

    for k, di in enumerate(rebal):
        date = common[di]
        ds = date.strftime("%Y%m%d")
        if k % 20 == 0:
            print(f"  {k}/{len(rebal)}  {date.date()}", flush=True)

        # ① 投資範圍由**當日**的流動性決定 —— 不是拿今天的成交金額回頭挑池子。
        #
        # ⚠️ 這裡原本是「用今天的成交金額排序取前 400 檔」，那是直接與報酬相關的
        #    前視偏誤：2023 年冷門、後來才變熱門的股票會被放進池子，而
        #    「後來變熱門」常常就是因為它漲了很多。現在改成每個換股日各自用
        #    當日往前 20 日的平均成交金額排序取前 `--stocks` 檔。
        cand = pit_universe(enriched, date, MIN_HISTORY,
                            args.min_turnover, args.stocks)

        rows, facts = [], []
        for _to, code, pos in cand:
            d = enriched[code]
            sl = d.iloc[:pos + 1]
            close = float(sl["Close"].iloc[-1])
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
            lu = _limit_up_at(d, pos)
            rows.append({
                "stock_id": code,
                "pe_ratio": pe_from_timeline(eps_tl, code, ds, close),
                "potential": pot,
                # 判定條件與 App 逐字相同（universe.scan_universe 也是這兩行）
                "is_limit_up": (lu["max_streak"] >= MIN_STREAK
                                and lu["last_days_ago"] <= MAX_DAYS_AGO),
                "max_streak": lu["max_streak"],
                # 純動能（近 N 日報酬）—— 診斷欄位。修正後的 backtest_research 裡
                # 動能類的平均超額最高，但那是**另一個 run**，不能跨 run 比。
                # 帶在 row 上，才能把它當候選策略放進同一批換股日測。
                "mom250": _pct(sl["Close"], 250),
                "mom60": _pct(sl["Close"], 60),
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

        # 相對同業的本益比 —— 和趨勢分一樣是**當日橫斷面**，所以也只能等
        # 這一天的 rows 都建好才算得出來。用的是同一天的 point-in-time 本益比
        # （月度 EPS 快照 × 當日股價），沒有前視偏誤。
        # 分組與中位數走 `sector.pe_medians()`，與 App 即時路徑同一個實作。
        med = pe_medians({r["stock_id"]: r["pe_ratio"] for r in rows})
        for r in rows:
            ind = (imap.get(r["stock_id"]) or {}).get("name")
            row_med = med.get(ind) or {}
            r["pe_peer"] = ({"industry": ind, **row_med} if row_med else {})
            r["pe_rel_pct"] = rel_pe_pct(r["pe_ratio"], row_med.get("median"))

        dates_used.append(date)
        bench = {h: float(np.mean([r["_fwd"][h] for r in rows])) for h in FWD}
        ctx = {"buy_bar": 58, "horizon_key": None, "trend_bar": BUY_BAR}

        # 估值門檻是使用者可以疊在**任一策略**上的加掛條件，所以每個策略都要量
        # 「加了它會怎樣」。體質門檻量不出來（財報沒有歷史快照），
        # 但估值門檻吃的 `pe_rel_pct` 在這裡是 point-in-time 算出來的，量得到。
        ctx_pe = {**ctx, "pe_bar": PE_DISCOUNT_PCT}
        for sdef in STRATEGIES:
            try:
                sel_pe = select(sdef, rows, ctx_pe)[:args.topn]
            except Exception:
                sel_pe = []
            addon_n[sdef["key"]].append(len(sel_pe))
            if sel_pe:
                for h in FWD:
                    r = float(np.mean([x["_fwd"][h] for x in sel_pe]))
                    addon[sdef["key"]][h].append(r - bench[h])

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
    # ⚠️ 對外報告的 t 一律是 **Newey-West**：每 `--every` 天換股、持有 h 天，
    #    相鄰 h/every 期的持有區間重疊，139 個觀測不是獨立樣本。
    #    `t_raw` 仍存進 json 供對照，但**不要拿它下結論**。
    print(f"{'策略':<22}{'有標的期數':>9}{'平均檔數':>8}"
          + "".join(f"{'+' + str(h) + '日':>10}{'tNW':>7}{'t舊':>7}{'贏率':>6}"
                    for h in FWD))
    out = {}
    for kk in keys:
        line = f"{label_of[kk]:<22}{len(res[kk][20]):>9}{np.mean(picked_n[kk]):>8.1f}"
        rec = {"periods_with_picks": len(res[kk][20]),
               "avg_picks": round(float(np.mean(picked_n[kk])), 1)}
        for h in FWD:
            xs = res[kk][h]
            if not xs:
                line += f"{'—':>10}{'—':>7}{'—':>7}{'—':>6}"
                rec[f"h{h}"] = None
                continue
            win = 100 * sum(1 for x in xs if x > 0) / len(xs)
            overlap = h / args.every           # 重疊的期別數
            tnw = _t_nw(xs, overlap - 1)
            line += f"{np.mean(xs):>+9.2f}%{tnw:>7.2f}{_t(xs):>7.2f}{win:>5.0f}%"
            rec[f"h{h}"] = {"excess": round(float(np.mean(xs)), 2),
                            "t": round(tnw, 2),          # ← 對外的 t 就是 NW
                            "t_raw": round(_t(xs), 2),   # 舊的（高估），僅供對照
                            "overlap": round(overlap, 1),
                            "nonoverlap": _nonoverlap(xs, overlap),
                            "beat_rate": round(win, 1),
                            "periods": len(xs),
                            "net_excess": round(float(np.mean(xs)) - ROUND_TRIP_COST, 2)}
        print(line)
        out[kk] = rec

    print()
    print("=" * 100)
    print("完全不重疊的子樣本檢驗（每 h/every 期取一個，所有起始位移都算）")
    print("=" * 100)
    for h in FWD:
        print(f"  +{h}日（每組約 {int(len(rebal) / (h / args.every))} 期、"
              f"{int(h / args.every)} 組）")
        for kk in keys:
            no = (out[kk].get(f"h{h}") or {}).get("nonoverlap") or {}
            if not no:
                print(f"    {label_of[kk]:<22} 樣本不足")
                continue
            print(f"    {label_of[kk]:<22} t 中位 {no['t_med']:+5.2f}"
                  f"　範圍 {no['t_min']:+5.2f} ~ {no['t_max']:+5.2f}")

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
            _tnw = _t_nw(xs, h / args.every - 1)
            line += f"{np.mean(xs):>+9.2f}%{_tnw:>7.2f}"
            rec[f"h{h}"] = {"excess": round(float(np.mean(xs)), 2), "t": round(_tnw, 2),
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
            _lag = h / args.every - 1
            print(f"    {label_of[kk]:<22} 前半 {np.mean(a):+6.2f}%"
                  f"（t={_t_nw(a, _lag):+5.2f}）　後半 {np.mean(b):+6.2f}%"
                  f"（t={_t_nw(b, _lag):+5.2f}）")
            out[kk].setdefault("walk_forward", {})[f"h{h}"] = {
                "first_half": round(float(np.mean(a)), 2),
                "second_half": round(float(np.mean(b)), 2),
            }

    # ── 加掛估值門檻的影響 ───────────────────────────────────────────────
    print()
    print("=" * 100)
    print(f"加掛「估值門檻：本益比低於同業 {abs(PE_DISCOUNT_PCT):.0f}%」之後（疊在每個策略上）")
    print("=" * 100)
    print(f"{'策略':<22}{'平均檔數':>8}"
          + "".join(f"{'+' + str(h) + '日':>10}{'差':>9}" for h in FWD))
    addon_out = {}
    for kk in keys:
        rec = {"bar": PE_DISCOUNT_PCT,
               "avg_picks": round(float(np.mean(addon_n[kk])), 1),
               "periods_with_picks": len(addon[kk][20])}
        line = f"{label_of[kk]:<22}{np.mean(addon_n[kk]):>8.1f}"
        for h in FWD:
            xs, base = addon[kk][h], res[kk][h]
            if len(xs) < 20:
                line += f"{'樣本不足':>10}{'':>9}"
                continue
            m, d = float(np.mean(xs)), float(np.mean(xs)) - float(np.mean(base))
            line += f"{m:>+9.2f}%{d:>+8.2f}%"
            rec[f"h{h}"] = {"excess": round(m, 2),
                            "t": round(_t_nw(xs, h / args.every - 1), 2),
                            "delta_vs_base": round(d, 2), "periods": len(xs)}
        print(line)
        addon_out[kk] = rec

    payload = {
        "generated_periods": len(dates_used),
        "date_range": [str(dates_used[0].date()), str(dates_used[-1].date())],
        "topn": args.topn,
        "method": ("同一次回測、同一批換股日、同一個當日等權基準；"
                   "直接呼叫 services/strategies.select()，測的就是 App 實跑的定義"),
        "every": args.every,
        "universe": {
            "pool": len(codes),
            "per_period_cap": args.stocks,
            "selection": "每個換股日各自用當日往前 20 日的平均成交金額排序取前 N 檔",
            "min_turnover": args.min_turnover,
        },
        # ⚠️ 畫面上要講的限制。t 已用 Newey-West 修正重疊視窗；
        #    生存者偏誤修不掉（拿不到台股已下市公司的歷史），只能標明。
        "caveats": {
            "t_stat": ("t 值為 Newey-West（Bartlett kernel，lag = 持有天數/換股間隔 − 1），"
                       "已修正重疊視窗造成的高估；`t_raw` 是未修正的舊算法，僅供對照"),
            "survivorship": ("股票池取自**今天仍在上市**的公司，"
                             "區間內已下市者完全不在樣本中——所有策略的數字都因此偏樂觀"),
        },
        "strategies": out,
        "cross_screen": {"top_n_per_strategy": CROSS_N, "pairs": cross_out},
        # 使用者加選的估值門檻疊在每個策略上的實際影響。
        # 畫面上的說明由它生成（`app._pe_evidence_note`），不要寫死。
        "addon_pe": addon_out,
    }
    with open("strategy_comparison.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("\n已存出 strategy_comparison.json")


if __name__ == "__main__":
    main()
