"""
多期滾動回測研究 — 找出最有效的選股模型。

用法:
    python3 backtest_research.py [--stocks N] [--every N] [--years N] [--topn N]

方法論（誠實性優先）:
  · 只用「可還原到當日」的價量訊號（技術／量價／低基期／ATR風報比／大盤環境）。
    基本面與新聞沒有歷史快照，納入會造成前視偏誤，因此本回測**完全排除**它們。
  · 指標具因果性（rolling/ewm 只看過去），故先對完整歷史算一次指標，再取當日該列，
    等同「當天只用當天以前的資料」，且速度快數十倍。
  · 每個換股日對全市場排名，取前 N 名，計算未來 5/20/60 交易日報酬。
  · 基準 = 同一天所有合格股票的等權平均報酬（等於「隨便買」）。
    真正有意義的是 **超額報酬 = 策略平均 − 基準**，而非絕對報酬（多頭時什麼都賺）。
"""

import argparse
import statistics as stat
import sys

import pandas as pd
import yfinance as yf

sys.path.insert(0, ".")

from services.technical import (
    calculate_indicators, calculate_technical_score, calculate_horizon_scores,
    analyze_volume_price, calculate_risk_plan,
)
from services.potential import calculate_potential_score
from services.universe import get_listed_snapshot, download_history_bulk

FWD = (5, 20, 60)          # 交易日：約 1週 / 1個月 / 3個月
MIN_HISTORY = 260          # 評分所需最少歷史長度

# ── 可還原到歷史日期的「非價量」資料 ─────────────────────────────────────────
# 先前結論說「基本面無法回測」只對了一半：ROE／營收成長確實沒有歷史快照，
# 但**估值面（本益比／殖利率／股價淨值比）證交所有逐日資料**（BWIBBU_d），
# 融資籌碼也有（MI_MARGN）。兩者都是「每個換股日 1 次請求」，可負擔。
_VAL_URL = ("https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d"
            "?date={d}&selectType=ALL&response=json")
_val_cache, _mgn_cache = {}, {}
_val_fetch_failures = []


def _num(x):
    try:
        v = float(str(x).replace(",", "").strip())
        return v if v != 0 else None
    except (TypeError, ValueError):
        return None


def get_hist_valuation(date_str, retries=3):
    """
    {code: {pe, dy, pb}} 於指定日期（YYYYMMDD）。

    ⚠️ 證交所 BWIBBU_d 會限流：被擋時**回傳非 JSON**（HTML 錯誤頁），舊版把例外
    默默吞掉當成「當天沒資料」，導致 179 個日期只有 50 個成功且非隨機
    ——估值模型的結論因此**不可信**。此處改為指數退避重試，並回報失敗率，
    讓「資料抓不到」不會偽裝成「因子無效」。
    """
    if date_str in _val_cache:
        return _val_cache[date_str]
    import time
    import requests
    out, blocked = {}, False
    for attempt in range(retries):
        try:
            time.sleep(1.5 * (2 ** attempt))       # 1.5s → 3s → 6s 指數退避
            resp = requests.get(_VAL_URL.format(d=date_str), timeout=30,
                                headers={"User-Agent": "Mozilla/5.0"})
            j = resp.json()                         # 被擋時這裡會拋 JSONDecodeError
            if j.get("stat") == "OK" and j.get("data"):
                for row in j["data"]:
                    if len(row) >= 7:
                        out[str(row[0]).strip()] = {
                            "pe": _num(row[5]), "dy": _num(row[3]), "pb": _num(row[6]),
                        }
                blocked = False
                break
            blocked = False                          # 正常回應但當天無資料
        except ValueError:                           # 非 JSON = 被限流
            blocked = True
            continue
        except Exception:
            continue
    if blocked:
        _val_fetch_failures.append(date_str)
    _val_cache[date_str] = out
    return out


def get_hist_margin(date_str):
    """{code: {usage_pct, change_pct}} 於指定日期。"""
    if date_str in _mgn_cache:
        return _mgn_cache[date_str]
    from services.margin import _fetch_twse_margin_table
    out = {}
    try:
        tbl = _fetch_twse_margin_table.__wrapped__(date_str)
        for code, rec in (tbl or {}).items():
            out[code] = {"usage_pct": rec.get("usage_pct"),
                         "change_pct": rec.get("change_pct")}
    except Exception:
        pass
    _mgn_cache[date_str] = out
    return out

# 台股來回交易成本：手續費 0.1425%×2（買賣各一次）+ 賣出證交稅 0.3% ≈ 0.585%
# 不扣成本的回測會嚴重高估短週期策略（週轉越快越失真）
ROUND_TRIP_COST = 0.585


# ── 模型定義 ──────────────────────────────────────────────────────────────────
# 每個模型 = (名稱, 排序函式, 過濾函式)。sig 是當日算好的訊號字典。
def _m(name, key, filt=None, desc=""):
    return {"name": name, "key": key, "filter": filt, "desc": desc}


MODELS = [
    _m("綜合強勢(技術)", lambda s: s["tech"], None,
       "純技術評分排序"),
    _m("極短線", lambda s: s["h_ultra"], None, "1-3天動能"),
    _m("短線", lambda s: s["h_short"], None, "1週波段"),
    _m("中線", lambda s: s["h_medium"], None, "1個月趨勢"),
    _m("長線", lambda s: s["h_long"], None, "半年結構"),
    _m("潛力潛伏", lambda s: s["pot"], None,
       "低基期為主（話題/前瞻中性）"),
    _m("低基期純粹", lambda s: s["low_base"], None,
       "只看『還沒漲』"),
    _m("攻守兼備", lambda s: (max(s["tech"], 0) * max(s["pot"], 0)) ** 0.5,
       lambda s: s["tech"] >= 48 and s["low_base"] >= 45 and (s["rr"] or 0) >= 1.5,
       "技術×潛力幾何平均 + R:R≥1.5"),
    _m("攻守兼備(無RR)", lambda s: (max(s["tech"], 0) * max(s["pot"], 0)) ** 0.5,
       lambda s: s["tech"] >= 48 and s["low_base"] >= 45,
       "同上但不濾風報比"),
    _m("低基期+技術轉強", lambda s: 0.5 * s["low_base"] + 0.5 * s["tech"],
       lambda s: s["low_base"] >= 55 and s["tech"] >= 55,
       "低檔且技術已轉強"),
    _m("高風報比", lambda s: (s["rr"] or 0), lambda s: (s["rr"] or 0) >= 1.0,
       "純粹依 ATR 風報比排序"),
    _m("短線+高RR", lambda s: s["h_short"],
       lambda s: (s["rr"] or 0) >= 2.0, "短線分排序，只取 R:R≥2"),
    _m("反向(買最弱)", lambda s: -s["tech"], None,
       "對照組：買技術最差的"),

    # ── 新增：原始動能因子（檢驗複合分數是否真的比單純動能好）──────────────
    _m("純動能60日", lambda s: (s["r60"] or -999), lambda s: s["r60"] is not None,
       "只看近60日漲幅"),
    _m("純動能120日", lambda s: (s["r120"] or -999), lambda s: s["r120"] is not None,
       "只看近120日漲幅"),
    _m("純動能250日", lambda s: (s["r250"] or -999), lambda s: s["r250"] is not None,
       "只看近250日漲幅"),
    _m("動能12-1", lambda s: (s["mom_12_1"] or -999),
       lambda s: s["mom_12_1"] is not None, "年動能扣掉最近1個月(學術標準)"),

    # ── 新增：長線分 + 各種輔助過濾（能否再提升）──────────────────────────
    _m("長線+低波動", lambda s: s["h_long"],
       lambda s: (s["atr_pct"] or 99) <= 3.5, "長線分但排除高波動股"),
    _m("長線+量能確認", lambda s: s["h_long"], lambda s: s["vol_adj"] >= 0,
       "長線分且量價未轉弱"),
    _m("長線+不追高", lambda s: s["h_long"], lambda s: s["pos"] <= 85,
       "長線分但排除已逼近52週高點"),
    _m("長線×動能", lambda s: s["h_long"] * (1 + (s["r120"] or 0) / 100),
       lambda s: s["r120"] is not None, "長線分乘上120日動能"),

    # ── 估值面（歷史可還原：證交所 BWIBBU_d）──────────────────────────────
    _m("低本益比(價值)", lambda s: -(s["pe"] or 9999),
       lambda s: s["pe"] is not None and 0 < s["pe"] < 100, "純價值：本益比越低越前面"),
    _m("高殖利率", lambda s: (s["dy"] or -1),
       lambda s: s["dy"] is not None and s["dy"] > 0, "純殖利率排序"),
    _m("低股價淨值比", lambda s: -(s["pb"] or 9999),
       lambda s: s["pb"] is not None and 0 < s["pb"] < 20, "純 P/B 排序"),
    _m("長線+合理估值", lambda s: s["h_long"],
       lambda s: s["pe"] is not None and 0 < s["pe"] < 30,
       "長線分但排除本益比>30"),
    _m("長線+高殖利率", lambda s: s["h_long"],
       lambda s: (s["dy"] or 0) >= 3, "長線分且殖利率≥3%"),

    # ── 融資籌碼（歷史可還原：證交所 MI_MARGN）────────────────────────────
    _m("長線+融資健康", lambda s: s["h_long"],
       lambda s: (s["mgn_usage"] is None) or s["mgn_usage"] < 15,
       "長線分且融資使用率<15%"),
    _m("長線+融資減少", lambda s: s["h_long"],
       lambda s: (s["mgn_chg"] is None) or s["mgn_chg"] <= 0,
       "長線分且融資餘額未增加"),
    _m("融資使用率高(反向)", lambda s: (s["mgn_usage"] or -1),
       lambda s: s["mgn_usage"] is not None, "對照：專買散戶槓桿最重的"),
]

# 需要「大盤翻空就空手」的模型（單獨處理，因為要記錄 0% 而非略過）
CASH_WHEN_BEAR = "長線+多頭才進場"


def _ret(close, n):
    """n 個交易日報酬 %，資料不足回 None。"""
    if len(close) <= n:
        return None
    past = float(close.iloc[-n - 1])
    return ((float(close.iloc[-1]) / past - 1) * 100) if past else None


def build_signals(df_slice):
    """由『截至當日』的資料算出所有可還原訊號。"""
    tech, _ = calculate_technical_score(df_slice)
    hz = calculate_horizon_scores(df_slice)
    vol = analyze_volume_price(df_slice)
    # 話題/前瞻/空間給中性值 → 不影響同日彼此排名
    pot = calculate_potential_score(
        df_slice, {}, {}, 50, {"positive": [], "negative": []}, None
    )
    risk = calculate_risk_plan(df_slice) or {}
    c = df_slice["Close"]
    r250, r20 = _ret(c, 250), _ret(c, 20)
    return {
        "tech": tech,
        "h_ultra": hz["ultra_short"]["score"],
        "h_short": hz["short"]["score"],
        "h_medium": hz["medium"]["score"],
        "h_long": hz["long"]["score"],
        "pot": pot["total"],
        "low_base": pot["low_base"],
        "pos": pot["position_pct"],
        "rr": risk.get("rr"),
        "stop": risk.get("stop"),
        "atr_pct": risk.get("atr_pct"),
        "vol_adj": vol.get("score_adj", 0),
        # 原始動能因子（學術標準）：用來檢驗我們的複合分數是否真的優於單純動能
        "r60": _ret(c, 60),
        "r120": _ret(c, 120),
        "r250": r250,
        # 12-1 動能：跳過最近一個月，避開短期反轉效應
        "mom_12_1": (r250 - r20) if (r250 is not None and r20 is not None) else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=400, help="最多分析幾檔（依流動性）")
    ap.add_argument("--every", type=int, default=10, help="每隔幾個交易日換股一次")
    ap.add_argument("--years", type=float, default=2.0, help="回測涵蓋幾年")
    ap.add_argument("--topn", type=int, default=10, help="每次選前幾名")
    ap.add_argument("--valuation", action="store_true",
                    help="抓歷史估值(本益比/殖利率/PB)。證交所會限流，需節流故很慢，預設關閉")
    args = ap.parse_args()

    print(f"設定: 前{args.stocks}檔流動股 / 每{args.every}交易日換股 / "
          f"近{args.years}年 / 每次選前{args.topn}名\n")

    print("① 取得上市清單與歷史資料…")
    snap = get_listed_snapshot()
    ranked = sorted(snap.items(), key=lambda kv: -(kv[1].get("turnover") or 0))
    codes = [c for c, _ in ranked[:args.stocks]]
    frames = download_history_bulk(codes, period="5y", chunk=120)
    print(f"   取得 {len(frames)} 檔歷史資料")

    print("② 預先計算指標（一次算完，之後切片即為當日狀態）…")
    enriched = {}
    for c, d in frames.items():
        try:
            if len(d) >= MIN_HISTORY + max(FWD):
                enriched[c] = calculate_indicators(d)
        except Exception:
            pass
    print(f"   {len(enriched)} 檔可用")
    if not enriched:
        print("無足夠資料，結束。")
        return

    # 共同交易日軸
    common = None
    for d in enriched.values():
        idx = d.index
        common = idx if common is None else common.union(idx)
    common = common.sort_values()
    total_days = len(common)
    span = int(args.years * 252)
    start_i = max(MIN_HISTORY, total_days - span)
    end_i = total_days - max(FWD) - 1
    rebal = list(range(start_i, end_i, args.every))
    print(f"③ 換股日 {len(rebal)} 個（{common[start_i].date()} ~ {common[end_i].date()}）")

    # 大盤環境序列：用來檢驗「多頭時有效的模型，空頭是否失效」
    print("④ 判讀各換股日的大盤環境（加權指數）…")
    regime_at = {}
    try:
        tw = yf.download("^TWII", period="5y", auto_adjust=True, progress=False)
        twc = tw["Close"]
        if hasattr(twc, "columns"):
            twc = twc.iloc[:, 0]
        ma60 = twc.rolling(60).mean()
        for i in rebal:
            date = common[i]
            past = twc.loc[:date]
            if len(past) < 70:
                regime_at[date] = "unknown"
                continue
            c = float(past.iloc[-1])
            m = float(ma60.loc[:date].iloc[-1])
            m_prev = float(ma60.loc[:date].iloc[-6])
            if c > m and m >= m_prev:
                regime_at[date] = "多頭"
            elif c < m and m < m_prev:
                regime_at[date] = "空頭"
            else:
                regime_at[date] = "震盪"
    except Exception as e:
        print(f"   （大盤資料取得失敗：{e!r}，略過分環境分析）")
    if regime_at:
        from collections import Counter
        cnt = Counter(regime_at.values())
        print("   " + "、".join(f"{k} {v} 期" for k, v in cnt.most_common()))
    print()

    # 累積結果: model -> horizon -> list of (ret, bench)
    results = {m["name"]: {h: [] for h in FWD} for m in MODELS}
    bench_all = {h: [] for h in FWD}
    coverage = {m["name"]: 0 for m in MODELS}

    for n, i in enumerate(rebal, 1):
        date = common[i]
        # 該日的估值與融資快照（每個換股日各 1 次請求，已快取）
        dstr = date.strftime("%Y%m%d")
        val_map = get_hist_valuation(dstr) if args.valuation else {}
        mgn_map = get_hist_margin(dstr)
        day_sigs = []
        for code, d in enriched.items():
            try:
                sl = d.loc[:date]
                if len(sl) < MIN_HISTORY:
                    continue
                # 流動性：近20日平均成交金額 ≥ 2000萬（用當日以前資料，可還原）
                dv = float((sl["Close"] * sl["Volume"]).tail(20).mean())
                if dv < 2e7:
                    continue
                base = float(sl["Close"].iloc[-1])
                if base <= 0:
                    continue
                # 未來報酬
                pos_full = d.index.get_indexer([date], method="ffill")[0]
                fwd = {}
                ok = True
                for h in FWD:
                    if pos_full + h < len(d):
                        fwd[h] = (float(d["Close"].iloc[pos_full + h]) / base - 1) * 100
                    else:
                        ok = False
                if not ok:
                    continue
                s = build_signals(sl)
                s["fwd"] = fwd
                s["code"] = code
                v = val_map.get(code) or {}
                s["pe"], s["dy"], s["pb"] = v.get("pe"), v.get("dy"), v.get("pb")
                mg = mgn_map.get(code) or {}
                s["mgn_usage"] = mg.get("usage_pct")
                s["mgn_chg"] = mg.get("change_pct")

                # 停損模擬：持有期間任一日最低價跌破停損價 → 以停損價出場
                # （假設能在停損價成交；實際可能跳空跌破，故略為樂觀）
                stop = s.get("stop")
                fwd_sl = {}
                for h in FWD:
                    if stop and stop > 0:
                        lows = d["Low"].iloc[pos_full + 1: pos_full + h + 1]
                        if len(lows) and float(lows.min()) <= stop:
                            fwd_sl[h] = (stop / base - 1) * 100
                        else:
                            fwd_sl[h] = fwd[h]
                    else:
                        fwd_sl[h] = fwd[h]
                s["fwd_sl"] = fwd_sl
                day_sigs.append(s)
            except Exception:
                continue

        if len(day_sigs) < 30:
            continue
        for h in FWD:
            bench_all[h].append(stat.mean(x["fwd"][h] for x in day_sigs))

        for m in MODELS:
            pool = [x for x in day_sigs if (m["filter"](x) if m["filter"] else True)]
            if not pool:
                continue
            top = sorted(pool, key=m["key"], reverse=True)[:args.topn]
            coverage[m["name"]] += 1
            for h in FWD:
                bench = stat.mean(x["fwd"][h] for x in day_sigs)
                # 每個換股日聚合成「一個觀測」：等權買進前N名的組合報酬。
                # 若逐檔記錄，60日窗在5日換股下會重疊12倍，樣本數被灌水、
                # 統計顯著性嚴重高估（偽重複 pseudo-replication）。
                port = stat.mean(t["fwd"][h] for t in top)
                win = sum(1 for t in top if t["fwd"][h] > 0) / len(top) * 100
                port_sl = stat.mean(t["fwd_sl"][h] for t in top)
                # 記下換股日序號，之後才能在「同一天」對齊不同模型（有過濾的模型
                # 可能跳過某些日期），做走查(walk-forward)驗證
                results[m["name"]][h].append(
                    (port, bench, win, port_sl, regime_at.get(date, "unknown"), n))

        if n % 5 == 0 or n == len(rebal):
            print(f"   進度 {n}/{len(rebal)}  ({date.date()}, 當日合格 {len(day_sigs)} 檔)")

    # ── 報表 ──────────────────────────────────────────────────────────────────
    if _val_fetch_failures:
        print(f"\n⚠️ 估值資料被證交所限流失敗 {len(_val_fetch_failures)}/{len(rebal)} 個日期 —— "
              f"估值類模型（低本益比/高殖利率/低P B/長線+估值）樣本不足，**其結論不可採信**。")

    print("\n" + "=" * 104)
    print("回測結果（超額報酬 = 策略平均 − 當日全體平均；正值才代表『選股排序有價值』）")
    print("=" * 104)
    hz_name = {5: "1週(5日)", 20: "1個月(20日)", 60: "3個月(60日)"}
    print(f"\n【基準】買進全部合格股票的等權平均報酬")
    for h in FWD:
        if bench_all[h]:
            print(f"   {hz_name[h]:<12} 平均 {stat.mean(bench_all[h]):+6.2f}%  "
                  f"（{len(bench_all[h])} 個換股日）")

    for h in FWD:
        print(f"\n{'─' * 104}")
        print(f"■ 持有 {hz_name[h]}")
        print(f"{'模型':<18}{'期數':>5}{'個股勝率':>9}{'組合報酬':>10}{'扣成本後':>10}"
              f"{'超額報酬':>10}{'贏基準率':>9}{'t值':>7}{'加停損':>9}  說明")
        rows = []
        for m in MODELS:
            data = results[m["name"]][h]
            if len(data) < 5:
                continue
            ports = [p for p, _, _, _, _, _ in data]
            exc = [p - b for p, b, _, _, _, _ in data]
            wins = [w for _, _, w, _, _, _ in data]
            sl_exc = [ps - b for _, b, _, ps, _, _ in data]
            # t 檢定：超額報酬是否顯著不等於 0（樣本 = 換股日數）
            sd = stat.pstdev(exc) if len(exc) > 1 else 0
            tval = (stat.mean(exc) / (sd / (len(exc) ** 0.5))) if sd > 0 else 0
            rows.append({
                "name": m["name"], "n": len(ports),
                "win": stat.mean(wins),
                "mean": stat.mean(ports),
                "net": stat.mean(ports) - ROUND_TRIP_COST,
                "exc": stat.mean(exc),
                "beat": sum(1 for e in exc if e > 0) / len(exc) * 100,
                "t": tval, "sl_exc": stat.mean(sl_exc), "desc": m["desc"],
            })
        for r in sorted(rows, key=lambda x: -x["exc"]):
            sig = "*" if abs(r["t"]) >= 1.96 else " "
            print(f"{r['name']:<18}{r['n']:>5}{r['win']:>8.1f}%{r['mean']:>9.2f}%"
                  f"{r['net']:>9.2f}%{r['exc']:>9.2f}%{r['beat']:>8.1f}%"
                  f"{r['t']:>6.2f}{sig}{r['sl_exc']:>8.2f}%  {r['desc']}")

    # ── 分大盤環境檢驗：多頭有效的模型，空頭是否失效？ ────────────────────────
    if regime_at and len(set(regime_at.values())) > 1:
        print("\n" + "=" * 104)
        print("■ 分大盤環境：超額報酬（持有1個月）—— 檢驗結論是否只在多頭成立")
        print("=" * 104)
        order = ["多頭", "震盪", "空頭"]
        present = [r for r in order if any(
            rec[4] == r for m in MODELS for rec in results[m["name"]][20])]
        header = f"{'模型':<18}" + "".join(f"{r:>16}" for r in present) + "   說明"
        print(header)
        reg_rows = []
        for m in MODELS:
            data = results[m["name"]][20]
            if len(data) < 5:
                continue
            cells, overall = {}, []
            for r in present:
                sub = [(p - b) for p, b, _, _, rg, _ in data if rg == r]
                cells[r] = (stat.mean(sub), len(sub)) if len(sub) >= 3 else None
                overall.extend(sub)
            reg_rows.append({"name": m["name"], "cells": cells,
                             "mean": stat.mean(overall) if overall else 0,
                             "desc": m["desc"]})
        for row in sorted(reg_rows, key=lambda x: -x["mean"]):
            line = f"{row['name']:<18}"
            for r in present:
                c = row["cells"].get(r)
                line += f"{(f'{c[0]:+.2f}% (n={c[1]})' if c else '—'):>16}"
            print(line + f"   {row['desc']}")
        print("\n※ 若某模型在『空頭』欄轉為負值，代表其優勢僅存在於多頭，實務上需搭配大盤濾網。")

    # ── 走查驗證：依大盤環境切換策略，真的比單押一種好嗎？ ────────────────────
    # 關鍵：用「前半段」挑出各環境最佳模型，只在「後半段」驗證。
    # 若用全期資料挑模型再用全期驗證，等於看答案考試（overfitting），毫無意義。
    if regime_at and len(set(regime_at.values())) > 1:
        print("\n" + "=" * 104)
        print("■ 走查驗證(Walk-forward)：大盤濾網 vs 單一策略　【持有1個月】")
        print("=" * 104)
        H = 20
        all_idx = sorted({rec[5] for m in MODELS for rec in results[m["name"]][H]})
        if len(all_idx) >= 40:
            split = all_idx[len(all_idx) // 2]
            # 各模型：{期序 → (超額, 環境)}
            by_model = {
                m["name"]: {rec[5]: (rec[0] - rec[1], rec[4])
                            for rec in results[m["name"]][H]}
                for m in MODELS
            }

            def mean_exc(name, idxs, regime=None):
                d = by_model[name]
                v = [e for i, (e, rg) in d.items()
                     if i in idxs and (regime is None or rg == regime)]
                return (stat.mean(v), len(v)) if v else (None, 0)

            train = {i for i in all_idx if i < split}
            test = {i for i in all_idx if i >= split}

            # 前半段：挑各環境最佳模型 + 全期最佳單一模型
            picks, lines = {}, []
            for rg in ["多頭", "震盪", "空頭"]:
                cands = [(mean_exc(m["name"], train, rg)[0], m["name"]) for m in MODELS]
                cands = [(v, n) for v, n in cands if v is not None]
                if cands:
                    best = max(cands)
                    picks[rg] = best[1]
                    lines.append(f"{rg}→{best[1]}({best[0]:+.2f}%)")
            single_c = [(mean_exc(m["name"], train)[0], m["name"]) for m in MODELS]
            single_c = [(v, n) for v, n in single_c if v is not None]
            best_single = max(single_c)[1] if single_c else None

            print(f"訓練段（前 {len(train)} 期）挑選結果：")
            print(f"   分環境最佳：{'、'.join(lines)}")
            print(f"   全期最佳單一策略：{best_single}")

            # 後半段：實際驗證（完全未參與挑選）
            sw_vals = []
            for i in sorted(test):
                rg = next((rgv for (_, rgv) in
                           [by_model[m["name"]].get(i, (None, None)) for m in MODELS]
                           if rgv), None)
                pick = picks.get(rg)
                if pick and i in by_model[pick]:
                    sw_vals.append(by_model[pick][i][0])

            print(f"\n測試段（後 {len(test)} 期，完全未參與挑選）：")
            print(f"{'策略':<28}{'期數':>6}{'平均超額報酬':>14}{'勝率':>9}")
            rows_wf = []
            if sw_vals:
                rows_wf.append(("大盤濾網切換策略", len(sw_vals), stat.mean(sw_vals),
                                sum(1 for v in sw_vals if v > 0) / len(sw_vals) * 100))
            for nm in [best_single, "長線+量能確認", "長線", "潛力潛伏", "攻守兼備"]:
                if not nm:
                    continue
                v, n = mean_exc(nm, test)
                if v is not None:
                    vals = [e for i, (e, _) in by_model[nm].items() if i in test]
                    rows_wf.append((f"單押 {nm}", n, v,
                                    sum(1 for x in vals if x > 0) / len(vals) * 100))
            seen_wf = set()
            for nm, n, v, wr in rows_wf:
                if nm in seen_wf:
                    continue
                seen_wf.add(nm)
                print(f"{nm:<28}{n:>6}{v:>13.2f}%{wr:>8.1f}%")
            print("\n※ 若『大盤濾網切換』沒有明顯勝過單押最佳策略，代表切換的複雜度不值得，"
                  "\n   或環境判斷本身有延遲（等你看出是空頭，跌勢往往已走完一段）。")
        else:
            print("   換股日不足，略過走查驗證。")

    print("\n" + "=" * 104)
    print("讀表說明：")
    print("  · 每個換股日聚合為 1 個觀測（等權買進前N名的組合報酬），避免重疊窗造成樣本灌水")
    print(f"  · 扣成本後 = 組合報酬 − {ROUND_TRIP_COST}%（手續費0.1425%×2 + 證交稅0.3%）")
    print("  · 超額報酬 = 組合報酬 − 當日全體合格股等權報酬；**這才是選股能力的證據**")
    print("  · t值 ≥1.96 標示 * 代表超額報酬統計上顯著；否則與『運氣』無法區分")
    print("\n重要限制：")
    print("  · 僅檢驗價量核心；基本面與新聞無歷史快照，其貢獻未被驗證")
    print("  · 存活者偏誤：用『現在還在市』的股票回測，已下市/崩壞的公司不在樣本內，結果偏樂觀")
    print("  · 60日窗在5日換股下仍有重疊，t值仍略微高估顯著性")


if __name__ == "__main__":
    main()
