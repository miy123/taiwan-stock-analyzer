"""
Recommendation engine.

Weights (when target price available):
  Technical   35%  — RSI, MACD, KD, MA, Bollinger, volume
  Fundamental 35%  — P/E, ROE, revenue growth, margin, dividend
  News        15%  — category-weighted sentiment
  Target Price 15% — upside/downside vs weighted target price

When target price is unavailable the remaining 15% is split 40/40/20
(same as before) so the score degrades gracefully.

Why target price matters for the recommendation:
  A stock can have great technical signals (RSI oversold, MACD cross) while
  still being fundamentally overvalued according to analyst consensus.
  Including target-price upside prevents "Strong Buy" from appearing on a
  stock that every analyst rates as overvalued.
"""


def _target_price_score(upside_pct: float) -> tuple:
    """Convert target-price upside % into a 0-100 score and a reason string."""
    if upside_pct > 25:
        return 88, f"目標價上漲空間 {upside_pct:.1f}%，估值明顯低估 (+)"
    elif upside_pct > 12:
        return 75, f"目標價上漲空間 {upside_pct:.1f}%，仍有上漲空間 (+)"
    elif upside_pct > 3:
        return 60, f"目標價上漲空間 {upside_pct:.1f}%，小幅上漲空間 (=)"
    elif upside_pct > -3:
        return 50, f"目標價約與現價持平（{upside_pct:.1f}%），估值合理 (=)"
    elif upside_pct > -12:
        return 38, f"目標價下跌空間 {upside_pct:.1f}%，估值略偏高，注意 (-)"
    elif upside_pct > -25:
        return 25, f"目標價下跌空間 {upside_pct:.1f}%，估值明顯偏高 (-)"
    else:
        return 10, f"目標價下跌空間 {upside_pct:.1f}%，大幅高估，風險偏高 (-)"


def generate_recommendation(
    tech_score: int,
    fund_score: int,
    news_score: int,
    tech_reasons: list,
    fund_reasons: list,
    news_reasons: list,
    target_upside_pct=None,   # float | None — from calculate_target_price
    margin_signal=None,       # dict | None — from calculate_margin_signal
    volume_signal=None,       # dict | None — from analyze_volume_price
    market_regime=None,       # dict | None — from get_market_regime
) -> dict:

    # ── Target price component ────────────────────────────────────────────────
    if target_upside_pct is not None:
        tp_score, tp_reason = _target_price_score(target_upside_pct)
        # tech 35% + fund 35% + news 15% + target 15%
        total = (
            tech_score  * 0.35
            + fund_score  * 0.35
            + news_score  * 0.15
            + tp_score    * 0.15
        )
        weight_note = "技術 35% + 基本面 35% + 消息 15% + 目標價 15%"
    else:
        tp_score, tp_reason = None, "目標價資料不足，未納入評分（改以 tech40/fund40/news20）"
        total = tech_score * 0.40 + fund_score * 0.40 + news_score * 0.20
        weight_note = "技術 40% + 基本面 40% + 消息 20%（無目標價資料）"

    total = int(total)

    # ── Margin (融資) risk overlay ────────────────────────────────────────────
    # High retail leverage is a chip-structure risk, not a valuation dimension,
    # so it's applied as a penalty on top of the weighted score rather than as
    # a fifth weighted component.
    margin_penalty = 0
    margin_reason = None
    if margin_signal:
        margin_penalty = margin_signal.get("penalty", 0) or 0
        if margin_penalty > 0:
            total -= margin_penalty
            weight_note += f"　（融資籌碼風險 -{margin_penalty}）"
            margin_reason = "；".join(margin_signal.get("reasons", [])) or "融資使用率偏高"

    # ── Volume (量價) overlay ─────────────────────────────────────────────────
    # Volume confirms or contradicts the price move; applied as a small nudge on
    # top of the weighted score (the technical component already counts basic
    # volume, this adds 量縮價漲 / 爆量 nuance).
    volume_adj = 0
    if volume_signal:
        volume_adj = volume_signal.get("score_adj", 0) or 0
        if volume_adj:
            total += volume_adj
            sign = f"+{volume_adj}" if volume_adj > 0 else str(volume_adj)
            weight_note += f"　（量價 {sign}）"

    total = max(0, min(100, int(total)))

    # ── Market-regime-adjusted thresholds ─────────────────────────────────────
    # Taiwan single stocks are highly correlated with the TAIEX, so the same score
    # means less in a downtrend. Raise the bar in a bear market, relax slightly in
    # a bull market, instead of using fixed 68/58/48/38 cutoffs in every regime.
    adj = (market_regime or {}).get("threshold_adj", 0) or 0
    t_strong, t_buy, t_hold, t_reduce = 68 + adj, 58 + adj, 48 + adj, 38 + adj
    regime_note = ""
    if adj and market_regime:
        direction = "提高" if adj > 0 else "放寬"
        regime_note = (
            f"大盤為「{market_regime.get('label')}」，買進門檻{direction} {abs(adj)} 分"
            f"（買進線 {t_buy}）"
        )

    # ── Action thresholds ─────────────────────────────────────────────────────
    if total >= t_strong:
        action      = "強力買進"
        action_en   = "STRONG BUY"
        color       = "#00c853"
        bg_color    = "#e8f5e9"
        icon        = "🚀"
        summary     = "四維度指標（技術、基本面、消息、目標價）均顯示強烈買進訊號，建議積極布局。"
    elif total >= t_buy:
        action      = "偏多買進"
        action_en   = "BUY"
        color       = "#4caf50"
        bg_color    = "#f1f8e9"
        icon        = "📈"
        summary     = "多項指標偏多，整體趨勢向上，可考慮分批買進。"
    elif total >= t_hold:
        action      = "持有觀望"
        action_en   = "HOLD"
        color       = "#ff9800"
        bg_color    = "#fff8e1"
        icon        = "⚖️"
        summary     = "訊號偏中性，建議持有現有部位，等待更明確方向。"
    elif total >= t_reduce:
        action      = "偏空減碼"
        action_en   = "REDUCE"
        color       = "#f44336"
        bg_color    = "#fce4ec"
        icon        = "📉"
        summary     = "多項指標轉弱，建議降低持股比重，保守操作。"
    else:
        action      = "建議出場"
        action_en   = "SELL"
        color       = "#b71c1c"
        bg_color    = "#ffebee"
        icon        = "⛔"
        summary     = "多維度均出現警訊，建議出場避險。"

    # ── Risk warnings ─────────────────────────────────────────────────────────
    risk_warnings = []
    if tech_score < 40:
        risk_warnings.append("技術指標顯示下跌趨勢，注意停損點設定")
    if fund_score < 40:
        risk_warnings.append("基本面數據偏弱，獲利能力需持續觀察")
    if news_score < 40:
        risk_warnings.append("近期負面消息較多，留意市場情緒變化")
    if tp_score is not None and tp_score < 38:
        risk_warnings.append(
            f"目標價顯示下跌空間 {abs(target_upside_pct):.1f}%，"
            "現價可能已高於合理估值，追高需謹慎"
        )
    if margin_signal and margin_signal.get("warning"):
        risk_warnings.append(margin_signal["warning"])
    elif margin_signal and margin_signal.get("level") == "high":
        risk_warnings.append(
            f"融資使用率 {margin_signal['usage_pct']:.1f}% 偏高，"
            "散戶槓桿沉重，短線留意融資追繳（斷頭）引發的賣壓"
        )

    # Combined 融資 × 量價 warning — the two chip-structure signals reinforce
    combo = _volume_margin_combo(volume_signal, margin_signal)
    if combo:
        risk_warnings.append(combo)

    if volume_signal and volume_signal.get("relationship") == "量增價跌":
        risk_warnings.append(
            "量增價跌：成交量放大但股價下跌，通常代表主力／大戶趁勢出貨，賣壓沉重"
        )

    if market_regime and market_regime.get("regime") in ("bear", "mild_bear"):
        risk_warnings.append(
            f"大盤處於「{market_regime['label']}」：台股個股與大盤連動高，"
            "系統性風險升高，即使個股條件佳也建議降低部位、嚴設停損"
        )

    if not risk_warnings:
        risk_warnings.append("目前無明顯重大風險警訊，仍需注意市場整體走勢")

    return {
        "total_score": total,
        "action": action,
        "action_en": action_en,
        "color": color,
        "bg_color": bg_color,
        "icon": icon,
        "summary": summary,
        "weight_note": weight_note,
        "tech_score": tech_score,
        "fund_score": fund_score,
        "news_score": news_score,
        "tp_score": tp_score,
        "tp_reason": tp_reason,
        "target_upside_pct": target_upside_pct,
        "margin_penalty": margin_penalty,
        "margin_reason": margin_reason,
        "volume_adj": volume_adj,
        "regime_label": (market_regime or {}).get("label"),
        "regime_adj": adj,
        "regime_note": regime_note,
        "buy_threshold": t_buy,
        "volume_relationship": (volume_signal or {}).get("relationship"),
        "volume_explain": (volume_signal or {}).get("explain"),
        "volume_reasons": (volume_signal or {}).get("reasons", []),
        "tech_reasons": tech_reasons,
        "fund_reasons": fund_reasons,
        "news_reasons": news_reasons,
        "risk_warnings": risk_warnings,
    }


def _volume_margin_combo(volume_signal, margin_signal):
    """Return a combined 融資×量價 warning string, or None. These two chip-structure
    signals together tell a sharper story than either alone."""
    if not volume_signal or not margin_signal:
        return None
    rel = volume_signal.get("relationship")
    m_chg = margin_signal.get("change_pct")
    m_level = margin_signal.get("level")
    if m_chg is None:
        return None
    if m_chg >= 5 and rel == "量增價漲":
        return (
            "融資、成交量與股價同步放大——散戶借錢追高、量能過熱，"
            "一旦漲多拉回易觸發融資賣壓，追高務必控制部位"
        )
    if m_chg >= 5 and rel == "量增價跌":
        return (
            "融資增加卻量增價跌——散戶加碼攤平卻遇沉重賣壓，套牢籌碼堆積，"
            "為明顯的偏空籌碼結構"
        )
    if m_chg <= -5 and rel == "量縮價跌" and m_level in ("normal", "low", "elevated"):
        return (
            "融資退場且殺盤量縮——槓桿籌碼清洗、賣壓趨於枯竭，"
            "可留意是否出現落底止穩訊號"
        )
    return None


# ─── Timeframe-specific recommendations ───────────────────────────────────────

def _action_for(score: int) -> dict:
    """Map a 0-100 score to an action label/color/icon (shared thresholds)."""
    if score >= 68:
        return {"action": "強力買進", "en": "STRONG BUY", "color": "#00c853", "icon": "🚀"}
    if score >= 58:
        return {"action": "偏多買進", "en": "BUY", "color": "#4caf50", "icon": "📈"}
    if score >= 48:
        return {"action": "持有觀望", "en": "HOLD", "color": "#ff9800", "icon": "⚖️"}
    if score >= 38:
        return {"action": "偏空減碼", "en": "REDUCE", "color": "#f44336", "icon": "📉"}
    return {"action": "建議出場", "en": "SELL", "color": "#b71c1c", "icon": "⛔"}


# Per-horizon weighting of the component scores. Margin risk is a penalty applied
# after weighting, scaled by horizon (斷頭 pressure bites hardest short-term).
# ⚠️ 命名說明：name 指的是「**用多長週期的指標計算**」，不是「建議你抱多久」。
# 這兩件事互相獨立——實證顯示即使只想抱一週，用「長線分」選股仍然最好。
# span 因此改寫為「取樣的指標長度」，避免被誤讀成建議持有期。
_HORIZON_CONFIG = [
    {
        "key": "ultra_short", "name": "極短線分", "span": "指標：1–3 天",
        "desc": "當日動能與量價、KD/RSI 極值、MA5",
        "weights": {"tech": 0.72, "news": 0.28},
        "margin_scale": 1.0,
    },
    {
        "key": "short", "name": "短線分", "span": "指標：約 1 週",
        "desc": "MA5/MA10、MACD 交叉、量能",
        "weights": {"tech": 0.70, "news": 0.18, "fund": 0.12},
        "margin_scale": 0.8,
    },
    {
        "key": "medium", "name": "中線分", "span": "指標：約 1 個月",
        "desc": "MA20/MA60 排列與斜率、近月報酬",
        "weights": {"tech": 0.42, "fund": 0.30, "tp": 0.20, "news": 0.08},
        "margin_scale": 0.35,
    },
    {
        "key": "long", "name": "長線分", "span": "指標：半年結構",
        "desc": "季線 MA120、MA60>MA120、半年報酬",
        "weights": {"fund": 0.45, "tp": 0.30, "tech": 0.20, "news": 0.05},
        "margin_scale": 0.1,
    },
]


# How much of the volume nudge each horizon feels (量價對短線影響最大)
_HORIZON_VOL_SCALE = {"ultra_short": 1.0, "short": 0.8, "medium": 0.4, "long": 0.15}


def generate_timeframe_recommendations(
    horizon_tech: dict,      # from calculate_horizon_scores(df)
    fund_score: int,
    news_score: int,
    target_upside_pct=None,
    margin_signal=None,
    volume_signal=None,
) -> list:
    """
    Produce a separate buy/sell recommendation for each holding horizon
    (極短線 / 短線 / 中線 / 長線). Each horizon blends the horizon-tuned technical
    sub-score with fundamentals / news / target-price / margin / volume at its
    own weights (volume & margin bite hardest on the short horizons).

    Returns a list of dicts (in horizon order) ready for display.
    """
    tp_score = _target_price_score(target_upside_pct)[0] if target_upside_pct is not None else None
    margin_penalty = (margin_signal or {}).get("penalty", 0) or 0
    vol_adj = (volume_signal or {}).get("score_adj", 0) or 0
    vol_rel = (volume_signal or {}).get("relationship")

    results = []
    for cfg in _HORIZON_CONFIG:
        w = cfg["weights"]
        tech_sub = horizon_tech.get(cfg["key"], {"score": 50, "drivers": []})
        tech_val = tech_sub["score"]

        # Build weighted sum only over components that are available; renormalize
        # weights so a missing target-price component doesn't deflate the score.
        parts = []  # (weight, value)
        if "tech" in w:
            parts.append((w["tech"], tech_val))
        if "fund" in w:
            parts.append((w["fund"], fund_score))
        if "news" in w:
            parts.append((w["news"], news_score))
        if "tp" in w and tp_score is not None:
            parts.append((w["tp"], tp_score))

        wsum = sum(p[0] for p in parts) or 1.0
        score = sum(p[0] * p[1] for p in parts) / wsum

        # Volume nudge, scaled by horizon relevance
        applied_vol = round(vol_adj * _HORIZON_VOL_SCALE.get(cfg["key"], 0.5))
        score += applied_vol

        # Margin penalty, scaled by horizon relevance
        applied_penalty = round(margin_penalty * cfg["margin_scale"])
        score -= applied_penalty
        score = max(0, min(100, int(round(score))))

        act = _action_for(score)
        drivers = list(tech_sub.get("drivers", []))[:3]
        if applied_vol != 0 and vol_rel:
            sign = f"+{applied_vol}" if applied_vol > 0 else str(applied_vol)
            drivers.append(f"量價：{vol_rel} ({sign})")
        if applied_penalty > 0:
            drivers.append(f"融資籌碼風險 (-{applied_penalty})")

        results.append({
            "key": cfg["key"],
            "name": cfg["name"],
            "span": cfg["span"],
            "desc": cfg["desc"],
            "score": score,
            "tech_score": tech_val,
            "margin_penalty": applied_penalty,
            "volume_adj": applied_vol,
            "drivers": drivers,
            **act,
        })
    return results


# ─── "Why this recommendation" rationale ──────────────────────────────────────

def build_rationale(rec: dict, volume_signal=None, margin_signal=None) -> dict:
    """
    Synthesize a plain-language explanation of *why* the recommendation is what it
    is: rank each dimension into supporting vs. pressuring factors, and weave the
    量價 × 融資 interplay into a short narrative.

    Returns {'headline', 'supports': [str], 'pressures': [str], 'synthesis': str}.
    """
    action = rec["action"]
    total = rec["total_score"]

    # Rank dimensions by how far they sit from neutral (50)
    dims = [
        ("技術面", rec.get("tech_score", 50), "均線/MACD/KD 等技術指標"),
        ("基本面", rec.get("fund_score", 50), "本益比/ROE/營收成長等財務體質"),
        ("消息面", rec.get("news_score", 50), "近期新聞與催化劑"),
    ]
    if rec.get("tp_score") is not None:
        dims.append(("目標價", rec["tp_score"], "相對合理估值的上漲/下跌空間"))

    supports, pressures = [], []
    for name, score, what in dims:
        if score >= 60:
            supports.append(f"{name}偏強（{score}/100）：{what}表現正向")
        elif score <= 40:
            pressures.append(f"{name}偏弱（{score}/100）：{what}表現不佳")

    # Volume
    if volume_signal and volume_signal.get("relationship") not in (None, "資料不足", "量平"):
        rel = volume_signal["relationship"]
        ex = volume_signal.get("explain", "")
        if volume_signal.get("signal") == "bullish":
            supports.append(f"量價關係為「{rel}」：{ex}")
        elif volume_signal.get("signal") == "bearish":
            pressures.append(f"量價關係為「{rel}」：{ex}")
        else:
            # neutral-but-notable (量縮價漲 = caution, 量縮價跌 = mild positive)
            if volume_signal.get("score_adj", 0) < 0:
                pressures.append(f"量價關係為「{rel}」：{ex}")
            else:
                supports.append(f"量價關係為「{rel}」：{ex}")

    # Margin — level (數量水準) and 增減趨勢 are separate considerations
    if margin_signal and margin_signal.get("level") in ("elevated", "high"):
        usage = margin_signal.get("usage_pct")
        pressures.append(
            f"融資使用率 {usage:.1f}% 偏高：散戶槓桿沉重、籌碼凌亂，"
            "短線易因融資追繳引發賣壓"
        )
    elif margin_signal and margin_signal.get("level") == "normal":
        supports.append("融資使用率正常：籌碼結構單純，無明顯散戶過度槓桿")

    if margin_signal:
        tlabel = margin_signal.get("trend_label")
        cl = margin_signal.get("change_long")
        nd = margin_signal.get("long_days", 0)
        win = f"近{nd}日" if nd else "近期"
        if tlabel in ("大增", "增加") and cl is not None:
            pressures.append(
                f"融資餘額{win}{tlabel} {cl:+.0f}%：散戶持續加碼槓桿、籌碼變重，"
                "後續若反轉易引發融資賣壓"
            )
        elif tlabel in ("大減", "減少") and cl is not None:
            supports.append(
                f"融資餘額{win}{tlabel} {cl:+.0f}%：散戶去槓桿、浮額洗清，籌碼趨於安定"
            )

    # Headline
    if total >= 58:
        headline = f"綜合評分 {total} → 「{action}」，多數面向偏多且有量能／籌碼支撐。"
    elif total >= 48:
        headline = f"綜合評分 {total} → 「{action}」，多空訊號互見，缺乏一致方向。"
    else:
        headline = f"綜合評分 {total} → 「{action}」，多數面向偏弱或存在籌碼／量價風險。"

    # Synthesis — lead with the strongest support and strongest pressure
    bits = []
    if supports:
        bits.append("主要支撐：" + support_join(supports[:2]))
    if pressures:
        bits.append("主要壓力／風險：" + support_join(pressures[:2]))
    if not supports and not pressures:
        bits.append("各面向多落在中性區間，暫無明顯偏向，建議觀望等待更明確訊號。")
    synthesis = " ".join(bits)

    return {
        "headline": headline,
        "supports": supports,
        "pressures": pressures,
        "synthesis": synthesis,
    }


def support_join(items: list) -> str:
    """Join reason fragments into a readable clause, trimming the trailing detail."""
    short = []
    for it in items:
        # keep the part before the colon (the headline of each factor)
        short.append(it.split("：")[0])
    return "、".join(short) + "。"
