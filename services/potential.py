"""
潛力潛伏股評分：找出「有成長、有上漲空間，但股價還沒漲起來」的個股。

與主推薦相反 —— 主推薦獎勵「已經在漲」的技術強勢股；本評分反而要求股價「還在低基期、
還沒噴出」，把已經大漲、過熱、逼近高點的股票濾掉。三個構面：

  前瞻 (prospect) : 營收/獲利成長、預估獲利改善（forward vs trailing PE）
  潛力 (upside)   : 加權目標價相對現價的上漲空間
  低基期 (low_base): 股價位階低、未過熱、近期尚未大漲（本評分的關鍵閘門）

總分 = 0.30 前瞻 + 0.30 潛力 + 0.40 低基期（見 WEIGHTS）
「低基期」權重最高且含大幅負分，確保已大漲/逼近高點/超買的股票排不上來。

## ⚠️ 「話題」(buzz) 已移出計分（2026-09-16）

它原本佔 26%，內容是**消息面分 + 催化劑計數**。拿掉的理由：

1. **程式並沒有真的在分析話題**。`news._detect_category()` 是對標題做
   關鍵字分類，不是理解內容；把它當成一個佔四分之一權重的構面，
   等於給一個粗略的字串比對過大的發言權。
2. **新聞評分有反身性**（股價漲越兇 → 正面報導越多 → 分數越高），
   專案已經因此把它在**排序主訊號裡的權重設為 0%**。
   潛力分卻還給它 26%，兩邊標準不一致。
3. **全市場掃描根本算不出它**：粗掃不抓新聞，buzz 對全市場**固定是 50**
   （實測 881 檔相異值只有 [50]），完全不影響彼此排名，卻佔著 26% 的權重。

比照 9/15 把估值移出體質分的作法：**分數裡拿掉，畫面上仍然顯示**
（`buzz` / `buzz_reasons` / `pos_catalysts` 照常回傳），催化劑本身仍是有用的
背景資訊，只是不再參與排序。畫面已標明它不計分。

校準：粗掃 881 檔新舊排序 Spearman ρ=0.998、名次中位僅移動 4 名、前 30 名換 1 檔
——因為 buzz 在粗掃路徑本來就是常數，差別會出現在**深度分析過**的股票上。
中性點不變（三項權重和為 1，各項 50 分仍得 50）。

## ⚠️ 入選旗標 `qualifies` 已移除（2026-09-16）

它原本回傳一個布林值，畫面上是「🌱 潛伏」徽章與「✅ 符合潛伏股條件」。移除的理由
見 `calculate_potential_score()` 裡的註解：它是與各策略 filters **平行的第二套
結論**，門檻沒有回測支持，而且拿掉 buzz 前固定 0 檔、拿掉後會在 60% 的股票上亮。

**「🌱 逆勢潛伏」策略不受影響** —— 它用自己的 filters（`low_base ≥45` 且
`total ≥45`），實測前後都是 481 檔。要判斷某檔有沒有入選，一律看策略的
「在各選股策略中的入選情形」，那是唯一出口。
"""

# 構面權重 —— **唯一定義**。畫面說明請引用它，不要另外寫一組數字。
WEIGHTS = {"prospect": 0.30, "upside": 0.30, "low_base": 0.40}


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
    Returns a dict with sub-scores + a 'total', position/RSI/return diagnostics,
    per-dimension reasons, and a one-line 'summary'.
    （刻意**不回傳入選旗標**——判定一律交給策略自己的 filters，見下方說明。）
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
    # ⚠️ buzz 不進總分（見模組說明）。三個構面權重和為 1，所以「各項都中性 50」
    #    仍然得 50 分，與拿掉前的中性點一致。
    total = (WEIGHTS["prospect"] * prospect
             + WEIGHTS["upside"] * upside_score
             + WEIGHTS["low_base"] * low_base)
    total = int(round(total))

    # 一句話定位 —— **只描述位階，不做入選／未入選的判定。**
    #
    # 先前這裡有一個 `qualifies` 旗標（🌱 潛伏徽章），問題有三：
    #   1. 它是與各策略 filters **平行的第二套結論**，而本專案的規矩是
    #      「篩選條件即說明」——判定只能有一個出口，就是策略自己的 filters。
    #   2. 它的門檻從來沒有回測過（evidence.py 甚至量到低基期類超額為負）。
    #   3. 拿掉 buzz 之後它會在全市場 60% 的股票上亮，等於沒有指示性；
    #      而拿掉前是固定 0 檔。兩邊都是錯的。
    # 「🌱 逆勢潛伏」策略不受影響——它用自己的 filters（low_base / total）。
    bits = [f"52 週區間 {pos * 100:.0f}%", f"RSI {rsi:.0f}"]
    if r60 is not None:
        bits.append(f"近 60 日 {r60:+.0f}%")
    if low_base >= 70:
        tail = "位階偏低、尚未起漲"
    elif low_base >= 50:
        tail = "位階中性"
    else:
        tail = "位階偏高或漲幅已大，追高風險較高"
    summary = "、".join(bits) + f" —— {tail}。"

    return {
        "total": total,
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
