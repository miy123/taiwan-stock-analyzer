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


def analyze_fundamentals(info: dict, financials: dict) -> dict:
    result = {}

    # From yfinance info
    result["pe_ratio"] = info.get("trailingPE") or info.get("forwardPE")
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
    Returns (score 0-100, list of scoring reasons).

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

    # P/E — market p25≈15, p50≈19, p75≈36
    pe = fundamentals.get("pe_ratio")
    if pe is not None and pe > 0:
        if pe < 12:
            raw += 12; reasons.append(f"本益比 {pe:.1f} 顯著低於市場中位(19)，估值便宜 (+12)")
        elif pe < 19:
            raw += 6; reasons.append(f"本益比 {pe:.1f} 低於市場中位，估值合理 (+6)")
        elif pe < 30:
            raw += 0; reasons.append(f"本益比 {pe:.1f} 約在市場中上水準 (0)")
        elif pe < 50:
            raw -= 7; reasons.append(f"本益比 {pe:.1f} 偏高（市場前25%），估值偏貴 (-7)")
        else:
            raw -= 14; reasons.append(f"本益比 {pe:.1f} 極高，估值風險大 (-14)")

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

    # Dividend yield — fraction (normalised); market p50≈3.2%
    div_yield = fundamentals.get("dividend_yield")
    if div_yield is not None and div_yield > 0:
        dy = div_yield * 100
        if dy > 5:
            raw += 7; reasons.append(f"殖利率 {dy:.2f}%，配息豐厚 (+7)")
        elif dy > 3.5:
            raw += 4; reasons.append(f"殖利率 {dy:.2f}%，優於市場中位(3.2%) (+4)")
        elif dy > 2:
            raw += 1; reasons.append(f"殖利率 {dy:.2f}% (+1)")

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
