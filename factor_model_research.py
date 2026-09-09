"""
連續評分模型研究 —— 把資券、量能、本益比與技術線合成「一個沒有平手的分數」，
回到舊時點依該分數選股，再用之後 1／2／3 個月的實際結果驗收。

為什麼要重做：
  現行長線分是 50 加減幾個**離散跳點**（±14/±12/±8/+10），全市場只有 40 種
  可能值，滿分 94 有 277 檔並列（49%）——分數在頂端幾乎沒有鑑別力，
  真正在排序的是後來加的平手鍵。這不是「分數有效」，是「平手鍵有效」。

做法：**橫斷面百分位**（cross-sectional percentile rank）
  每個因子在「當天所有股票」之中排名，換算成 0~100 的百分位，再加權相加。
  · 天生連續，不會有一堆並列
  · 天生同日可比，不受大盤漲跌影響（每天重新排名）
  · 缺資料的因子給中性 50，不會因為上櫃股沒有融資就被判死刑

Point-in-time 紀律（避免前視偏誤）：
  · 技術／量能：由「截至當日」的價量切片計算
  · 資券：向證交所要當日快照（services/histdata，落地快取）
  · 本益比：當日股價 ÷ **當日之前**最近一次已知 EPS

驗收：+20／+40／+60 交易日（約 1／2／3 個月）的超額報酬
      （超額 = 個股報酬 − 當日全市場等權平均；多頭時人人都漲，絕對報酬會騙人）
"""

import argparse
import json
import math
from collections import defaultdict

import numpy as np
import pandas as pd

from services.universe import get_listed_snapshot, download_history_bulk
from services.technical import calculate_indicators, analyze_volume_price
from services.histdata import (
    valuation_snapshot, build_eps_timeline, pe_from_timeline,
    margin_snapshot, cache_stats, CACHE_DIR,
)

MIN_HISTORY = 260
FWD = [20, 40, 60]              # 約 1／2／3 個月
ROUND_TRIP_COST = 0.585         # 手續費 0.1425%×2 + 證交稅 0.3%


# ── 因子 ────────────────────────────────────────────────────────────────────
# 每個因子回傳一個「越大越好」的原始值（方向已統一），或 None 表示無資料。
# 之後會轉成當日百分位，所以尺度不用管。

def _pct(c, n):
    if len(c) <= n:
        return None
    p = float(c.iloc[-n - 1])
    return ((float(c.iloc[-1]) / p - 1) * 100) if p else None


def _safe(x):
    try:
        v = float(x)
        return v if v == v else None
    except Exception:
        return None


def raw_factors(sl, mgn, pe):
    """由當日切片算出所有因子的原始值。sl = 截至當日的 K 線（含指標）。"""
    c = sl["Close"]
    close = float(c.iloc[-1])
    f = {}

    # ── 趨勢結構 ──
    ma120 = _safe(sl["MA120"].iloc[-1])
    ma60 = _safe(sl["MA60"].iloc[-1])
    ma20 = _safe(sl["MA20"].iloc[-1])
    f["dist_ma120"] = (close / ma120 - 1) * 100 if ma120 else None
    f["dist_ma60"] = (close / ma60 - 1) * 100 if ma60 else None
    f["ma60_vs_ma120"] = (ma60 / ma120 - 1) * 100 if (ma60 and ma120) else None
    prev120 = _safe(sl["MA120"].iloc[-21]) if len(sl) > 21 else None
    f["ma120_slope"] = (ma120 / prev120 - 1) * 100 if (ma120 and prev120) else None
    prev60 = _safe(sl["MA60"].iloc[-21]) if len(sl) > 21 else None
    f["ma60_slope"] = (ma60 / prev60 - 1) * 100 if (ma60 and prev60) else None
    f["ma20_vs_ma60"] = (ma20 / ma60 - 1) * 100 if (ma20 and ma60) else None

    # ── 動能 ──
    f["r20"], f["r60"] = _pct(c, 20), _pct(c, 60)
    f["r120"], f["r250"] = _pct(c, 120), _pct(c, 250)
    # 12-1 動能（學術標準：跳過最近一個月避開短期反轉）
    f["mom_12_1"] = (f["r250"] - f["r20"]) if (f["r250"] is not None
                                               and f["r20"] is not None) else None

    # ── 量能 ──
    v5 = _safe(sl["Vol_MA5"].iloc[-1]) if "Vol_MA5" in sl else None
    v20 = _safe(sl["Vol_MA20"].iloc[-1]) if "Vol_MA20" in sl else None
    v60 = _safe(sl["Vol_MA60"].iloc[-1]) if "Vol_MA60" in sl else None
    f["vol_burst"] = (v5 / v60 - 1) * 100 if (v5 and v60) else None      # 短期爆量
    f["vol_trend"] = (v20 / v60 - 1) * 100 if (v20 and v60) else None    # 中期量增
    f["turnover"] = float(close * (v20 or 0))                            # 流動性
    try:
        f["vol_price"] = float(analyze_volume_price(sl).get("score_adj", 0))
    except Exception:
        f["vol_price"] = None

    # ── 資券 ──
    if mgn:
        f["margin_usage"] = (-mgn["usage_pct"]
                             if mgn.get("usage_pct") is not None else None)
        f["margin_change"] = (-mgn["change_pct"]
                              if mgn.get("change_pct") is not None else None)
        f["short_ratio"] = mgn.get("short_ratio")
    else:
        f["margin_usage"] = f["margin_change"] = f["short_ratio"] = None

    # ── 估值 ──
    f["pe_inv"] = (1.0 / pe) if (pe and pe > 0) else None    # 越便宜越大
    f["pe_raw"] = pe

    # ── 波動（風險）──
    try:
        ret = c.pct_change().tail(60)
        f["low_vol"] = -float(ret.std() * 100)
    except Exception:
        f["low_vol"] = None
    return f


def pct_rank(values):
    """
    當日橫斷面百分位（0~100）。None 一律給中性 50，不參與排名。

    用「排名」而非「z 分數」是因為台股因子分布厚尾嚴重（漲停股、雞蛋水餃股），
    z 分數會被極端值主宰；排名對離群值免疫。
    """
    idx = [i for i, v in enumerate(values) if v is not None]
    out = [50.0] * len(values)
    if len(idx) < 5:
        return out
    vals = np.array([values[i] for i in idx], dtype=float)
    order = vals.argsort().argsort()          # 0 = 最小
    scaled = order / max(len(vals) - 1, 1) * 100
    for k, i in enumerate(idx):
        out[i] = float(scaled[k])
    return out


# ── 候選模型：因子權重表（權重會自動正規化）──────────────────────────────
MODELS = {
    # 對照組：只用趨勢，等同現行邏輯的連續版
    "T1 趨勢(連續)": {"dist_ma120": 1, "ma60_vs_ma120": 1, "ma120_slope": 1},
    "T2 趨勢+動能": {"dist_ma120": 1, "ma60_vs_ma120": 1, "ma120_slope": 1,
                     "r120": 1, "r60": 1},
    "T3 純動能": {"r60": 1, "r120": 1, "r250": 1},
    "T4 12-1動能": {"mom_12_1": 1},

    # 加量能
    "V1 趨勢+量能": {"dist_ma120": 1, "ma60_vs_ma120": 1, "ma120_slope": 1,
                     "vol_trend": 1, "vol_price": 1},
    "V2 趨勢+動能+量能": {"dist_ma120": 1, "ma60_vs_ma120": 1, "ma120_slope": 1,
                          "r120": 1, "r60": 1, "vol_trend": 1, "vol_price": 1},

    # 加資券
    "M1 趨勢+資券": {"dist_ma120": 1, "ma60_vs_ma120": 1, "ma120_slope": 1,
                     "margin_usage": 1, "margin_change": 1},
    "M2 趨勢+動能+資券": {"dist_ma120": 1, "ma60_vs_ma120": 1, "ma120_slope": 1,
                          "r120": 1, "r60": 1, "margin_usage": 1, "margin_change": 1},

    # 加估值
    "P1 純低本益比": {"pe_inv": 1},
    "P2 趨勢+估值": {"dist_ma120": 1, "ma60_vs_ma120": 1, "ma120_slope": 1,
                     "pe_inv": 1},
    "P3 動能+估值(便宜的強勢股)": {"r120": 1, "r60": 1, "dist_ma120": 1,
                                   "pe_inv": 1.5},

    # 全因子
    "A1 全因子等權": {"dist_ma120": 1, "ma60_vs_ma120": 1, "ma120_slope": 1,
                      "r120": 1, "r60": 1, "vol_trend": 1, "vol_price": 1,
                      "margin_usage": 1, "margin_change": 1, "pe_inv": 1},
    "A2 全因子(趨勢加重)": {"dist_ma120": 2, "ma60_vs_ma120": 2, "ma120_slope": 2,
                            "r120": 1.5, "r60": 1.5, "vol_trend": 1, "vol_price": 1,
                            "margin_usage": 0.5, "margin_change": 0.5, "pe_inv": 1},
    "A3 全因子(估值加重)": {"dist_ma120": 1, "ma60_vs_ma120": 1, "ma120_slope": 1,
                            "r120": 1, "r60": 1, "vol_trend": 0.5, "vol_price": 0.5,
                            "margin_usage": 0.5, "margin_change": 0.5, "pe_inv": 3},
}


def score_models(ranks, n):
    """ranks: {factor: [百分位…]} → {model: [分數…]}"""
    out = {}
    for name, w in MODELS.items():
        tot = sum(w.values())
        s = np.zeros(n)
        for f, wt in w.items():
            s += np.array(ranks.get(f, [50.0] * n)) * wt
        out[name] = s / tot
    return out


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
    ap.add_argument("--min-turnover", type=float, default=1e7)
    args = ap.parse_args()

    print(f"設定: 前{args.stocks}檔 / 每{args.every}交易日換股 / 近{args.years}年 / "
          f"選前{args.topn}名")
    print(f"快取: {cache_stats()}\n")

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

    # 估值時間軸（用已快取的每月快照；沒快取的日期會被跳過而非硬抓）
    val_dates = sorted(p.stem.replace("val_", "")
                       for p in CACHE_DIR.glob("val_*.json"))
    print(f"估值快照 {len(val_dates)} 個日期"
          f"（{val_dates[0] if val_dates else '無'} ~ {val_dates[-1] if val_dates else '無'}）")

    def close_on(code, d):
        df = enriched.get(code)
        if df is None or df.empty:
            return None
        sub = df.loc[:pd.Timestamp(f"{d[:4]}-{d[4:6]}-{d[6:]}")]
        return float(sub["Close"].iloc[-1]) if len(sub) else None

    eps_tl = build_eps_timeline(val_dates, close_on)
    print(f"EPS 時間軸涵蓋 {len(eps_tl)} 檔\n")

    common = None
    for d in enriched.values():
        common = d.index if common is None else common.union(d.index)
    common = common.sort_values()
    start_i = max(MIN_HISTORY, len(common) - int(args.years * 252))
    end_i = len(common) - max(FWD) - 1
    rebal = list(range(start_i, end_i, args.every))
    print(f"換股日 {len(rebal)} 個（{common[start_i].date()} ~ {common[end_i].date()}）\n")

    # 結果容器
    res = {m: {h: [] for h in FWD} for m in MODELS}
    dates_used = []
    disc = defaultdict(list)          # 鑑別力：最大並列數
    mgn_cov, pe_cov = [], []

    for k, di in enumerate(rebal):
        date = common[di]
        ds = date.strftime("%Y%m%d")
        if k % 20 == 0:
            print(f"  {k}/{len(rebal)}  {date.date()}", flush=True)

        mgn_tbl = margin_snapshot(ds)          # 已快取則不打 API

        day = []
        for code, d in enriched.items():
            pos = d.index.searchsorted(date, side="right") - 1
            if pos < MIN_HISTORY or pos >= len(d):
                continue
            if abs((d.index[pos] - date).days) > 7:
                continue
            sl = d.iloc[:pos + 1]
            close = float(sl["Close"].iloc[-1])
            # 流動性下限：太冷門的股票買不進去，回測選到也沒意義
            v20 = _safe(sl["Vol_MA20"].iloc[-1]) if "Vol_MA20" in sl else None
            if not v20 or close * v20 < args.min_turnover:
                continue
            fwd, ok = {}, True
            for h in FWD:
                if pos + h >= len(d):
                    ok = False
                    break
                a = close
                b = float(d["Close"].iloc[pos + h])
                fwd[h] = (b / a - 1) * 100 if a else None
                if fwd[h] is None:
                    ok = False
            if not ok:
                continue
            pe = pe_from_timeline(eps_tl, code, ds, close)
            try:
                f = raw_factors(sl, mgn_tbl.get(code), pe)
            except Exception:
                continue
            day.append({"code": code, "f": f, "fwd": fwd})

        if len(day) < 50:
            continue
        dates_used.append(date)
        mgn_cov.append(100 * sum(1 for x in day
                                 if x["f"]["margin_usage"] is not None) / len(day))
        pe_cov.append(100 * sum(1 for x in day
                                if x["f"]["pe_inv"] is not None) / len(day))

        names = sorted({kk for x in day for kk in x["f"]})
        ranks = {f: pct_rank([x["f"].get(f) for x in day]) for f in names}
        scores = score_models(ranks, len(day))
        bench = {h: float(np.mean([x["fwd"][h] for x in day])) for h in FWD}

        for m, sc in scores.items():
            order = np.argsort(-sc)[:args.topn]
            picks = [day[i] for i in order]
            for h in FWD:
                r = float(np.mean([p["fwd"][h] for p in picks]))
                res[m][h].append(r - bench[h])
            # 鑑別力：分數最高者有幾檔同分
            top = sc.max()
            disc[m].append(int((np.abs(sc - top) < 1e-9).sum()))

    if not dates_used:
        print("沒有可用的換股日。")
        return

    print(f"\n資料涵蓋率：資券 {np.mean(mgn_cov):.0f}%　本益比 {np.mean(pe_cov):.0f}%")
    print(f"有效換股日 {len(dates_used)} 個\n")

    # ── 全期結果 ──
    print("=" * 96)
    print(f"全期表現 —— 選前 {args.topn} 名，超額報酬 vs 當日全市場等權平均")
    print("=" * 96)
    print(f"{'模型':<26} " + "".join(
        f"{'+' + str(h) + '日超額':>11}{'t':>7}{'勝率':>7}" for h in FWD)
        + f"{'最大並列':>8}")
    ranking = []
    for m in MODELS:
        line = f"{m:<26} "
        for h in FWD:
            xs = res[m][h]
            win = 100 * sum(1 for x in xs if x > 0) / len(xs) if xs else 0
            line += f"{np.mean(xs):>+10.2f}%{_t(xs):>7.2f}{win:>6.0f}%"
        line += f"{np.mean(disc[m]):>8.1f}"
        print(line)
        ranking.append((float(np.mean(res[m][60])), _t(res[m][60]), m))

    # ── 走查：前半段挑模型，後半段驗證 ──
    print("\n" + "=" * 96)
    print("走查驗證：用**前半段**挑出最佳模型，看它在**後半段**（完全沒看過的資料）表現")
    print("=" * 96)
    mid = len(dates_used) // 2
    print(f"訓練段 {dates_used[0].date()} ~ {dates_used[mid-1].date()}（{mid} 期）")
    print(f"測試段 {dates_used[mid].date()} ~ {dates_used[-1].date()}"
          f"（{len(dates_used)-mid} 期）\n")
    for h in FWD:
        train = sorted(((float(np.mean(res[m][h][:mid])), m) for m in MODELS),
                       reverse=True)
        best = train[0][1]
        te = res[best][h][mid:]
        tr_all = sorted(((float(np.mean(res[m][h][mid:])), m) for m in MODELS),
                        reverse=True)
        print(f"  +{h}日：訓練段最佳 = 「{best}」({train[0][0]:+.2f}%)")
        print(f"        → 測試段實際 {np.mean(te):+.2f}%  t={_t(te):+.2f}  "
              f"勝率 {100*sum(1 for x in te if x>0)/len(te):.0f}%")
        print(f"        （測試段真正最佳是「{tr_all[0][1]}」{tr_all[0][0]:+.2f}%，"
              f"名次落差就是過擬合的代價）")

    # ── 近期環境（最後 1/3 期）──
    print("\n" + "=" * 96)
    print("目前大環境（最近 1/3 期）—— 現在該用哪個模型")
    print("=" * 96)
    cut = int(len(dates_used) * 2 / 3)
    print(f"期間 {dates_used[cut].date()} ~ {dates_used[-1].date()}"
          f"（{len(dates_used)-cut} 期）\n")
    print(f"{'模型':<26} " + "".join(
        f"{'+' + str(h) + '日':>10}{'t':>7}" for h in FWD))
    recent = []
    for m in MODELS:
        line = f"{m:<26} "
        for h in FWD:
            xs = res[m][h][cut:]
            line += f"{np.mean(xs):>+9.2f}%{_t(xs):>7.2f}"
        print(line)
        recent.append((float(np.mean(res[m][60][cut:])), _t(res[m][60][cut:]), m))
    recent.sort(reverse=True)
    print(f"\n  近期 +60日最佳：「{recent[0][2]}」 {recent[0][0]:+.2f}% (t={recent[0][1]:+.2f})")

    # ── 扣掉交易成本 ──
    print("\n" + "=" * 96)
    print(f"扣除來回交易成本 {ROUND_TRIP_COST}% 後的淨超額（每次換股都付一次）")
    print("=" * 96)
    for m in [r[2] for r in sorted(ranking, reverse=True)[:5]]:
        line = f"{m:<26} "
        for h in FWD:
            line += f"+{h}日 {np.mean(res[m][h]) - ROUND_TRIP_COST:>+6.2f}%   "
        print(line)

    out = {
        "generated_periods": len(dates_used),
        "date_range": [str(dates_used[0].date()), str(dates_used[-1].date())],
        "coverage": {"margin_pct": float(np.mean(mgn_cov)),
                     "pe_pct": float(np.mean(pe_cov))},
        "topn": args.topn,
        "models": {
            m: {
                "weights": MODELS[m],
                "max_tie": float(np.mean(disc[m])),
                **{f"h{h}": {"excess": float(np.mean(res[m][h])),
                             "t": _t(res[m][h]),
                             "win_rate": 100 * sum(1 for x in res[m][h] if x > 0)
                             / len(res[m][h]),
                             "recent_excess": float(np.mean(res[m][h][cut:]))}
                   for h in FWD},
            } for m in MODELS
        },
    }
    with open("factor_model_results.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n已存出 factor_model_results.json")


if __name__ == "__main__":
    main()
