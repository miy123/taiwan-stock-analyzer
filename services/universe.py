"""
全上市股票掃描（完整版）。

原本選股只掃 31 檔手動維護的熱門股（全市場約 1986 檔，覆蓋率 1.6%）——這對「找還沒漲
的潛伏股」傷害最大，因為真正沒被發現的標的多在中小型股，只看權值股等於在最不可能有
遺珠的地方挖遺珠。

完整掃描靠三個「批次」資料源，避免逐檔慢查（逐檔完整分析要 3~9 秒，1000 檔要數小時）：

  1. yfinance 批次下載：60 檔 2 年日線只要 1.7 秒 → 全市場約 30 秒
  2. 證交所 BWIBBU_ALL：1081 檔的本益比／殖利率／股價淨值比，一次 0.08 秒
  3. 證交所 STOCK_DAY_ALL：全市場當日成交量值，一次 0.1 秒
  （融資另有全市場每日表，見 services/margin.py）

因此「技術面、量價、低基期位階、融資籌碼、估值、風報比」這些決定『有沒有潛力』的維度，
**每一檔上市股都會被真的算過**，不是抽樣或粗篩。

只有兩項無法批次取得，改為「入圍後再深查」：
  · 新聞情緒／催化劑（話題）— 逐檔抓 RSS 很慢
  · 詳細財報（ROE／營收成長／淨利率）— 逐檔 yfinance 約 2.5 秒
粗掃階段這兩項對所有股票給相同的中性值，因此**不影響彼此排名**，入圍者再補齊。
"""

import math

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

_BWIBBU = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
_DAY_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _f(v):
    try:
        x = float(str(v).replace(",", "").strip())
        return x if x != 0 else None
    except (TypeError, ValueError):
        return None


@st.cache_data(ttl=10800, show_spinner=False)
def get_listed_snapshot() -> dict:
    """
    All TWSE-listed *common stocks* with today's quote + valuation ratios.
    Returns {code: {name, close, volume, turnover, pe, pb, dy}}.
    ETFs / warrants / 受益證券 are excluded (only 4-digit codes are kept).
    """
    out = {}
    try:
        for d in requests.get(_DAY_ALL, timeout=30, headers=_HEADERS).json():
            code = str(d.get("Code", "")).strip()
            # Common stocks are 1101–9999; codes starting with 0 are ETFs (0050…)
            if len(code) != 4 or not code.isdigit() or code.startswith("0"):
                continue
            out[code] = {
                "name": str(d.get("Name", "")).strip(),
                "close": _f(d.get("ClosingPrice")),
                "volume": _f(d.get("TradeVolume")) or 0,
                "turnover": _f(d.get("TradeValue")) or 0,   # 成交金額 (元)
                "pe": None, "pb": None, "dy": None,
            }
    except Exception:
        return {}

    try:
        for d in requests.get(_BWIBBU, timeout=30, headers=_HEADERS).json():
            code = str(d.get("Code", "")).strip()
            if code in out:
                out[code]["pe"] = _f(d.get("PEratio"))
                out[code]["pb"] = _f(d.get("PBratio"))
                dy = _f(d.get("DividendYield"))
                # BWIBBU DividendYield is a percent number (3.29 = 3.29%)
                out[code]["dy"] = dy / 100 if dy else None
    except Exception:
        pass

    return out


def download_history_bulk(codes, period="2y", chunk=120, progress_cb=None):
    """
    Batch-download daily history for many codes. Returns {code: DataFrame}.
    yfinance batches each request, so this is ~100x faster than looping.
    """
    frames = {}
    total = len(codes)
    for i in range(0, total, chunk):
        part = codes[i:i + chunk]
        tickers = [c + ".TW" for c in part]
        try:
            data = yf.download(
                tickers, period=period, auto_adjust=True, progress=False,
                threads=True, group_by="ticker",
            )
        except Exception:
            data = None
        if data is not None and not data.empty:
            lvl0 = set(data.columns.get_level_values(0)) if hasattr(data.columns, "levels") else set()
            for code, tk in zip(part, tickers):
                try:
                    if tk in lvl0:
                        d = data[tk].dropna(how="all")
                        if len(d) >= 150 and d["Close"].notna().sum() >= 150:
                            frames[code] = d
                except Exception:
                    continue
        if progress_cb:
            progress_cb(min(i + chunk, total), total)
    return frames


def scan_universe(min_turnover=1e7, period="2y", progress_cb=None):
    """
    Score EVERY liquid listed stock on the bulk-available dimensions.

    Deliberately neutral placeholders are used for the two things that can't be
    fetched in bulk (news sentiment → 50, target-price upside → None). They are
    identical for every stock, so they don't distort the relative ranking; the
    finalists get the real values in the enrichment pass.

    Returns list of row dicts (preliminary scores), plus the snapshot used.
    """
    from services.technical import (
        calculate_indicators, calculate_technical_score, calculate_horizon_scores,
        analyze_volume_price, calculate_risk_plan,
    )
    from services.fundamental import calculate_fundamental_score
    from services.recommendation import (
        generate_recommendation, generate_timeframe_recommendations,
    )
    from services.potential import calculate_potential_score
    from services.margin import get_latest_margin_table, calculate_margin_signal
    from services.market import get_market_regime

    snap = get_listed_snapshot()
    codes = sorted(c for c, v in snap.items() if (v.get("turnover") or 0) >= min_turnover)
    frames = download_history_bulk(codes, period=period, progress_cb=progress_cb)

    _, margin_table = get_latest_margin_table()
    regime = get_market_regime()
    rows = []

    for code, raw in frames.items():
        try:
            df = calculate_indicators(raw)
            if len(df) < 150:
                continue
            meta = snap.get(code, {})

            tech_score, _ = calculate_technical_score(df)
            # Partial fundamentals from the bulk valuation feed
            fundamentals = {
                "pe_ratio": meta.get("pe"), "pb_ratio": meta.get("pb"),
                "dividend_yield": meta.get("dy"),
            }
            fund_score, _ = calculate_fundamental_score({}, fundamentals)

            volume_signal = analyze_volume_price(df)
            m_raw = margin_table.get(code, {})
            margin_signal = calculate_margin_signal(dict(m_raw)) if m_raw else \
                calculate_margin_signal({})

            rec = generate_recommendation(
                tech_score, fund_score, 50, [], [], [],
                target_upside_pct=None, margin_signal=margin_signal,
                volume_signal=volume_signal, market_regime=regime,
            )
            horizon_tech = calculate_horizon_scores(df)
            tfr = generate_timeframe_recommendations(
                horizon_tech, fund_score, 50, target_upside_pct=None,
                margin_signal=margin_signal, volume_signal=volume_signal,
            )
            potential = calculate_potential_score(
                df, {}, fundamentals, 50, {"positive": [], "negative": []}, None
            )
            risk = calculate_risk_plan(df)

            close = float(df["Close"].iloc[-1])
            prev = float(df["Close"].iloc[-2]) if len(df) > 1 else close

            # Shortlist ranking must use only signals that ACTUALLY vary in the
            # bulk pass. buzz / upside / prospect are neutral placeholders here,
            # so ranking on the raw 潛力分 would tie everyone — rank on the real
            # varying parts (低基期 / 技術 / 估值) instead, then enrich the top.
            lb = potential.get("low_base", 50)
            prelim_sleeper = 0.60 * lb + 0.25 * tech_score + 0.15 * fund_score
            prelim_balanced = math.sqrt(max(rec["total_score"], 0) * max(lb, 0))
            # 回測最佳策略（長線分 + 量價未轉弱）的初篩分數
            long_sc = next((h["score"] for h in tfr if h["key"] == "long"), 50)
            v_adj = volume_signal.get("score_adj", 0)
            prelim_bestproven = long_sc + (5 if v_adj >= 0 else -15)
            # 超低本益比：越低越前面（排序用負值）。排除 <3 倍者——多半是業外一次性
            # 收益灌大 EPS 造成的假低估（價值陷阱），而非真的便宜。
            _pe = meta.get("pe")
            prelim_lowpe = (-_pe if (_pe is not None and 3 <= _pe <= 100) else -9999)

            rows.append({
                "prelim_sleeper": round(prelim_sleeper, 1),
                "prelim_momentum": rec["total_score"],
                "prelim_balanced": round(prelim_balanced, 1),
                "prelim_bestproven": round(prelim_bestproven, 1),
                "prelim_lowpe": round(prelim_lowpe, 2),
                # bestproven 的最終過濾需要它（先前只存在深度分析結果中）
                "volume_adj": v_adj,
                "stock_id": code,
                "company_name": meta.get("name") or code,
                "current_price": close,
                "change_pct": (close / prev - 1) * 100 if prev else 0.0,
                "total_score": rec["total_score"],
                "action": rec["action"], "action_en": rec["action_en"],
                "color": rec["color"], "icon": rec["icon"],
                "tech_score": tech_score, "fund_score": fund_score, "news_score": 50,
                "target_price": None, "upside_pct": None,
                "pe_ratio": meta.get("pe"), "dividend_yield": meta.get("dy"),
                "revenue_growth": None,
                "margin_usage": margin_signal.get("usage_pct"),
                "margin_level": margin_signal.get("level"),
                "margin_penalty": margin_signal.get("penalty", 0),
                "potential": potential,
                "rr": (risk or {}).get("rr"),
                "atr_pct": (risk or {}).get("atr_pct"),
                "stop_pct": (risk or {}).get("stop_pct"),
                "horizon": {h["key"]: {"score": h["score"], "action": h["action"],
                                       "icon": h["icon"], "color": h["color"],
                                       "name": h["name"], "span": h["span"]}
                            for h in tfr},
                "turnover": meta.get("turnover"),
                "is_limit_up": False, "max_streak": 0, "last_days_ago": 0,
                "exchange": "TWSE", "limit_up_pct": None,
                "preliminary": True,   # news / target price not yet fetched
            })
        except Exception:
            continue

    return rows, snap
