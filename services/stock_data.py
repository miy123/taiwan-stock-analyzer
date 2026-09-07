import yfinance as yf
import pandas as pd
import streamlit as st

POPULAR_STOCKS = {
    "2330": "台積電",
    "2317": "鴻海",
    "2454": "聯發科",
    "2412": "中華電信",
    "2308": "台達電",
    "2882": "國泰金",
    "2881": "富邦金",
    "2886": "兆豐金",
    "6505": "台塑化",
    "1301": "台塑",
    "1303": "南亞",
    "2002": "中鋼",
    "2303": "聯電",
    "3711": "日月光投控",
    "2379": "瑞昱",
    "2395": "研華",
    "3034": "聯詠",
    "2382": "廣達",
    "2357": "華碩",
    "2353": "宏碁",
    "2603": "長榮",
    "2615": "萬海",
    "2609": "陽明",
    "5880": "合庫金",
    "2884": "玉山金",
    "2885": "元大金",
    "2891": "中信金",
    "2892": "第一金",
    "2207": "和泰車",
    "2105": "正新",
    "8039": "台虹",
}

OTC_STOCKS = {
    "6488": "環球晶",
    "3008": "大立光",
    "6669": "緯穎",
    "3231": "緯創",
}


def _get_ticker_symbol(stock_id: str) -> str:
    if stock_id in OTC_STOCKS:
        return f"{stock_id}.TWO"
    return f"{stock_id}.TW"


@st.cache_data(ttl=1800, show_spinner=False)
def get_stock_data(stock_id: str, period: str = "1y") -> pd.DataFrame:
    ticker = _get_ticker_symbol(stock_id)
    stock = yf.Ticker(ticker)
    df = stock.history(period=period, auto_adjust=True)
    if df.empty:
        # Try OTC
        stock = yf.Ticker(f"{stock_id}.TWO")
        df = stock.history(period=period, auto_adjust=True)
    return df


@st.cache_data(ttl=60, show_spinner=False)
def get_intraday_price(stock_id: str) -> dict:
    """Best-effort near-real-time quote using today's 1-minute bars (short TTL cache)."""
    ticker = _get_ticker_symbol(stock_id)
    stock = yf.Ticker(ticker)
    intraday = stock.history(period="1d", interval="1m", auto_adjust=True)
    if intraday.empty:
        # Try OTC
        stock = yf.Ticker(f"{stock_id}.TWO")
        intraday = stock.history(period="1d", interval="1m", auto_adjust=True)
    if intraday.empty:
        return {}
    return {
        "price": float(intraday["Close"].iloc[-1]),
        "high": float(intraday["High"].max()),
        "low": float(intraday["Low"].min()),
        "timestamp": intraday.index[-1],
    }


@st.cache_data(ttl=3600, show_spinner=False)
def get_ticker_info(stock_id: str) -> dict:
    ticker = _get_ticker_symbol(stock_id)
    try:
        info = yf.Ticker(ticker).info
        if not info or info.get("regularMarketPrice") is None:
            info = yf.Ticker(f"{stock_id}.TWO").info
        return info
    except Exception:
        return {}


@st.cache_data(ttl=86400, show_spinner=False)
def get_financials(stock_id: str) -> dict:
    ticker = _get_ticker_symbol(stock_id)
    stock = yf.Ticker(ticker)
    try:
        return {
            "income_stmt": stock.financials,
            "balance_sheet": stock.balance_sheet,
            "cash_flow": stock.cashflow,
            "quarterly_income": stock.quarterly_financials,
            "quarterly_balance": stock.quarterly_balance_sheet,
        }
    except Exception:
        return {}


@st.cache_data(ttl=1800, show_spinner=False)
def get_news(stock_id: str) -> list:
    ticker = _get_ticker_symbol(stock_id)
    try:
        stock = yf.Ticker(ticker)
        news = stock.news
        return news if news else []
    except Exception:
        return []
