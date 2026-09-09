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

import requests
import streamlit as st
import yfinance as yf

_BWIBBU = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
_DAY_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
# 櫃買中心（上櫃）——原本完全沒納入，導致「全市場」其實只有一半
# （大立光、緯創、環球晶、緯穎等上櫃權值股都掃不到）
_TPEX_DAY = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
_TPEX_PER = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"
_HEADERS = {"User-Agent": "Mozilla/5.0"}

# 掃描時一律下載到這個流動性下限（＝UI 滑桿的最低檔），
# 更高的門檻只在記憶體過濾，不重抓。
SCAN_FLOOR_TURNOVER = 1e7


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

    for code in out:
        out[code]["market"] = "TWSE"
    return out


@st.cache_data(ttl=10800, show_spinner=False)
def get_otc_snapshot() -> dict:
    """
    上櫃普通股快照，欄位與 get_listed_snapshot 相同（多一個 market='TPEX'）。

    注意：櫃買的日收盤 API 一次回傳多個日期（約 11000 筆 / 900 檔），
    必須只取最新一天，否則同一檔會出現多筆而互相覆蓋成舊價。
    """
    out = {}
    try:
        rows = requests.get(_TPEX_DAY, timeout=40, headers=_HEADERS).json()
    except Exception:
        return {}
    if not rows:
        return {}

    latest = max(str(r.get("Date", "")) for r in rows)
    for d in rows:
        if str(d.get("Date", "")) != latest:
            continue
        code = str(d.get("SecuritiesCompanyCode", "")).strip()
        if len(code) != 4 or not code.isdigit() or code.startswith("0"):
            continue
        shares = _f(d.get("TradingShares")) or 0
        out[code] = {
            "name": str(d.get("CompanyName", "")).strip(),
            "close": _f(d.get("Close")),
            "volume": shares,
            "turnover": _f(d.get("TransactionAmount")) or 0,
            "pe": None, "pb": None, "dy": None, "market": "TPEX",
        }

    try:
        for d in requests.get(_TPEX_PER, timeout=40, headers=_HEADERS).json():
            code = str(d.get("SecuritiesCompanyCode", "")).strip()
            if code in out:
                out[code]["pe"] = _f(d.get("PriceEarningRatio"))
                out[code]["pb"] = _f(d.get("PriceBookRatio"))
                yr = _f(d.get("YieldRatio"))
                out[code]["dy"] = yr / 100 if yr else None
    except Exception:
        pass

    return out


@st.cache_data(ttl=10800, show_spinner=False)
def get_full_market_snapshot(include_otc: bool = True) -> dict:
    """上市 + 上櫃合併快照。代碼不重疊，故可直接合併。"""
    snap = dict(get_listed_snapshot())
    if include_otc:
        snap.update(get_otc_snapshot())
    return snap


@st.cache_data(ttl=3600, show_spinner=False, persist="disk", max_entries=4)
def _download_history_cached(codes_key: tuple, period: str, chunk: int,
                             otc_codes: frozenset):
    """
    真正下載的內層函式（有快取）。

    ⚠️ 這是整個掃描最貴的一步（全市場約 1,974 檔）。原本完全沒有快取，
    導致每次重新掃描都把幾百 MB 的歷史資料重抓一遍。加上 persist="disk" 後，
    連 Streamlit 重啟都還留著，換策略／調流動性門檻都不必重抓。

    參數必須是可雜湊的（tuple / frozenset），否則 st.cache_data 無法當快取鍵。
    """
    return _do_download(list(codes_key), period, chunk, None,
                        lambda c: ".TWO" if c in otc_codes else ".TW")


def download_history_bulk(codes, period="2y", chunk=120, progress_cb=None,
                          suffix_of=None, use_cache=True):
    """
    Batch-download daily history for many codes. Returns {code: DataFrame}.
    yfinance batches each request, so this is ~100x faster than looping.

    suffix_of: code → yfinance suffix. 上市為 '.TW'、上櫃為 '.TWO'；
               沒給就一律當上市（維持舊行為）。
    use_cache: 走 st.cache_data（含硬碟持久化）。有進度回呼時自動略過快取，
               因為進度回呼無法被雜湊，且第二次命中快取時也不需要進度條。
    """
    if use_cache and progress_cb is None:
        otc = frozenset(c for c in codes
                        if suffix_of and suffix_of(c) == ".TWO")
        return _download_history_cached(tuple(sorted(codes)), period, chunk, otc)
    return _do_download(codes, period, chunk, progress_cb, suffix_of)


def _do_download(codes, period, chunk, progress_cb, suffix_of):
    frames = {}
    total = len(codes)
    for i in range(0, total, chunk):
        part = codes[i:i + chunk]
        tickers = [c + (suffix_of(c) if suffix_of else ".TW") for c in part]
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


def scan_universe(min_turnover=1e7, period="2y", progress_cb=None, include_otc=True):
    """
    Score EVERY liquid listed stock on the bulk-available dimensions.

    min_turnover 只決定**下載與評分的下限**（會再與 SCAN_FLOOR_TURNOVER 取較寬者）。
    使用者在 UI 選的流動性門檻**不在這裡套用**——每一列都帶 `turnover`，
    由畫面端過濾，這樣拖滑桿不必重掃，趨勢分的百分位分母也不會跟著變。

    Deliberately neutral placeholders are used for the two things that can't be
    fetched in bulk (news sentiment → 50, target-price upside → None). They are
    identical for every stock, so they don't distort the relative ranking; the
    finalists get the real values in the enrichment pass.

    Returns list of row dicts (preliminary scores), plus the snapshot used.
    """
    from services.technical import (
        calculate_indicators, calculate_technical_score, calculate_horizon_scores,
        dist_from_ma120, overheat_flag,
        analyze_volume_price, calculate_risk_plan,
    )
    from services.scoring import (
        raw_factors as _raw_factors, score_cross_section, save_distribution,
    )
    from services.fundamental import calculate_fundamental_score
    from services.recommendation import (
        generate_recommendation, generate_timeframe_recommendations,
    )
    from services.potential import calculate_potential_score
    from services.margin import get_latest_margin_table, calculate_margin_signal
    from services.market import get_market_regime

    # ⚠️ 一律以「最寬的門檻」下載**並評分**，使用者選的門檻在畫面端過濾。
    #
    # 先前這裡在評分迴圈裡就用 min_turnover 把股票濾掉，於是：
    #   1. **流動性滑桿變成死 UI** —— 掃完之後拖滑桿完全沒有反應（結果來自
    #      session 快取，而快取鍵刻意不含門檻），但畫面上還寫著「調整門檻不會重抓，
    #      只重新排序」。實際上它連重新排序都沒有。
    #   2. **趨勢結構分的百分位分母會跟著滑桿變** —— 分數號稱「贏過全市場 X%」，
    #      其實只贏過「流動性達標的那幾百檔」；滑桿一動，同一檔的分數就變了。
    # 改成一律評分到 SCAN_FLOOR_TURNOVER：百分位分母固定且真的接近全市場，
    # 滑桿則在 app.py 對快取結果做記憶體過濾（實測每檔評分僅約 3ms，
    # 多評幾百檔只多 1~2 秒，遠比重抓幾百 MB 便宜）。
    snap = get_full_market_snapshot(include_otc=include_otc)
    scan_floor = min(min_turnover, SCAN_FLOOR_TURNOVER)
    codes = sorted(c for c, v in snap.items() if (v.get("turnover") or 0) >= scan_floor)
    frames = download_history_bulk(
        codes, period=period, progress_cb=progress_cb,
        suffix_of=lambda c: ".TWO" if snap.get(c, {}).get("market") == "TPEX" else ".TW",
    )

    _, margin_table = get_latest_margin_table()
    regime = get_market_regime()
    rows = []

    for code, raw in frames.items():
        try:
            meta = snap.get(code, {})
            df = calculate_indicators(raw)
            if len(df) < 150:
                continue

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
            r60 = ((close / float(df["Close"].iloc[-61]) - 1) * 100
                   if len(df) > 61 else None)

            # Shortlist ranking must use only signals that ACTUALLY vary in the
            # bulk pass. buzz / upside / prospect are neutral placeholders here,
            # so ranking on the raw 潛力分 would tie everyone — rank on the real
            # varying parts (低基期 / 技術 / 估值) instead, then enrich the top.
            lb = potential.get("low_base", 50)
            prelim_sleeper = 0.60 * lb + 0.25 * tech_score + 0.15 * fund_score
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
                # 顯示與排序都用純技術分，避免同一檔出現兩個「長線分」
                "horizon": {k: {"score": v["score"], "action": "", "icon": "",
                                "color": "#4caf50", "name": k, "span": ""}
                            for k, v in horizon_tech.items()},
                # 純技術週期分：排序與實證對照用（回測驗證的就是它）
                "horizon_tech": {k: v["score"] for k, v in horizon_tech.items()},
                # 平手鍵與過熱示警：與 analysis.compute_scores 共用同一實作
                "above_ma120": dist_from_ma120(df),
                # 連續趨勢分的原始因子；分數要等整批掃完才算得出百分位
                "_factors": _raw_factors(df),
                "r60": r60,
                "overheat": overheat_flag(pe=meta.get("pe"), r60=r60,
                                          news_score=None),
                "turnover": meta.get("turnover"),
                "is_limit_up": False, "max_streak": 0, "last_days_ago": 0,
                "exchange": meta.get("market", "TWSE"), "limit_up_pct": None,
                "preliminary": True,   # news / target price not yet fetched
            })
        except Exception:
            continue

    # ── 連續趨勢結構分：全站排序訊號 ──────────────────────────────────────
    # 必須等整批掃完才算，因為百分位是「跟當天所有股票比」。
    # 順便把分布落地，讓個股分析／我的持股用同一把尺打分（見 services/scoring）。
    facts = [r.get("_factors") or {} for r in rows]
    for r, sc in zip(rows, score_cross_section(facts)):
        r["trend_score"] = sc["score"]
        r["trend_pcts"] = sc["percentiles"]
        r.pop("_factors", None)
    save_distribution(facts)

    return rows, snap
