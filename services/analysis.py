"""
共用分析核心 —— 個股分析與智能選股使用「同一套」計分邏輯。

過去兩邊各自實作，導致同一檔股票在兩頁分數不同（實測台積電：智能選股綜合 69／技術 47，
個股分析綜合 74／技術 63）。原因有三：
  1. 取樣長度不同（選股 6mo vs 個股 1y）— 6mo 只有 127 根K線，MA120 僅 8 個有效值，
     長期趨勢指標形同失效；且「52週位置」實際只算了 6 個月。
  2. 融資趨勢視窗不同（10日 vs 20日）→ 趨勢標籤與扣分不同。
  3. 個股分析會套用盤中即時價，選股不會。

因此這裡統一：一律以 SCORING_PERIOD 的歷史計算指標（確保 MA120／Vol_MA60／52週區間
都有效），統一融資視窗，統一盤中價處理。顯示用的期間只影響「圖表切片」，不影響評分。
"""

import pandas as pd

from services.stock_data import (
    get_stock_data, get_intraday_price, POPULAR_STOCKS,
)
from services.technical import (
    calculate_indicators, calculate_technical_score, calculate_horizon_scores,
    analyze_volume_price, calculate_risk_plan,
)
from services.fundamental import analyze_fundamentals, calculate_fundamental_score
from services.news import get_all_news, calculate_news_sentiment_score, get_catalysts
from services.stock_data import get_news
from services.target_price import calculate_target_price
from services.recommendation import (
    generate_recommendation, generate_timeframe_recommendations, build_rationale,
)
from services.margin import get_margin_data, get_margin_trend, calculate_margin_signal
from services.market import get_market_regime
from services.potential import calculate_potential_score

# Indicators need long history to be valid (MA120, Vol_MA60, 52-week range).
# Both pages fetch this same period so they also share one cache entry.
SCORING_PERIOD = "2y"
BACKTEST_PERIOD = "5y"

# Unified margin-trend window (was 10 in the screener, 20 in the detail page)
MARGIN_TREND_DAYS = 20

# Trading rows per display period (chart slicing only — never affects scoring)
PERIOD_ROWS = {"3mo": 63, "6mo": 126, "1y": 252, "2y": 504, "3y": 756}


def prepare_frame(stock_id: str, as_of_date=None, use_intraday: bool = True):
    """
    Fetch and prepare the scoring frame.

    Returns (df, df_full, intraday, has_intraday):
      df       — indicator-enriched frame, truncated to as_of_date in backtest mode
      df_full  — the same frame WITHOUT truncation (for backtest verification)
      intraday — the intraday quote dict (or {})
      has_intraday — whether today's bar was patched with a live quote
    """
    is_backtest = as_of_date is not None
    raw = get_stock_data(stock_id, BACKTEST_PERIOD if is_backtest else SCORING_PERIOD)
    if raw is None or raw.empty:
        return None, None, {}, False

    intraday, has_intraday = {}, False
    if use_intraday and not is_backtest:
        intraday = get_intraday_price(stock_id) or {}
        if intraday and raw.index[-1].date() == intraday["timestamp"].date():
            has_intraday = True
            li = raw.index[-1]
            raw.loc[li, "Close"] = intraday["price"]
            raw.loc[li, "High"] = max(raw.loc[li, "High"], intraday["high"])
            raw.loc[li, "Low"] = min(raw.loc[li, "Low"], intraday["low"])

    # Indicators are causal (rolling/ewm look only backwards), so computing on the
    # full series then truncating equals computing on the truncated series.
    df_full = calculate_indicators(raw)
    df = df_full
    if is_backtest:
        df = df_full[df_full.index.date <= as_of_date]
    return df, df_full, intraday, has_intraday


def compute_scores(df, info, financials, stock_id, company_name, as_of_date=None,
                   skip_news=False):
    """
    The single source of truth for every score both pages display.
    `df` must already be indicator-enriched (see prepare_frame).

    skip_news: 跳過新聞抓取（每檔約 6 秒，是掃描最慢的一環）。新聞面佔綜合評分 15%，
               但因為沒有歷史新聞快照，它的貢獻**從未被回測驗證**——既不能說有效，
               也不能說無效。關掉可讓掃描快 2–3 倍，代價是消息面以中性 50 計。
    """
    as_of_str = as_of_date.strftime("%Y%m%d") if as_of_date else ""

    tech_score, tech_reasons = calculate_technical_score(df)
    fundamentals = analyze_fundamentals(info, financials)
    fund_score, fund_reasons = calculate_fundamental_score(info, fundamentals)

    if skip_news:
        all_news, catalysts = [], {"positive": [], "negative": []}
        news_score, news_reasons = 50, ["（已略過新聞分析以加速掃描，消息面以中性 50 計）"]
    else:
        all_news = get_all_news(stock_id, company_name, get_news(stock_id))
        news_score, news_reasons = calculate_news_sentiment_score(all_news)
        catalysts = get_catalysts(all_news)

    tp = calculate_target_price(df, info, fundamentals)
    target_upside = tp.get("upside_pct")

    # Margin: level + balance trend over a unified window, compared against the
    # price move over the SAME window (套牢 / 追高 / 惜售).
    margin_trend = get_margin_trend(stock_id, days=MARGIN_TREND_DAYS, as_of=as_of_str)
    price_chg_trend = None
    if margin_trend and len(df) > len(margin_trend):
        base = df["Close"].iloc[-(len(margin_trend) + 1)]
        if base:
            price_chg_trend = (df["Close"].iloc[-1] / base - 1) * 100
    margin_signal = calculate_margin_signal(
        get_margin_data(stock_id, as_of=as_of_str),
        price_chg_pct=price_chg_trend, trend=margin_trend,
    )

    volume_signal = analyze_volume_price(df)
    market_regime = get_market_regime(
        as_of=as_of_date.strftime("%Y-%m-%d") if as_of_date else ""
    )

    rec = generate_recommendation(
        tech_score, fund_score, news_score,
        tech_reasons, fund_reasons, news_reasons,
        target_upside_pct=target_upside,
        margin_signal=margin_signal,
        volume_signal=volume_signal,
        market_regime=market_regime,
    )
    rationale = build_rationale(rec, volume_signal=volume_signal, margin_signal=margin_signal)
    risk_plan = calculate_risk_plan(df, target_price=tp.get("recommended_target"))

    horizon_tech = calculate_horizon_scores(df)
    timeframe_recs = generate_timeframe_recommendations(
        horizon_tech, fund_score, news_score,
        target_upside_pct=target_upside,
        margin_signal=margin_signal,
        volume_signal=volume_signal,
    )

    potential = calculate_potential_score(
        df, info, fundamentals, news_score, catalysts, target_upside
    )

    return {
        "fundamentals": fundamentals,
        "tech_score": tech_score, "fund_score": fund_score, "news_score": news_score,
        "all_news": all_news, "catalysts": catalysts,
        "tp": tp, "target_upside": target_upside,
        "margin_signal": margin_signal, "margin_trend": margin_trend,
        "volume_signal": volume_signal, "market_regime": market_regime,
        "rec": rec, "rationale": rationale, "risk_plan": risk_plan,
        "horizon_tech": horizon_tech, "timeframe_recs": timeframe_recs,
        "potential": potential,
    }
