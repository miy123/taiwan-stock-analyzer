"""
融資（Margin financing）籌碼面資料與訊號。

資料來源：TWSE 每日「融資融券彙總」(MI_MARGN)。yfinance 不提供台股融資資料，
因此改抓證交所每日全市場信用交易表（單一 HTTP 請求即涵蓋全上市股票，重度快取共用）。

核心指標：
  融資使用率 = 融資今日餘額 / 融資限額（次一營業日限額）
    - 全市場中位數約 3.9%，前 15% > ~15%，前 5% > ~27%。
    - 使用率過高代表散戶槓桿沉重、籌碼凌亂，短線易因融資追繳（斷頭）引發賣壓。
  融資餘額變化：融資增而股價跌＝套牢賣壓累積；融資與股價同步急漲＝散戶追高過熱。
"""

import datetime
import requests
import streamlit as st

_TWSE_MARGIN_URL = (
    "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
    "?date={date}&selectType=ALL&response=json"
)
_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _num(s) -> float:
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, AttributeError):
        return 0.0


@st.cache_data(ttl=21600, show_spinner=False)  # 6h — margin data is once-daily
def _fetch_twse_margin_table(date_str: str) -> dict:
    """
    Fetch one day's whole-market margin table.
    Returns {stock_id: {...}} or {} if that day has no data (holiday/not-yet-published).
    date_str: 'YYYYMMDD'
    """
    try:
        r = requests.get(
            _TWSE_MARGIN_URL.format(date=date_str), timeout=15, headers=_HEADERS
        )
        j = r.json()
    except Exception:
        return {}

    if j.get("stat") != "OK":
        return {}

    # tables[1] = 融資融券彙總 (全部); fields:
    # 0代號 1名稱 2融資買進 3融資賣出 4融資現償 5融資前日餘額 6融資今日餘額
    # 7融資次一營業日限額 8融券買進 9融券賣出 10融券現償 11融券前日餘額 12融券今日餘額 ...
    tables = j.get("tables", [])
    if len(tables) < 2:
        return {}
    rows = tables[1].get("data", [])

    out = {}
    for row in rows:
        if len(row) < 13:
            continue
        sid = str(row[0]).strip()
        balance = _num(row[6])
        prev = _num(row[5])
        limit = _num(row[7])
        out[sid] = {
            "date": j.get("date", date_str),
            "balance": balance,          # 融資今日餘額 (張)
            "prev_balance": prev,        # 融資前日餘額 (張)
            "limit": limit,              # 融資限額 (張)
            "usage_pct": (balance / limit * 100) if limit > 0 else None,
            "change": balance - prev,    # 今日 - 前日 (張)
            "change_pct": ((balance - prev) / prev * 100) if prev > 0 else None,
            "short_balance": _num(row[12]),  # 融券今日餘額 (張)
        }
    return out


def _ref_date(as_of: str) -> datetime.date:
    """Parse an 'YYYYMMDD' as-of string; empty → today."""
    if as_of:
        try:
            return datetime.datetime.strptime(as_of, "%Y%m%d").date()
        except ValueError:
            pass
    return datetime.date.today()


@st.cache_data(ttl=10800, show_spinner=False)  # 3h
def get_latest_margin_table(as_of: str = "") -> tuple:
    """Walk back up to 10 calendar days from `as_of` (default today) to find the
    most recent published table on/before that date.
    Returns (date_str, {stock_id: {...}}) or (None, {})."""
    ref = _ref_date(as_of)
    for back in range(0, 11):
        d = ref - datetime.timedelta(days=back)
        if d.weekday() >= 5:  # skip Sat/Sun
            continue
        table = _fetch_twse_margin_table(d.strftime("%Y%m%d"))
        if table:
            return d.strftime("%Y%m%d"), table
    return None, {}


def get_margin_data(stock_id: str, as_of: str = "") -> dict:
    """Single-day margin snapshot for one stock, on/before `as_of` (default today).
    {} if unavailable (ETF/上櫃/興櫃或當日無資券交易)."""
    _, table = get_latest_margin_table(as_of)
    return dict(table.get(stock_id, {}))


@st.cache_data(ttl=10800, show_spinner=False)
def get_margin_trend(stock_id: str, days: int = 5, as_of: str = "") -> list:
    """Daily 融資餘額 for one stock ending on/before `as_of` (default today),
    oldest→newest. Returns list of {'date', 'balance', 'usage_pct'}. Best-effort.
    (Underlying per-day tables are cached, so long windows are cheap across stocks.)"""
    ref = _ref_date(as_of)
    trend = []
    for back in range(0, days * 2 + 14):
        if len(trend) >= days:
            break
        d = ref - datetime.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        table = _fetch_twse_margin_table(d.strftime("%Y%m%d"))
        rec = table.get(stock_id) if table else None
        if rec:
            trend.append({
                "date": rec["date"],
                "balance": rec["balance"],
                "usage_pct": rec["usage_pct"],
            })
    trend.reverse()  # oldest → newest
    return trend


def summarize_margin_trend(trend: list) -> dict:
    """From a daily 融資餘額 trend (oldest→newest), compute short (~5d) and long
    (~20d, full window) percentage changes plus a direction label."""
    out = {"change_5d": None, "change_long": None, "long_days": 0, "label": "資料不足"}
    if not trend or len(trend) < 2:
        return out
    last = trend[-1]["balance"]
    if len(trend) >= 6 and trend[-6]["balance"]:
        out["change_5d"] = (last - trend[-6]["balance"]) / trend[-6]["balance"] * 100
    first = trend[0]["balance"]
    if first:
        out["change_long"] = (last - first) / first * 100
        out["long_days"] = len(trend) - 1
    # Primary trend uses the longest available window
    primary = out["change_long"] if out["change_long"] is not None else out["change_5d"]
    if primary is None:
        out["label"] = "資料不足"
    elif primary >= 20:
        out["label"] = "大增"
    elif primary >= 8:
        out["label"] = "增加"
    elif primary <= -20:
        out["label"] = "大減"
    elif primary <= -8:
        out["label"] = "減少"
    else:
        out["label"] = "持平"
    out["primary_pct"] = primary
    return out


def calculate_margin_signal(margin_data: dict, price_chg_pct=None, trend=None) -> dict:
    """
    Turn a margin snapshot + trend into a risk penalty + reasons. Three factors:
      (1) 融資使用率水準 — 數量相對股本規模（level）
      (2) 融資餘額增減趨勢 — 獨立計分（增=加碼槓桿=風險；減=去槓桿=安定）
      (3) 趨勢 × 股價 — 套牢／追高／惜售的加強警示

    margin_data: from get_margin_data().
    price_chg_pct: price change % over the trend window (optional).
    trend: daily 融資餘額 list oldest→newest from get_margin_trend() (optional);
           when given, 5日/20日變化取自此，較單日餘額變化穩定。

    Returns dict with 'penalty' (points to subtract), 'level', 'trend_label',
    'change_5d', 'change_long', 'reasons', 'warning', + passthrough for display.
    """
    if not margin_data or margin_data.get("usage_pct") is None:
        return {
            "level": "na",
            "penalty": 0,
            "usage_pct": None,
            "change_pct": None,
            "change_5d": None,
            "change_long": None,
            "trend_label": "資料不足",
            "balance": None,
            "reasons": ["無融資資料（可能為 ETF、上櫃/興櫃或當日無資券交易）"],
            "warning": None,
        }

    usage = margin_data["usage_pct"]
    balance = margin_data.get("balance")

    # Trend: prefer a multi-day summary; fall back to the single-day change_pct.
    tsum = summarize_margin_trend(trend) if trend else {}
    chg_5d = tsum.get("change_5d")
    chg_long = tsum.get("change_long")
    long_days = tsum.get("long_days", 0)
    trend_label = tsum.get("label", "資料不足")
    # The change % used for scoring / price-combo (longest window available)
    chg = tsum.get("primary_pct")
    if chg is None:
        chg = margin_data.get("change_pct")
        if chg is not None:
            trend_label = (
                "大增" if chg >= 20 else "增加" if chg >= 8 else
                "大減" if chg <= -20 else "減少" if chg <= -8 else "持平"
            )

    penalty = 0
    reasons = []
    warning = None

    # ── (1) 融資使用率水準（數量相對規模）──────────────────────────────────────
    if usage >= 30:
        penalty += 14
        level = "high"
        reasons.append(f"融資使用率 {usage:.1f}%，屬全市場前段（>30%），散戶槓桿沉重 (-14)")
    elif usage >= 20:
        penalty += 9
        level = "elevated"
        reasons.append(f"融資使用率 {usage:.1f}%，偏高（>20%，約前 5%），籌碼偏亂 (-9)")
    elif usage >= 12:
        penalty += 5
        level = "elevated"
        reasons.append(f"融資使用率 {usage:.1f}%，略高（>12%，約前 15%），留意追繳風險 (-5)")
    else:
        level = "normal"
        reasons.append(f"融資使用率 {usage:.1f}%，籌碼面正常（市場中位數約 4%）")

    # ── (2) 融資餘額增減趨勢（獨立計分，用與顯示一致的 trend_label 分級）─────────
    # 融資增＝散戶槓桿加碼（籌碼變重、風險升）；融資減＝去槓桿（籌碼安定）。
    win_txt = f"近{long_days}日" if long_days else "近期"
    _trend_pen = {"大增": 8, "增加": 5, "持平": 0, "減少": -4, "大減": -6}
    if chg is not None and trend_label in _trend_pen:
        tp = _trend_pen[trend_label]
        penalty += tp
        if tp > 0:
            reasons.append(f"融資餘額{win_txt}{trend_label} {chg:+.0f}%，散戶加碼槓桿、籌碼變重 (-{tp})")
        elif tp < 0:
            reasons.append(f"融資餘額{win_txt}{trend_label} {chg:+.0f}%，散戶去槓桿、籌碼趨安定 (+{-tp})")
        else:
            reasons.append(f"融資餘額{win_txt}變化 {chg:+.0f}%，大致持平")

    # ── (3) 融資趨勢 × 股價走勢（背離／過熱的加強警示）───────────────────────────
    if chg is not None:
        if chg >= 8 and price_chg_pct is not None and price_chg_pct < -2:
            penalty += 5
            warning = (
                f"融資餘額{win_txt}增加 {chg:+.0f}% 但股價下跌 {price_chg_pct:.1f}%，"
                "散戶越跌越攤平、套牢賣壓累積，籌碼鬆動風險升高"
            )
            reasons.append("融資增、股價跌：套牢盤堆積 (-5)")
        elif chg >= 12 and price_chg_pct is not None and price_chg_pct > 15:
            penalty += 4
            warning = (
                f"融資餘額{win_txt}急增 {chg:+.0f}% 且股價同步大漲 {price_chg_pct:+.1f}%，"
                "散戶追高過熱，慎防漲多拉回引發融資賣壓"
            )
            reasons.append("融資與股價同步急漲：追高過熱 (-4)")
        elif chg <= -8 and price_chg_pct is not None and price_chg_pct > 2:
            penalty -= 2
            reasons.append("融資減、股價漲：籌碼由散戶轉強手，惜售偏多 (+2)")

    penalty = max(-8, min(penalty, 24))

    return {
        "level": level,
        "penalty": penalty,
        "usage_pct": usage,
        "change_pct": chg,
        "change_5d": chg_5d,
        "change_long": chg_long,
        "long_days": long_days,
        "trend_label": trend_label,
        "balance": balance,
        "short_balance": margin_data.get("short_balance"),
        "date": margin_data.get("date"),
        "reasons": reasons,
        "warning": warning,
    }
