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


def attach_peer_metrics(fundamentals: dict, stock_id) -> dict:
    """
    把「相對同業」的欄位補進 fundamentals —— **兩條路徑共用這一個實作**。

    為什麼要獨立成函式：個股頁走 `analyze_fundamentals()`，全市場掃描
    （`universe.scan_universe`）為了速度自己組 fundamentals dict，兩邊各算一次
    就會有兩把尺——本專案在本益比與體質分上已經踩過兩次。

    目前只有毛利率需要相對同業（它產業差異極大，見
    `sector.industry_gross_margin`）。ROE／淨利率／營收成長的產業差異沒有
    大到必須相對化，維持全市場級距。
    """
    from services.sector import peer_gross_margin
    p = peer_gross_margin(stock_id, fundamentals.get("gross_margin"))
    if p:
        fundamentals["gross_margin_peer"] = p
        if p.get("rel_pp") is not None:
            fundamentals["gross_margin_rel_pp"] = p["rel_pp"]
    return fundamentals


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
        for k in ("roe", "profit_margin", "revenue_growth", "debt_to_equity",
                  "gross_margin", "bvps"):
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
        # 「這一檔在官方批次財報裡有沒有資料」—— **兩條路徑的唯一判準**。
        # `universe.scan_universe` 用的是同一個表達式（`bool(_fin)`）。
        # 先前 `app._analyze_one_stock` 直接寫死 True，於是官方查無的那幾檔
        # 在粗掃時標「無財報資料」、被深度分析之後卻變成一個看似正常的分數
        # （分數其實是 yfinance 補的 TTM，跟全市場不是同一把尺），
        # 體質門檻也會因此對同一檔給出前後不一的結論。
        result["has_financials"] = bool(fin)
        attach_peer_metrics(result, stock_id)

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

    # 毛利率 —— **相對同業**，不是絕對水準。
    #
    # 為什麼要相對：毛利率產業差異極大（產業中位數 生技醫療 41% vs 電子通路 8.3%，
    # 差 33 個百分點；台積電 67% vs 鴻海 6.2%）。用全市場級距等於系統性地
    # 獎勵半導體/生技、處罰通路組裝——那不是體質差，是商業模式不同，
    # 跟金融業不計負債權益比是同一個道理。
    #
    # 為什麼還要它（已經有淨利率了）：毛利率在損益表**上半部**，不會被業外
    # 一次性損益汙染；而且這裡量的是「在自己的產業裡贏不贏同業」，
    # 與淨利率量的「絕對賺錢能力」是不同的訊號。點數刻意給得比 ROE 小
    # （±10 vs ±16），因為兩者仍有部分重疊。
    #
    # 級距取自全市場實際分位（1903 檔：p10=−17.2、p25=−8.7、p50=0.0、
    # p75=+10.9、p90=+23.1 百分點）。
    gm_rel = fundamentals.get("gross_margin_rel_pp")
    # 極端值防護：有公司毛利率是 −8860 百分點（營收趨近於零），
    # 那是資料退化不是體質差，不要讓它主宰分數。
    if gm_rel is not None and -100 <= gm_rel <= 100:
        if gm_rel >= 23:
            raw += 10; reasons.append(f"毛利率高出同業 {gm_rel:.0f} 個百分點，位居產業前段 (+10)")
        elif gm_rel >= 11:
            raw += 6; reasons.append(f"毛利率高出同業 {gm_rel:.0f} 個百分點 (+6)")
        elif gm_rel >= 3:
            raw += 2; reasons.append(f"毛利率略高於同業 {gm_rel:.0f} 個百分點 (+2)")
        elif gm_rel <= -17:
            raw -= 10; reasons.append(f"毛利率低於同業 {-gm_rel:.0f} 個百分點，產業後段 (-10)")
        elif gm_rel <= -9:
            raw -= 5; reasons.append(f"毛利率低於同業 {-gm_rel:.0f} 個百分點 (-5)")

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
