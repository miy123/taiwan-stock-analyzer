import math



def _normalize_dividend_yield(info: dict):
    """
    yfinance returns `dividendYield` as a PERCENT number (e.g. 3.84 = 3.84%),
    while every other ratio here is a fraction. Normalise to a fraction so all
    downstream scoring/formatting can treat it uniformly.

    Cross-checked against dividendRate / price and trailingAnnualDividendYield
    (which IS a fraction), e.g. 2412: rate 5.2 / price 135.5 = 3.84%.
    """
    dy = info.get("dividendYield")
    if dy is None:
        # Fall back to the fraction-valued field when available
        return info.get("trailingAnnualDividendYield")
    try:
        dy = float(dy)
    except (TypeError, ValueError):
        return None
    if dy <= 0:
        return None
    # >0.5 can only be a percent number (a 50%+ real yield doesn't occur here)
    return dy / 100 if dy > 0.5 else dy


def analyze_fundamentals(info: dict, financials: dict, stock_id: str = None) -> dict:
    """
    ⚠️ ROE／淨利率／營收成長／負債比**優先採用公開資訊觀測站的批次財報**
    （`services/financials.py`），yfinance 只用來補缺。

    為什麼：全市場掃描沒辦法逐檔抓 yfinance 財報（每檔 2.5 秒），所以那條路徑
    只能用批次來源。若個股頁改用 yfinance，同一檔股票的體質分在兩頁就會不同
    ——本專案反覆踩過這個坑。官方來源還有兩個好處：涵蓋 1959/1971 檔（99%）、
    而且是 point-in-time 正確的當期累計數（yfinance 是 TTM，窗口不同，
    台泥甚至連正負號都不一樣）。
    """
    result = {}

    # From yfinance info
    # ⚠️ **不要寫成 `trailingPE or forwardPE`。** 那會讓虧損股默默改用預估本益比：
    #    台泥本業虧損，證交所（正確地）不提供本益比，yfinance 的 trailingPE 是空的，
    #    於是自動掉到 forwardPE 17.7——歷史與預估本益比意義完全不同，卻放在同一個
    #    欄位、用同一組門檻計分與篩選（lowpe 策略就是吃這個欄位）。
    #    虧損就是「沒有本益比」，畫面已經會顯示「—（虧損或無資料）」。
    result["pe_ratio"] = info.get("trailingPE")
    result["forward_pe"] = info.get("forwardPE")   # 另存，供目標價等處參考
    result["pb_ratio"] = info.get("priceToBook")
    result["roe"] = info.get("returnOnEquity")
    result["roa"] = info.get("returnOnAssets")
    result["revenue_growth"] = info.get("revenueGrowth")
    result["earnings_growth"] = info.get("earningsGrowth")
    result["profit_margin"] = info.get("profitMargins")
    result["operating_margin"] = info.get("operatingMargins")
    result["debt_to_equity"] = info.get("debtToEquity")
    result["current_ratio"] = info.get("currentRatio")
    result["dividend_yield"] = _normalize_dividend_yield(info)
    result["eps"] = info.get("trailingEps")
    result["market_cap"] = info.get("marketCap")
    result["52w_high"] = info.get("fiftyTwoWeekHigh")
    result["52w_low"] = info.get("fiftyTwoWeekLow")
    result["beta"] = info.get("beta")

    # 官方批次財報覆蓋掉 yfinance 的對應欄位（全站同一把尺）
    if stock_id:
        try:
            from services.financials import get_bulk_fundamentals
            fin = get_bulk_fundamentals().get(stock_id) or {}
        except Exception:
            fin = {}
        for k in ("roe", "profit_margin", "revenue_growth", "debt_to_equity"):
            if fin.get(k) is not None:
                result[k] = fin[k]
        # 估值也走官方快照（證交所 BWIBBU／櫃買 peratio），與全市場掃描同一把尺。
        # 先前個股頁用 yfinance、掃描用證交所，同一檔的本益比兩邊差最多 17%
        # （國泰金 13.3 vs 15.6），而 lowpe 策略與過熱警示都吃這個欄位。
        try:
            from services.universe import get_full_market_snapshot
            meta = get_full_market_snapshot().get(stock_id) or {}
        except Exception:
            meta = {}
        if meta.get("pe") is not None:
            result["pe_ratio"] = meta["pe"]
        elif meta:
            # 官方有這檔但沒給本益比＝虧損或無獲利資料，不要用 yfinance 的數字頂替
            result["pe_ratio"] = None
        if meta.get("pb") is not None:
            result["pb_ratio"] = meta["pb"]
        if meta.get("dy") is not None:
            result["dividend_yield"] = meta["dy"]
        if fin.get("is_financial"):
            # 金融業的負債權益比與一般產業不可比（存款即負債），不計分
            result["debt_to_equity"] = None
        if fin.get("period"):
            result["fin_period"] = fin["period"]

    # Revenue trend from income statement
    income = financials.get("income_stmt")
    if income is not None and not income.empty:
        rev_row = None
        for label in ["Total Revenue", "Revenue", "Revenues"]:
            if label in income.index:
                rev_row = income.loc[label]
                break
        if rev_row is not None:
            result["revenue_history"] = rev_row.sort_index()

        net_income_row = None
        for label in ["Net Income", "Net Income Common Stockholders"]:
            if label in income.index:
                net_income_row = income.loc[label]
                break
        if net_income_row is not None:
            result["net_income_history"] = net_income_row.sort_index()

    # Quarterly revenue
    q_income = financials.get("quarterly_income")
    if q_income is not None and not q_income.empty:
        for label in ["Total Revenue", "Revenue"]:
            if label in q_income.index:
                result["quarterly_revenue"] = q_income.loc[label].sort_index()
                break

    return result


def calculate_fundamental_score(info: dict, fundamentals: dict) -> tuple[int, list]:
    """
    **體質分＝只看品質，不看估值。** Returns (score 0-100, reasons).

    ## 為什麼把本益比與殖利率拿掉

    品質（Quality）與價值（Value）在因子投資裡是**兩個獨立因子**，分開的理由
    正是它們常常負相關——好公司通常貴。混成一個數字，兩個問題都答不清楚。
    對本專案更有三個具體理由：

    1. **這裡已經量過估值是負的**：`strategy_comparison.json` 的純低本益比
       持有3個月超額 −4.57%、t=−3.96（顯著虧錢）。混進來等於讓體質分
       獎勵一個自己量過會賠錢的因子。實測後果：亞泥品質 39 卻因本益比 9.67
       加到 58，聯發科品質 60 卻因本益比 75 掉到 46——排序整個顛倒。
    2. **估值在畫面上已經出現兩次**：卡片有獨立的本益比徽章，
       `technical.overheat_flag()` 也用本益比 >40 發「利多已反映」警示。
       拿掉不會少任何資訊。
    3. **體質門檻是加掛在動能策略上的**。動能本來就會挑到漲多因此偏貴的股票，
       帶估值的體質分會系統性扣它們分——等於用濾網抵銷主訊號，
       正是本專案量過四次的「多加一層過濾更差」。

    實測影響：含估值 vs 純品質 Spearman ρ=0.87，但**名次中位移動 80 名**、
    前 10% 有 90 檔進出。不是小差別。

    ⚠️ 殖利率也是價格掛勾的（殖利率 = 股利 ÷ 股價），所以它跟本益比一起歸在
    估值那邊，不在體質分內。

    Bands are calibrated to the ACTUAL distribution of Taiwan large/mid caps
    (measured across the scan pool), not to absolute rules of thumb — the market
    median is roughly: P/E 19, ROE 15.5%, 營收成長 23%, 淨利率 14.6%, 殖利率 3.2%,
    D/E 30. Scoring against those medians is what makes the score discriminate:
    with the old thresholds (e.g. "ROE>10 → +6", "營收成長>20 → +15") the *median*
    stock collected nearly every bonus, so 13/31 stocks scored ≥90 and 7 tied at
    exactly 100 — the dimension carried almost no ranking information.

    Raw points are then squashed with tanh so that even a stock that maxes every
    band lands near ~88 rather than piling up at the 100 ceiling, preserving
    ordering among strong companies.
    """
    raw = 0.0
    reasons = []

    # ROE — market p25≈9%, p50≈15.5%, p75≈23%
    roe = fundamentals.get("roe")
    if roe is not None:
        r = roe * 100
        if r < 0:
            raw -= 16; reasons.append(f"ROE {r:.1f}% 為負，股東權益虧損 (-16)")
        elif r > 25:
            raw += 14; reasons.append(f"ROE {r:.1f}% 位居市場前段(>p75)，獲利能力優異 (+14)")
        elif r > 18:
            raw += 8; reasons.append(f"ROE {r:.1f}% 高於市場中位(15.5%) (+8)")
        elif r > 12:
            raw += 3; reasons.append(f"ROE {r:.1f}% 約當市場中位 (+3)")
        elif r < 8:
            raw -= 8; reasons.append(f"ROE {r:.1f}% 低於市場後25%，獲利能力弱 (-8)")

    # Revenue growth — market p25≈10%, p50≈23%, p75≈37%
    rev_growth = fundamentals.get("revenue_growth")
    if rev_growth is not None:
        g = rev_growth * 100
        if g > 40:
            raw += 12; reasons.append(f"營收年增 {g:.1f}%，位居市場前段，高速成長 (+12)")
        elif g > 25:
            raw += 7; reasons.append(f"營收年增 {g:.1f}%，優於市場中位(23%) (+7)")
        elif g > 10:
            raw += 2; reasons.append(f"營收年增 {g:.1f}%，成長中等 (+2)")
        elif g < -15:
            raw -= 14; reasons.append(f"營收年減 {g:.1f}%，明顯衰退 (-14)")
        elif g < 0:
            raw -= 8; reasons.append(f"營收年減 {g:.1f}%，成長轉負 (-8)")
        else:
            raw -= 3; reasons.append(f"營收年增 {g:.1f}%，成長落後市場 (-3)")

    # Profit margin — market p25≈7.6%, p50≈14.6%, p75≈28%
    margin = fundamentals.get("profit_margin")
    if margin is not None:
        m = margin * 100
        if m < 0:
            raw -= 14; reasons.append(f"淨利率 {m:.1f}%，本業虧損 (-14)")
        elif m > 30:
            raw += 10; reasons.append(f"淨利率 {m:.1f}%，位居市場前段 (+10)")
        elif m > 16:
            raw += 5; reasons.append(f"淨利率 {m:.1f}%，優於市場中位 (+5)")
        elif m < 6:
            raw -= 6; reasons.append(f"淨利率 {m:.1f}%，獲利能力偏薄 (-6)")

    # Debt / equity — market p50≈30, p75≈81
    d2e = fundamentals.get("debt_to_equity")
    if d2e is not None:
        if d2e < 20:
            raw += 5; reasons.append(f"負債權益比低 (D/E={d2e:.0f})，財務穩健 (+5)")
        elif d2e > 250:
            raw -= 12; reasons.append(f"負債權益比極高 (D/E={d2e:.0f})，財務風險大 (-12)")
        elif d2e > 120:
            raw -= 6; reasons.append(f"負債權益比偏高 (D/E={d2e:.0f})，槓桿較大 (-6)")

    # Squash so maxing every band lands ~88, not pinned at the 100 ceiling.
    score = int(round(50 + 45 * math.tanh(raw / 45)))
    score = max(0, min(100, score))
    return score, reasons
