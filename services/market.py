"""
大盤環境（market regime）判讀。

同一個 65 分，在多頭與空頭市場的意義完全不同：台股個股與大盤（加權指數）連動極高，
空頭時再好的個股也常被拖累。因此依大盤趨勢動態調整「買進門檻」——空頭時把門檻提高
（要更強才建議買），多頭時略為放寬，避免在系統性風險升高時大量發出買進訊號。
"""

import pandas as pd
import streamlit as st
import yfinance as yf

_TAIEX = "^TWII"


@st.cache_data(ttl=3600, show_spinner=False)
def get_index_forward_return(as_of: str, hold_days: int) -> dict:
    """
    TAIEX return from the last trading day on/before `as_of` (YYYY-MM-DD) forward
    `hold_days` trading days — the benchmark a strategy must beat.
    Returns {'ret', 'base_date', 'end_date'} or {}.
    """
    try:
        df = yf.Ticker(_TAIEX).history(period="5y", auto_adjust=True)
    except Exception:
        return {}
    if df is None or df.empty:
        return {}
    try:
        cutoff = pd.to_datetime(as_of).date()
    except Exception:
        return {}
    past = df[df.index.date <= cutoff]
    if past.empty:
        return {}
    pos = len(past) - 1
    closes = df["Close"]
    if pos + hold_days >= len(df):
        return {}
    base, end = float(closes.iloc[pos]), float(closes.iloc[pos + hold_days])
    return {
        "ret": (end / base - 1) * 100,
        "base_date": df.index[pos].date(),
        "end_date": df.index[pos + hold_days].date(),
    }


@st.cache_data(ttl=3600, show_spinner=False)
def get_market_regime(as_of: str = "") -> dict:
    """
    Classify the TAIEX trend into 多頭 / 中性 / 空頭.

    as_of: 'YYYY-MM-DD' — truncate history to that date for backtest mode.

    Returns dict with 'regime', 'label', 'color', 'threshold_adj' (points added to
    the buy thresholds), 'ma_note', 'r60', 'above_ma60', 'above_ma120'.
    """
    neutral = {
        "regime": "unknown", "label": "無法取得大盤資料", "color": "#78909c",
        "threshold_adj": 0, "ma_note": "", "r60": None,
        "above_ma60": None, "above_ma120": None, "index_close": None,
    }
    try:
        df = yf.Ticker(_TAIEX).history(period="2y", auto_adjust=True)
    except Exception:
        return neutral
    if df is None or df.empty or len(df) < 130:
        return neutral

    if as_of:
        try:
            cutoff = pd.to_datetime(as_of).date()
            df = df[df.index.date <= cutoff]
        except Exception:
            pass
        if len(df) < 130:
            return neutral

    close = df["Close"]
    cur = float(close.iloc[-1])
    ma60 = float(close.rolling(60).mean().iloc[-1])
    ma120 = float(close.rolling(120).mean().iloc[-1])
    ma60_prev = float(close.rolling(60).mean().iloc[-6])
    r60 = (cur / float(close.iloc[-61]) - 1) * 100 if len(close) > 61 else 0.0

    above60 = cur > ma60
    above120 = cur > ma120
    ma60_rising = ma60 > ma60_prev

    # Score the regime: price vs the two long MAs + slope
    pts = sum([above60, above120, ma60_rising, r60 > 0])

    if pts >= 4:
        regime, label, color, adj = "bull", "多頭市場", "#4caf50", -2
        note = "指數站上季線與半年線且趨勢向上，順風環境，買進門檻略放寬"
    elif pts >= 3:
        regime, label, color, adj = "mild_bull", "偏多整理", "#a9e34b", 0
        note = "指數多數條件偏多，環境中性偏正面"
    elif pts >= 2:
        regime, label, color, adj = "neutral", "區間整理", "#ff9800", +2
        note = "指數多空交錯，個股表現分歧，買進門檻略提高"
    elif pts >= 1:
        regime, label, color, adj = "mild_bear", "偏空", "#ff7043", +5
        note = "指數轉弱，系統性風險升高，買進門檻提高"
    else:
        regime, label, color, adj = "bear", "空頭市場", "#f44336", +8
        note = "指數跌破季線與半年線且趨勢向下，逆風環境，大幅提高買進門檻"

    return {
        "regime": regime,
        "label": label,
        "color": color,
        "threshold_adj": adj,
        "ma_note": note,
        "r60": r60,
        "above_ma60": above60,
        "above_ma120": above120,
        "index_close": cur,
    }
