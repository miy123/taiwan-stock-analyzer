"""
Find Taiwan stocks with consecutive limit-up (連日漲停) momentum.

⚠️ **全市場的連日漲停不是在這裡算的。** `universe.scan_universe()` 逐檔跑過
2 年日線時就順手呼叫 `streaks_from_closes()` 算好了，涵蓋全上市櫃、零額外請求。

這個模組現在負責的是**今日確認漲停**（證交所／櫃買當日清單才有當日漲幅，
而 yfinance 日線會落後盤中），以及熱門股池模式那條不跑全市場掃描的路徑。

Strategy:
  1. TWSE STOCK_DAY_ALL CSV + 櫃買日收盤 → 今日確認漲停股（上市＋上櫃）
  2. Candidate pool: today_lu + POPULAR_STOCKS + OTC_STOCKS
     （⚠️ 這個池子**不是全市場**：OTC_STOCKS 只有 4 筆離線 fallback。
      全市場覆蓋請走 scan_universe，不要再擴充這個池子。）
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

# 這三個常數是「連日漲停」的定義，全站共用（universe.scan_universe 也讀它們），
# 免得兩條路徑對「算不算漲停股」有兩套標準。
LOOKBACK_DAYS = 15   # 往回看幾個交易日
MAX_DAYS_AGO  = 5    # 最後一次漲停必須在幾個交易日內
MIN_STREAK    = 2    # 至少連續幾天才算「連日漲停」


def streak_metrics(pct_vals) -> dict:
    """
    由「日漲跌幅 %」序列算連續漲停指標 —— **唯一實作**。

    抽出來是因為全市場掃描（`universe.scan_universe`）已經逐檔拿著 2 年日線，
    直接呼叫這個函式就能把**全市場**的連日漲停算出來，不必為了候選池再下載一次。
    先前候選池是 `今日漲停 + POPULAR_STOCKS(31) + OTC_STOCKS(4)`，而 OTC_STOCKS
    自己的註解就寫明它只是離線 fallback——結果「近 5 個交易日內連日漲停、
    但今天沒漲停」的股票根本進不了候選池，`MAX_DAYS_AGO` 這個設計形同虛設。

    回傳 {max_streak, trailing_streak, last_days_ago}；從未漲停時 last_days_ago=999。
    """
    max_s = current = 0
    for v in pct_vals:
        if v >= _LIMIT_THRESHOLD:
            current += 1
            max_s = max(max_s, current)
        else:
            current = 0
    last_ago = 999
    for i, v in enumerate(reversed(pct_vals)):
        if v >= _LIMIT_THRESHOLD:
            last_ago = i
            break
    # current 跑完迴圈仍在手上 = 以最新一天結尾的連續天數
    return {"max_streak": max_s, "trailing_streak": current,
            "last_days_ago": last_ago}


def streaks_from_closes(close_series, lookback: int = LOOKBACK_DAYS) -> dict:
    """由收盤價 Series 直接算 —— 給已經持有日線的呼叫端（全市場掃描）用。"""
    tail = close_series.tail(lookback + 1)
    if len(tail) < 2:
        return {"max_streak": 0, "trailing_streak": 0, "last_days_ago": 999}
    return streak_metrics((tail.pct_change().dropna() * 100).tolist())


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


@st.cache_data(ttl=900, show_spinner=False)
def _fetch_tpex_today() -> list:
    """
    今日**上櫃**漲停股。

    為什麼需要：`_fetch_twse_today()` 只涵蓋上市。全市場掃描早就納入上櫃
    （services/universe.get_otc_snapshot），但漲停偵測沒跟上，於是
    「漲停動能」策略看不到任何上櫃股——而連日漲停最常發生在上櫃小型股。

    直接沿用 universe 的櫃買快照（已快取），用「收盤 vs 前一日收盤」算漲幅。
    """
    try:
        from services.universe import get_otc_snapshot, _TPEX_DAY, _HEADERS
        import requests
        snap = get_otc_snapshot()
        if not snap:
            return []
        rows = requests.get(_TPEX_DAY, timeout=40, headers=_HEADERS).json()
    except Exception:
        return []
    if not rows:
        return []

    # 櫃買日收盤 API 一次回多個日期，取最新一天；漲跌價差欄位為 "Change"
    try:
        latest = max(str(r.get("Date", "")) for r in rows)
    except ValueError:
        return []
    out = []
    for d in rows:
        if str(d.get("Date", "")) != latest:
            continue
        code = str(d.get("SecuritiesCompanyCode", "")).strip()
        if not re.match(r"^\d{4}$", code):
            continue
        try:
            close = float(str(d.get("Close", "")).replace(",", ""))
            chg = float(str(d.get("Change", "")).replace(",", ""))
        except (TypeError, ValueError):
            continue
        prev = close - chg
        if prev <= 0:
            continue
        chg_pct = chg / prev * 100
        if chg_pct < _LIMIT_THRESHOLD:
            continue
        vol = snap.get(code, {}).get("volume") or 0
        if vol < _MIN_VOLUME:
            continue
        out.append({
            "stock_id": code,
            "name": snap.get(code, {}).get("name", ""),
            "change_pct": round(chg_pct, 2),
            "volume": vol,
            "exchange": "TPEX",
        })
    return out


# ── 2. Streak computation via yfinance ────────────────────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def _compute_streaks(stock_ids_tuple: tuple, lookback: int = LOOKBACK_DAYS) -> dict:
    """
    Batch-fetch yfinance price history for stock_ids and return streak metrics.

    Args:
        stock_ids_tuple: tuple of 4-digit stock IDs (must be tuple for cache key)
        lookback: trading days to inspect

    Returns:
        {stock_id: {"max_streak": int, "trailing_streak": int, "last_days_ago": int}}
    """
    import yfinance as yf
    from services.stock_data import _is_otc

    stock_ids = list(stock_ids_tuple)
    if not stock_ids:
        return {}

    # ⚠️ 這裡原本用 `sid in OTC_STOCKS`——那是只有 4 筆的**離線 fallback 表**，
    #    上櫃股會被要成 `.TW` 而拿不到資料。上櫃判斷的唯一來源是
    #    `stock_data._is_otc()`（櫃買中心官方清單），與其他模組一致。
    tickers      = [f"{sid}.TWO" if _is_otc(sid) else f"{sid}.TW" for sid in stock_ids]
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

            results[sid] = streak_metrics(
                (series.pct_change().dropna() * 100).tolist())

        return results
    except Exception:
        return {}


# ── 3. Main public function ───────────────────────────────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def get_limit_up_stocks(top_n: int = 30, min_streak: int = MIN_STREAK) -> list:
    """
    Return stocks with consecutive limit-up (連日漲停) momentum.

    Candidates:
      - Today's TWSE limit-up stocks (confirmed by TWSE CSV)
      - POPULAR_STOCKS + OTC_STOCKS (checked via yfinance history)

    Recency filter: last limit-up must be within MAX_DAYS_AGO trading days.

    Today-correction: yfinance data lags the current session. For stocks
    confirmed today by TWSE CSV, we patch trailing_streak and last_days_ago
    to reflect that today IS a limit-up day.

    Sorted by: max_streak DESC → last_days_ago ASC → volume DESC

    Each entry:
        {"stock_id", "name", "change_pct", "volume", "exchange",
         "max_streak", "trailing_streak", "last_days_ago", "is_today_lu"}
    """
    from services.stock_data import POPULAR_STOCKS, OTC_STOCKS, _is_otc

    today_lu    = _fetch_twse_today() + _fetch_tpex_today()
    today_by_id = {s["stock_id"]: s for s in today_lu}
    today_ids   = set(today_by_id)

    candidate_ids = sorted(today_ids | set(POPULAR_STOCKS) | set(OTC_STOCKS))
    streak_info   = _compute_streaks(tuple(candidate_ids), lookback=LOOKBACK_DAYS)

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
        if last_ago > MAX_DAYS_AGO:
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
        # 交易所標籤也要用官方清單判斷（先前同樣只看 4 筆的 fallback 表，
        # 於是上櫃漲停股在卡片上被標成 TWSE）
        exchange = lu_today.get("exchange") or ("TPEX" if _is_otc(sid) else "TWSE")

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
