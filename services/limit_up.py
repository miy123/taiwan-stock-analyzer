"""
Find Taiwan stocks with consecutive limit-up (連日漲停) momentum.

Strategy:
  1. TWSE STOCK_DAY_ALL CSV → confirmed today's limit-up stocks (fast, accurate)
  2. Candidate pool: today_lu + POPULAR_STOCKS + OTC_STOCKS (~80 stocks)
  3. yfinance batch-fetch 15-day history → compute streak metrics for every candidate
  4. Rank by (max_streak DESC, last_days_ago ASC, volume DESC)

Streak metrics:
  max_streak      — longest consecutive limit-up run in the 15-day window
  trailing_streak — consecutive limit-up days ending at the LATEST available day
  last_days_ago   — 0 = today, 1 = yesterday, 999 = never hit in window
"""

import re
import csv
import requests
import streamlit as st

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TW-Stock-Analyzer/1.0)"}
_LIMIT_THRESHOLD = 9.5   # %; Taiwan daily limit is +10%, 9.5 catches near-limit too
_MIN_VOLUME      = 100_000  # shares — filter out illiquid / suspended stocks


# ── 1. TWSE today's data ──────────────────────────────────────────────────────

@st.cache_data(ttl=900, show_spinner=False)
def _fetch_twse_today() -> list:
    """
    Fetch today's TWSE listing via STOCK_DAY_ALL CSV endpoint.
    Returns list of all 4-digit stocks that hit ≥9.5% change today.
    """
    try:
        resp = requests.get(
            "https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=json",
            timeout=15, headers=_HEADERS,
        )
        resp.raise_for_status()

        lines = resp.text.strip().splitlines()
        if not lines:
            return []

        reader  = csv.reader(lines)
        headers = [h.strip().strip('"') for h in (next(reader, None) or [])]

        def col(name):
            return next((i for i, h in enumerate(headers) if name in h), None)

        code_col  = col("證券代號")
        name_col  = col("證券名稱")
        close_col = col("收盤價")
        chg_col   = col("漲跌價差")
        vol_col   = col("成交股數")

        if None in (code_col, close_col, chg_col):
            return []

        results = []
        for row in reader:
            try:
                row   = [c.strip().strip('"') for c in row]
                code  = row[code_col].strip()
                if not re.match(r'^\d{4}$', code):
                    continue

                close = float(row[close_col].replace(",", ""))
                chg   = float(row[chg_col].replace(",", ""))
                prev  = close - chg
                if prev <= 0:
                    continue

                chg_pct = chg / prev * 100
                if chg_pct < _LIMIT_THRESHOLD:
                    continue

                vol = 0
                if vol_col is not None:
                    try:
                        vol = int(row[vol_col].replace(",", ""))
                    except (ValueError, AttributeError):
                        pass
                if vol < _MIN_VOLUME:
                    continue

                name = row[name_col].strip() if name_col is not None else ""
                results.append({
                    "stock_id":   code,
                    "name":       name,
                    "change_pct": round(chg_pct, 2),
                    "volume":     vol,
                })
            except (ValueError, IndexError, AttributeError):
                continue

        return results
    except Exception:
        return []


# ── 2. Streak computation via yfinance ────────────────────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def _compute_streaks(stock_ids_tuple: tuple, lookback: int = 15) -> dict:
    """
    Batch-fetch yfinance price history for stock_ids and return streak metrics.

    Args:
        stock_ids_tuple: tuple of 4-digit stock IDs (must be tuple for cache key)
        lookback: trading days to inspect

    Returns:
        {stock_id: {"max_streak": int, "trailing_streak": int, "last_days_ago": int}}
    """
    import yfinance as yf
    from services.stock_data import OTC_STOCKS

    stock_ids = list(stock_ids_tuple)
    if not stock_ids:
        return {}

    tickers      = [f"{sid}.TWO" if sid in OTC_STOCKS else f"{sid}.TW" for sid in stock_ids]
    id_by_ticker = dict(zip(tickers, stock_ids))

    try:
        period = f"{lookback + 5}d"
        hist   = yf.download(tickers, period=period, auto_adjust=True, progress=False)

        # hist["Close"] is a DataFrame with ticker columns for multi-download
        close_df = hist["Close"]
        # If only one ticker, yfinance returns a Series → convert to DataFrame
        if hasattr(close_df, "name"):
            close_df = close_df.to_frame(name=tickers[0])

        results = {}
        for ticker, sid in id_by_ticker.items():
            if ticker not in close_df.columns:
                continue

            series = close_df[ticker].dropna()
            if len(series) < 2:
                continue

            pct_vals = (series.pct_change().dropna() * 100).tolist()

            # Max streak and trailing streak (consecutive ending at latest day)
            max_s = current = 0
            for v in pct_vals:
                if v >= _LIMIT_THRESHOLD:
                    current += 1
                    max_s = max(max_s, current)
                else:
                    current = 0
            trailing = current  # still at end of loop = trailing streak

            # Days since last limit-up
            last_ago = None
            for i, v in enumerate(reversed(pct_vals)):
                if v >= _LIMIT_THRESHOLD:
                    last_ago = i
                    break

            results[sid] = {
                "max_streak":      max_s,
                "trailing_streak": trailing,
                "last_days_ago":   last_ago if last_ago is not None else 999,
            }

        return results
    except Exception:
        return {}


# ── 3. Main public function ───────────────────────────────────────────────────

_MAX_DAYS_AGO = 5   # filter out stocks whose last limit-up was > 5 trading days ago


@st.cache_data(ttl=1800, show_spinner=False)
def get_limit_up_stocks(top_n: int = 30, min_streak: int = 2) -> list:
    """
    Return stocks with consecutive limit-up (連日漲停) momentum.

    Candidates:
      - Today's TWSE limit-up stocks (confirmed by TWSE CSV)
      - POPULAR_STOCKS + OTC_STOCKS (checked via yfinance history)

    Recency filter: last limit-up must be within _MAX_DAYS_AGO trading days.

    Today-correction: yfinance data lags the current session. For stocks
    confirmed today by TWSE CSV, we patch trailing_streak and last_days_ago
    to reflect that today IS a limit-up day.

    Sorted by: max_streak DESC → last_days_ago ASC → volume DESC

    Each entry:
        {"stock_id", "name", "change_pct", "volume", "exchange",
         "max_streak", "trailing_streak", "last_days_ago", "is_today_lu"}
    """
    from services.stock_data import POPULAR_STOCKS, OTC_STOCKS

    today_lu    = _fetch_twse_today()
    today_by_id = {s["stock_id"]: s for s in today_lu}
    today_ids   = set(today_by_id)

    candidate_ids = sorted(today_ids | set(POPULAR_STOCKS) | set(OTC_STOCKS))
    streak_info   = _compute_streaks(tuple(candidate_ids), lookback=15)

    results = []
    for sid, sdata in streak_info.items():
        max_s    = sdata["max_streak"]
        trailing = sdata["trailing_streak"]
        last_ago = sdata["last_days_ago"]

        is_today = sid in today_ids

        # ── Patch for today's TWSE-confirmed limit-up ─────────────────────────
        # yfinance data lags the live session; TWSE CSV is the ground truth.
        # If today is confirmed: extend the trailing streak by 1 and set last_ago=0.
        if is_today:
            trailing += 1          # today adds one more day to the streak
            max_s    = max(max_s, trailing)
            last_ago = 0

        # ── Recency filter ────────────────────────────────────────────────────
        if last_ago > _MAX_DAYS_AGO:
            continue

        # ── Min-streak filter ─────────────────────────────────────────────────
        if max_s < min_streak and not is_today:
            continue
        if max_s == 0 and not is_today:
            continue

        lu_today = today_by_id.get(sid, {})
        name     = lu_today.get("name") or POPULAR_STOCKS.get(sid, "")
        volume   = lu_today.get("volume", 0)
        change   = lu_today.get("change_pct", 0.0)
        exchange = "TPEX" if sid in OTC_STOCKS else "TWSE"

        results.append({
            "stock_id":        sid,
            "name":            name,
            "change_pct":      change,
            "volume":          volume,
            "exchange":        exchange,
            "max_streak":      max_s,
            "trailing_streak": trailing,
            "last_days_ago":   last_ago,
            "is_today_lu":     is_today,
        })

    # Sort: longest streak first; among ties, more recent is better; then volume
    results.sort(
        key=lambda x: (x["max_streak"], -x["last_days_ago"], x["volume"]),
        reverse=True,
    )
    return results[:top_n]
