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

# 手動維護的上櫃清單，僅作為離線 fallback。
# ⚠️ 舊版這裡把大立光(3008)、緯穎(6669)、緯創(3231) 誤列為上櫃——它們其實都是
# **上市**，導致程式去要 .TWO 代號而拿不到（或拿到錯的）資料。現已改為以櫃買中心
# 官方清單為準（見 _is_otc），此表只在 API 不可用時墊底。
OTC_STOCKS = {
    "6488": "環球晶",
    "5347": "世界",
    "8069": "元太",
    "6510": "精測",
}


def _is_otc(stock_id: str) -> bool:
    """以櫃買中心官方清單判斷是否為上櫃；取不到時退回內建表。"""
    try:
        from services.universe import get_otc_snapshot
        otc = get_otc_snapshot()
        if otc:
            return stock_id in otc
    except Exception:
        pass
    return stock_id in OTC_STOCKS


def _get_ticker_symbol(stock_id: str) -> str:
    return f"{stock_id}.TWO" if _is_otc(stock_id) else f"{stock_id}.TW"


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
    """
    yfinance 財務報表 —— **只給個股分析頁的營收／淨利走勢圖用**。

    ROE／淨利率／營收成長／負債比一律走公開資訊觀測站的批次財報
    （services/financials.py），不從這裡拿，否則兩條路徑會是兩把尺。

    ⚠️ 這裡原本還抓 balance_sheet / cash_flow / quarterly_balance 三張表，
    每張都是一次獨立請求，但**全專案沒有任何地方讀它們**——
    `fundamental.analyze_fundamentals()` 只讀 income_stmt 與 quarterly_income。
    已移除；要加回來之前先確認真的有呼叫端。
    """
    ticker = _get_ticker_symbol(stock_id)
    stock = yf.Ticker(ticker)
    try:
        return {
            "income_stmt": stock.financials,
            "quarterly_income": stock.quarterly_financials,
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
