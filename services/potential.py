"""
潛力潛伏股評分：找出「有話題、有前瞻、有潛力，但股價還沒漲起來」的個股。

與主推薦相反 —— 主推薦獎勵「已經在漲」的技術強勢股；本評分反而要求股價「還在低基期、
還沒噴出」，把已經大漲、過熱、逼近高點的股票濾掉。四個構面：

  話題 (buzz)     : 正面新聞情緒 + 催化劑（技術突破 / 大單 / 政策題材…）
  前瞻 (prospect) : 營收/獲利成長、預估獲利改善（forward vs trailing PE）
  潛力 (upside)   : 加權目標價相對現價的上漲空間
  低基期 (low_base): 股價位階低、未過熱、近期尚未大漲（本評分的關鍵閘門）

總分 = 0.26 話題 + 0.22 前瞻 + 0.22 潛力 + 0.30 低基期
「低基期」權重最高且含大幅負分，確保已大漲/逼近高點/超買的股票排不上來。
"""


def _pct_return(close, n):
    if len(close) <= n:
        return None
    past = close.iloc[-n - 1]
    if not past:
        return None
    return (close.iloc[-1] / past - 1) * 100


def _upside_score(upside_pct):
    """Target-price upside % → 0-100 (mirror of recommendation._target_price_score)."""
    if upside_pct is None:
        return 50
    if upside_pct > 25:
        return 90
    if upside_pct > 12:
        return 75
    if upside_pct > 3:
        return 60
    if upside_pct > -3:
        return 50
    if upside_pct > -12:
        return 38
    if upside_pct > -25:
        return 25
    return 12


def calculate_potential_score(df, info, fundamentals, news_score, catalysts, target_upside):
    """
    Returns a dict with sub-scores + a 'total', a 'qualifies' flag, position/RSI/return
    diagnostics, per-dimension reasons, and a one-line 'summary'.
    """
    close = df["Close"]
    current = float(close.iloc[-1])

    # ── Price position / momentum diagnostics ─────────────────────────────────
    win = min(252, len(df))
    high52 = float(df["High"].tail(win).max())
    low52 = float(df["Low"].tail(win).min())
    pos = (current - low52) / (high52 - low52) if high52 > low52 else 0.5  # 0=low, 1=high
    rsi = float(df["RSI"].iloc[-1]) if "RSI" in df.columns else 50.0
    r20 = _pct_return(close, 20)
    r60 = _pct_return(close, 60)
    ma60 = float(df["MA60"].iloc[-1]) if "MA60" in df.columns and df["MA60"].iloc[-1] == df["MA60"].iloc[-1] else None
    dist60 = (current / ma60 - 1) * 100 if ma60 else None

    pos_cat = len(catalysts.get("positive", [])) if catalysts else 0
    neg_cat = len(catalysts.get("negative", [])) if catalysts else 0

    # ── 話題 (buzz) ────────────────────────────────────────────────────────────
    buzz = min(100, news_score + 8 * min(pos_cat, 5) - 6 * min(neg_cat, 3))
    buzz = max(0, buzz)
    buzz_reasons = []
    if pos_cat:
        buzz_reasons.append(f"{pos_cat} 則利多催化劑（題材/訂單/技術突破類）")
    buzz_reasons.append(f"消息面情緒分 {news_score}")
    if neg_cat:
        buzz_reasons.append(f"{neg_cat} 則利空需留意")

    # ── 前瞻 (prospect) ────────────────────────────────────────────────────────
    prospect = 50
    prospect_reasons = []
    rev_g = fundamentals.get("revenue_growth")
    if rev_g is not None:
        rg = rev_g * 100
        if rg > 20:
            prospect += 18; prospect_reasons.append(f"營收年增 {rg:.0f}%，高速成長")
        elif rg > 10:
            prospect += 10; prospect_reasons.append(f"營收年增 {rg:.0f}%，穩健成長")
        elif rg > 0:
            prospect += 4; prospect_reasons.append(f"營收年增 {rg:.0f}%")
        elif rg < 0:
            prospect -= 10; prospect_reasons.append(f"營收年減 {rg:.0f}%，成長轉弱")
    earn_g = fundamentals.get("earnings_growth")
    if earn_g is not None:
        eg = earn_g * 100
        if eg > 20:
            prospect += 12; prospect_reasons.append(f"獲利年增 {eg:.0f}%")
        elif eg > 0:
            prospect += 4
        elif eg < 0:
            prospect -= 8; prospect_reasons.append(f"獲利年減 {eg:.0f}%")
    fpe = info.get("forwardPE")
    tpe = info.get("trailingPE")
    if fpe and tpe and fpe > 0 and tpe > 0 and fpe < tpe:
        prospect += 8; prospect_reasons.append("預估本益比低於目前，市場預期獲利成長")
    prospect = max(0, min(100, prospect))

    # ── 潛力 (upside vs target price) ─────────────────────────────────────────
    upside_score = _upside_score(target_upside)
    upside_reasons = []
    if target_upside is not None:
        upside_reasons.append(f"加權目標價上漲空間 {target_upside:+.0f}%")

    # ── 低基期 (not-yet-risen gate) ───────────────────────────────────────────
    low_base = 50
    lb_reasons = []
    # 52-week position (main driver — near lows = more room)
    if pos < 0.30:
        low_base += 25; lb_reasons.append(f"股價位於 52 週區間低檔（{pos*100:.0f}%），基期低、空間大")
    elif pos < 0.50:
        low_base += 12; lb_reasons.append(f"股價位於區間中低檔（{pos*100:.0f}%）")
    elif pos > 0.85:
        low_base -= 30; lb_reasons.append(f"股價逼近 52 週高點（{pos*100:.0f}%），已大漲、追高風險高")
    elif pos > 0.70:
        low_base -= 15; lb_reasons.append(f"股價位於區間高檔（{pos*100:.0f}%）")
    # RSI (not overbought)
    if rsi < 45:
        low_base += 12; lb_reasons.append(f"RSI {rsi:.0f} 偏低、未過熱")
    elif rsi < 55:
        low_base += 5
    elif rsi > 70:
        low_base -= 20; lb_reasons.append(f"RSI {rsi:.0f} 超買，已過熱")
    elif rsi > 62:
        low_base -= 10; lb_reasons.append(f"RSI {rsi:.0f} 偏高")
    # 60-day return (hasn't rallied hard yet)
    if r60 is not None:
        if r60 < 0:
            low_base += 12; lb_reasons.append(f"近 60 日 {r60:.0f}%，仍在打底、尚未起漲")
        elif r60 < 12:
            low_base += 6; lb_reasons.append(f"近 60 日 {r60:+.0f}%，漲幅溫和")
        elif r60 > 40:
            low_base -= 25; lb_reasons.append(f"近 60 日已大漲 {r60:+.0f}%，恐已反映題材")
        elif r60 > 25:
            low_base -= 12; lb_reasons.append(f"近 60 日 {r60:+.0f}%，漲幅已不小")
    # Distance above MA60 (basing vs extended)
    if dist60 is not None:
        if abs(dist60) <= 6:
            low_base += 8; lb_reasons.append("股價貼近季線，仍在整理打底")
        elif dist60 > 25:
            low_base -= 15; lb_reasons.append(f"股價高出季線 {dist60:.0f}%，乖離過大")
    low_base = max(0, min(100, low_base))

    # ── Total + qualification ─────────────────────────────────────────────────
    total = 0.26 * buzz + 0.22 * prospect + 0.22 * upside_score + 0.30 * low_base
    total = int(round(total))

    qualifies = (
        buzz >= 52
        and low_base >= 55
        and (prospect >= 52 or upside_score >= 60)
        and pos < 0.85
        and rsi < 72
    )

    # One-line summary
    if qualifies:
        summary = (
            f"具題材與成長性，但股價仍在 52 週區間 {pos*100:.0f}% 低檔、"
            f"RSI {rsi:.0f} 未過熱，近 60 日{'下跌' if (r60 or 0) < 0 else '僅漲'} "
            f"{(r60 or 0):+.0f}%，屬尚未起漲的潛伏股。"
        )
    else:
        reasons_no = []
        if buzz < 52:
            reasons_no.append("題材不足")
        if low_base < 55:
            reasons_no.append("股價已漲/位階偏高")
        if pos >= 0.85 or rsi >= 72:
            reasons_no.append("已逼近高點或超買")
        if prospect < 52 and upside_score < 60:
            reasons_no.append("成長性與上漲空間皆不明顯")
        summary = "未入選：" + "、".join(reasons_no or ["綜合條件不足"])

    return {
        "total": total,
        "qualifies": qualifies,
        "buzz": int(buzz),
        "prospect": int(prospect),
        "upside_score": int(upside_score),
        "low_base": int(low_base),
        "position_pct": pos * 100,
        "rsi": rsi,
        "r20": r20,
        "r60": r60,
        "pos_catalysts": pos_cat,
        "buzz_reasons": buzz_reasons,
        "prospect_reasons": prospect_reasons,
        "upside_reasons": upside_reasons,
        "low_base_reasons": lb_reasons,
        "summary": summary,
    }
