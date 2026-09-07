import pandas as pd
import numpy as np


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    # Moving averages
    for n in [5, 10, 20, 60, 120]:
        df[f"MA{n}"] = close.rolling(n).mean()

    # EMA
    df["EMA12"] = close.ewm(span=12, adjust=False).mean()
    df["EMA26"] = close.ewm(span=26, adjust=False).mean()

    # MACD
    df["MACD"] = df["EMA12"] - df["EMA26"]
    df["MACD_signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_hist"] = df["MACD"] - df["MACD_signal"]

    # RSI (14)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=13, adjust=False).mean()
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["RSI"] = 100 - (100 / (1 + rs))

    # Bollinger Bands (20, 2)
    df["BB_mid"] = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    df["BB_upper"] = df["BB_mid"] + 2 * bb_std
    df["BB_lower"] = df["BB_mid"] - 2 * bb_std
    df["BB_width"] = (df["BB_upper"] - df["BB_lower"]) / df["BB_mid"]

    # KD Stochastic (9)
    low9 = low.rolling(9).min()
    high9 = high.rolling(9).max()
    rsv = (close - low9) / (high9 - low9).replace(0, np.nan) * 100
    df["K"] = rsv.ewm(com=2, adjust=False).mean()
    df["D"] = df["K"].ewm(com=2, adjust=False).mean()

    # Volume MA (short / medium / long — for multi-timescale 量能結構)
    df["Vol_MA5"] = volume.rolling(5).mean()
    df["Vol_MA20"] = volume.rolling(20).mean()
    df["Vol_MA60"] = volume.rolling(60).mean()

    # ATR (14)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    df["ATR"] = tr.ewm(span=14, adjust=False).mean()

    return df


def get_technical_signals(df: pd.DataFrame) -> list:
    """Return list of (name, signal, display_value, description) tuples."""
    if df.empty or len(df) < 2:
        return []

    last = df.iloc[-1]
    prev = df.iloc[-2]
    signals = []

    # RSI
    rsi = last.get("RSI", None)
    if rsi is not None:
        if rsi < 30:
            sig = "bullish"
            desc = "超賣區間，反彈機會"
        elif rsi > 70:
            sig = "bearish"
            desc = "超買區間，注意回檔"
        elif rsi < 45:
            sig = "bullish"
            desc = "偏低，有上漲空間"
        elif rsi > 60:
            sig = "bearish"
            desc = "偏高，動能減弱"
        else:
            sig = "neutral"
            desc = "中性區間"
        signals.append(("RSI", sig, f"{rsi:.1f}", desc))

    # MACD
    macd = last.get("MACD")
    macd_sig = last.get("MACD_signal")
    prev_macd = prev.get("MACD")
    prev_macd_sig = prev.get("MACD_signal")
    if macd is not None and macd_sig is not None:
        cross_up = macd > macd_sig and prev_macd <= prev_macd_sig
        cross_down = macd < macd_sig and prev_macd >= prev_macd_sig
        if cross_up:
            sig = "bullish"
            desc = "黃金交叉 — 買進訊號"
        elif cross_down:
            sig = "bearish"
            desc = "死亡交叉 — 賣出訊號"
        elif macd > macd_sig:
            sig = "bullish"
            desc = "MACD 高於訊號線"
        else:
            sig = "bearish"
            desc = "MACD 低於訊號線"
        signals.append(("MACD", sig, f"{macd:.2f}", desc))

    # KD
    k = last.get("K")
    d = last.get("D")
    prev_k = prev.get("K")
    prev_d = prev.get("D")
    if k is not None and d is not None:
        if k < 20:
            sig = "bullish"
            desc = "KD 超賣區"
        elif k > 80:
            sig = "bearish"
            desc = "KD 超買區"
        elif k > d and prev_k <= prev_d:
            sig = "bullish"
            desc = "KD 黃金交叉"
        elif k < d and prev_k >= prev_d:
            sig = "bearish"
            desc = "KD 死亡交叉"
        else:
            sig = "neutral"
            desc = "KD 中性"
        signals.append(("KD", sig, f"K:{k:.1f} D:{d:.1f}", desc))

    # MA trend
    close = last["Close"]
    ma20 = last.get("MA20")
    ma60 = last.get("MA60")
    if ma20 is not None and ma60 is not None:
        if close > ma20 > ma60:
            sig = "bullish"
            desc = "多頭排列 — 均線向上"
        elif close < ma20 < ma60:
            sig = "bearish"
            desc = "空頭排列 — 均線向下"
        elif close > ma20:
            sig = "bullish"
            desc = "站上 MA20"
        else:
            sig = "bearish"
            desc = "跌破 MA20"
        ma_status = f">{ma20:.1f}" if close > ma20 else f"<{ma20:.1f}"
        signals.append(("均線", sig, ma_status, desc))

    # Bollinger Band
    bb_upper = last.get("BB_upper")
    bb_lower = last.get("BB_lower")
    bb_mid = last.get("BB_mid")
    if bb_upper is not None and bb_lower is not None:
        bb_pct = (close - bb_lower) / (bb_upper - bb_lower) * 100 if (bb_upper - bb_lower) != 0 else 50
        if bb_pct < 20:
            sig = "bullish"
            desc = "接近布林下軌，超賣"
        elif bb_pct > 80:
            sig = "bearish"
            desc = "接近布林上軌，超買"
        else:
            sig = "neutral"
            desc = f"布林位置 {bb_pct:.0f}%"
        signals.append(("布林通道", sig, f"{bb_pct:.0f}%", desc))

    # Volume
    vol = last.get("Volume")
    vol_ma20 = last.get("Vol_MA20")
    if vol is not None and vol_ma20 is not None and vol_ma20 > 0:
        vol_ratio = vol / vol_ma20
        if vol_ratio > 1.5 and close > prev["Close"]:
            sig = "bullish"
            desc = "量增價漲 — 多方訊號"
        elif vol_ratio > 1.5 and close < prev["Close"]:
            sig = "bearish"
            desc = "量增價跌 — 空方訊號"
        elif vol_ratio < 0.5:
            sig = "neutral"
            desc = "縮量 — 觀望"
        else:
            sig = "neutral"
            desc = "量能正常"
        signals.append(("成交量", sig, f"{vol_ratio:.1f}x 均量", desc))

    return signals


def calculate_technical_score(df: pd.DataFrame) -> tuple[int, list]:
    """Returns (score 0-100, list of scoring reasons)."""
    if df.empty or len(df) < 30:
        return 50, ["資料不足，無法計算"]

    last = df.iloc[-1]
    prev = df.iloc[-2]
    score = 50
    reasons = []

    close = last["Close"]

    # RSI scoring
    rsi = last.get("RSI")
    if rsi is not None:
        if rsi < 25:
            score += 20
            reasons.append(f"RSI {rsi:.1f} 嚴重超賣，強力反彈訊號 (+20)")
        elif rsi < 35:
            score += 12
            reasons.append(f"RSI {rsi:.1f} 超賣區間，買進機會 (+12)")
        elif rsi < 45:
            score += 6
            reasons.append(f"RSI {rsi:.1f} 偏低，有上漲空間 (+6)")
        elif rsi > 75:
            score -= 20
            reasons.append(f"RSI {rsi:.1f} 嚴重超買，注意回檔 (-20)")
        elif rsi > 65:
            score -= 10
            reasons.append(f"RSI {rsi:.1f} 超買區間，動能減弱 (-10)")

    # MACD scoring
    macd = last.get("MACD")
    macd_sig = last.get("MACD_signal")
    prev_macd = prev.get("MACD")
    prev_macd_sig = prev.get("MACD_signal")
    if macd is not None and macd_sig is not None:
        if macd > macd_sig and (prev_macd is None or prev_macd <= prev_macd_sig):
            score += 15
            reasons.append("MACD 黃金交叉 (+15)")
        elif macd < macd_sig and (prev_macd is None or prev_macd >= prev_macd_sig):
            score -= 15
            reasons.append("MACD 死亡交叉 (-15)")
        elif macd > macd_sig:
            score += 8
            reasons.append("MACD 多頭排列 (+8)")
        else:
            score -= 8
            reasons.append("MACD 空頭排列 (-8)")

    # MA scoring
    ma20 = last.get("MA20")
    ma60 = last.get("MA60")
    ma120 = last.get("MA120")
    if ma20 is not None and close > ma20:
        score += 8
        reasons.append("股價站上 MA20 (+8)")
    elif ma20 is not None:
        score -= 8
        reasons.append("股價跌破 MA20 (-8)")
    if ma60 is not None and close > ma60:
        score += 8
        reasons.append("股價站上 MA60 (+8)")
    elif ma60 is not None:
        score -= 8
        reasons.append("股價跌破 MA60 (-8)")
    if ma20 is not None and ma60 is not None and ma20 > ma60:
        score += 5
        reasons.append("MA20 高於 MA60，多頭格局 (+5)")

    # Bollinger Band scoring
    bb_upper = last.get("BB_upper")
    bb_lower = last.get("BB_lower")
    if bb_upper is not None and bb_lower is not None and (bb_upper - bb_lower) > 0:
        bb_pct = (close - bb_lower) / (bb_upper - bb_lower) * 100
        if bb_pct < 15:
            score += 12
            reasons.append(f"股價接近布林下軌，超賣 (+12)")
        elif bb_pct > 85:
            score -= 12
            reasons.append(f"股價接近布林上軌，超買 (-12)")

    # KD scoring
    k = last.get("K")
    d = last.get("D")
    if k is not None:
        if k < 20:
            score += 10
            reasons.append(f"KD 超賣 K={k:.1f} (+10)")
        elif k > 80:
            score -= 10
            reasons.append(f"KD 超買 K={k:.1f} (-10)")

    # Volume scoring
    vol = last.get("Volume")
    vol_ma20 = last.get("Vol_MA20")
    if vol is not None and vol_ma20 is not None and vol_ma20 > 0:
        vol_ratio = vol / vol_ma20
        if vol_ratio > 1.5 and close > prev["Close"]:
            score += 8
            reasons.append(f"量增價漲 (成交量 {vol_ratio:.1f}x 均量) (+8)")
        elif vol_ratio > 1.5 and close < prev["Close"]:
            score -= 8
            reasons.append(f"量增價跌 (成交量 {vol_ratio:.1f}x 均量) (-8)")

    score = max(0, min(100, score))
    return score, reasons


def _pct_return(close: pd.Series, n: int):
    """Return % change over the last n bars, or None if not enough data."""
    if len(close) <= n:
        return None
    past = close.iloc[-n - 1]
    if past == 0 or pd.isna(past):
        return None
    return (close.iloc[-1] / past - 1) * 100


def calculate_risk_plan(df: pd.DataFrame, target_price=None) -> dict:
    """
    把「買進」變成可執行的計畫：用 ATR（真實波動幅度）推導停損、停利與風險報酬比。

    停損 = 現價 − 2×ATR（並參考近20日低點，取較不寬鬆者作為結構性停損）
    停利 = 加權目標價（若有且在合理範圍），否則 現價 + 3×ATR
    風險報酬比 R:R = 潛在獲利 / 潛在虧損 —— 低於 1.5 通常不值得進場。

    Returns {} when data is insufficient.
    """
    if df.empty or len(df) < 20 or "ATR" not in df.columns:
        return {}
    atr = df["ATR"].iloc[-1]
    if pd.isna(atr) or atr <= 0:
        return {}
    close = float(df["Close"].iloc[-1])
    atr = float(atr)

    atr_stop = close - 2 * atr
    swing_low = float(df["Low"].tail(20).min())
    # Use the tighter (higher) of the two so the stop stays meaningful
    stop = max(atr_stop, swing_low * 0.99)
    if stop >= close:
        stop = close - 2 * atr

    if target_price and 0 < target_price and target_price > close:
        take = float(target_price)
        take_src = "加權目標價"
    else:
        take = close + 3 * atr
        take_src = "現價 + 3×ATR"

    risk = close - stop
    reward = take - close
    rr = (reward / risk) if risk > 0 else None
    atr_pct = atr / close * 100

    if rr is None:
        verdict, v_color = "無法計算", "#78909c"
    elif rr >= 2.5:
        verdict, v_color = "風報比優異", "#4caf50"
    elif rr >= 1.5:
        verdict, v_color = "風報比合理", "#a9e34b"
    elif rr >= 1:
        verdict, v_color = "風報比偏低", "#ff9800"
    else:
        verdict, v_color = "風報比不佳，不宜追價", "#f44336"

    if atr_pct >= 5:
        vol_label = "高波動"
    elif atr_pct >= 2.5:
        vol_label = "中波動"
    else:
        vol_label = "低波動"

    return {
        "atr": atr,
        "atr_pct": atr_pct,
        "vol_label": vol_label,
        "stop": stop,
        "stop_pct": (stop / close - 1) * 100,
        "take": take,
        "take_pct": (take / close - 1) * 100,
        "take_source": take_src,
        "rr": rr,
        "verdict": verdict,
        "verdict_color": v_color,
        # Position sizing: risking 2% of capital on this trade
        "suggested_position_pct": min(100.0, (2.0 / (risk / close * 100)) ) if risk > 0 else None,
    }


def analyze_volume_price(df: pd.DataFrame) -> dict:
    """
    量價關係診斷（多時間尺度）。成交量與漲跌、融資互相牽動，是判斷「趨勢是否有量能
    支撐」的關鍵。除了看今日量能，更比較不同時間長度的均量以判斷量能是「結構性放大」
    還是「短暫爆量」：

      短期均量 (5日) / 中期均量 (20日) / 長期均量 (60日)
        5日 > 20日 > 60日 → 量能持續放大（結構性放量，趨勢較扎實）
        5日 < 20日 < 60日 → 量能持續萎縮（人氣退潮）
        5日 > 20日 但 20日 < 60日 → 僅短期爆量，長期基期仍低（追價須存疑）

    再結合股價方向得出量價型態：
      量增價漲：買盤積極、追價有量 → 健康偏多
      量增價跌：高檔出貨、賣壓沉重 → 偏空
      量縮價漲：追價買盤不足、動能弱 → 提防拉回
      量縮價跌：殺盤力道減弱、賣壓枯竭 → 短線有機會止穩

    「量增/量縮」以 5日均量 vs 20日均量 判定（較單日或 5日-vs-前5日 穩定）。

    Returns dict with 'relationship', 'signal', 'vol_ratio' (今日/20日均量),
    'vol_ma5/20/60', 'short_mid_ratio' (5日/20日), 'mid_long_ratio' (20日/60日),
    'vol_structure', 'price5_pct', 'is_spike', 'score_adj', 'reasons', 'explain'.
    """
    na = {
        "relationship": "資料不足", "signal": "neutral", "vol_ratio": None,
        "vol_ma5": None, "vol_ma20": None, "vol_ma60": None,
        "short_mid_ratio": None, "mid_long_ratio": None, "vol_structure": "資料不足",
        "vol_trend_pct": None, "price5_pct": None, "is_spike": False, "score_adj": 0,
        "reasons": ["成交量資料不足"], "explain": "成交量資料不足，無法判讀量價關係。",
    }
    if df.empty or len(df) < 25:
        return na

    vol = df["Volume"]
    close = df["Close"]
    last_vol = vol.iloc[-1]

    def _ma(col, n):
        if col in df.columns and not pd.isna(df[col].iloc[-1]):
            return float(df[col].iloc[-1])
        s = vol.rolling(n).mean().iloc[-1]
        return float(s) if not pd.isna(s) else None

    ma5 = _ma("Vol_MA5", 5)
    ma20 = _ma("Vol_MA20", 20)
    ma60 = _ma("Vol_MA60", 60) if len(vol) >= 60 else None
    if not ma20 or ma20 <= 0 or not ma5:
        return na

    vol_ratio = last_vol / ma20                      # 今日相對月均量
    short_mid = ma5 / ma20                            # 5日 vs 20日
    mid_long = (ma20 / ma60) if (ma60 and ma60 > 0) else None  # 20日 vs 60日

    # ── 多時間尺度量能結構 ────────────────────────────────────────────────────
    if short_mid > 1.12 and (mid_long is None or mid_long > 1.03):
        vol_structure = "量能持續放大"
        struct_bonus = 3
        struct_note = (
            f"5日均量為20日均量的 {short_mid*100:.0f}%"
            + (f"、20日為60日的 {mid_long*100:.0f}%" if mid_long else "")
            + "，量能結構性放大"
        )
    elif short_mid < 0.88 and (mid_long is None or mid_long < 0.97):
        vol_structure = "量能持續萎縮"
        struct_bonus = -3
        struct_note = (
            f"5日均量僅20日的 {short_mid*100:.0f}%"
            + (f"、20日為60日的 {mid_long*100:.0f}%" if mid_long else "")
            + "，量能結構性萎縮、人氣退潮"
        )
    elif short_mid > 1.12:
        vol_structure = "短期爆量、長期基期低"
        struct_bonus = 0
        struct_note = (
            f"5日均量雖達20日的 {short_mid*100:.0f}%，但20日均量僅60日的 "
            f"{(mid_long*100 if mid_long else 100):.0f}%，屬短期放量、長線人氣未回，追價須存疑"
        )
    elif short_mid < 0.88:
        vol_structure = "近期量縮"
        struct_bonus = 0
        struct_note = f"5日均量降為20日的 {short_mid*100:.0f}%，近期買賣氣轉淡"
    else:
        vol_structure = "量能持平"
        struct_bonus = 0
        struct_note = f"5日均量約與20日相當（{short_mid*100:.0f}%），量能穩定"

    # ── 量價型態（量增/量縮 by 5日vs20日均量；價 by 5日漲跌）──────────────────
    price5_pct = (close.iloc[-1] / close.iloc[-6] - 1) * 100 if len(close) > 6 else 0.0
    vol_rising = short_mid > 1.08
    vol_falling = short_mid < 0.92
    price_up = price5_pct > 1
    price_down = price5_pct < -1

    reasons = []
    if vol_rising and price_up:
        relationship, signal, base = "量增價漲", "bullish", 8
        explain = "近期均量放大且股價走揚，買盤積極、追價有量能支撐，屬健康的多方型態。"
    elif vol_rising and price_down:
        relationship, signal, base = "量增價跌", "bearish", -9
        explain = "近期均量放大但股價下跌，代表有人趁勢出貨、賣壓沉重，為偏空的量價背離。"
    elif vol_falling and price_up:
        relationship, signal, base = "量縮價漲", "neutral", -5
        explain = "股價上漲但均量萎縮，追價買盤不足、漲勢動能偏弱，需提防量能跟不上而拉回。"
    elif vol_falling and price_down:
        relationship, signal, base = "量縮價跌", "neutral", 3
        explain = "股價下跌但均量同步萎縮，殺盤力道減弱、賣壓可能趨於枯竭，短線有止穩機會。"
    else:
        relationship, signal, base = "量平", "neutral", 0
        explain = "均量與前期相近、股價變動不大，多空暫時僵持，觀望為宜。"

    # Structural volume reinforces a directional 量價 pattern, not a neutral one
    score_adj = base
    if signal == "bullish":
        score_adj += max(0, struct_bonus)
    elif signal == "bearish":
        score_adj += min(0, -abs(struct_bonus)) if struct_bonus > 0 else struct_bonus
        # 放量出貨更空：放大時再扣
        if vol_structure == "量能持續放大":
            score_adj -= 3
    reasons.append(f"{relationship}（今日 {vol_ratio:.1f}x 月均量、股價5日 {price5_pct:+.0f}%）({score_adj:+d})")
    reasons.append(f"量能結構：{vol_structure} — {struct_note}")

    is_spike = vol_ratio > 2.5
    if is_spike:
        reasons.append(f"今日爆量（約 {vol_ratio:.1f}x 月均量），籌碼快速換手、波動加大")
        explain += f"另外今日爆出 {vol_ratio:.1f} 倍月均量，籌碼大幅換手，短線波動將加大。"

    explain += f"（量能結構：{vol_structure}）"

    return {
        "relationship": relationship,
        "signal": signal,
        "vol_ratio": vol_ratio,
        "vol_ma5": ma5,
        "vol_ma20": ma20,
        "vol_ma60": ma60,
        "short_mid_ratio": short_mid,
        "mid_long_ratio": mid_long,
        "vol_structure": vol_structure,
        "vol_trend_pct": (short_mid - 1) * 100,
        "price5_pct": price5_pct,
        "is_spike": is_spike,
        "score_adj": score_adj,
        "reasons": reasons,
        "explain": explain,
    }


def calculate_horizon_scores(df: pd.DataFrame) -> dict:
    """
    Compute separate technical scores (0-100) tuned to four holding horizons.
    Different horizons weigh different indicators:

      極短線 (1-3天):  當日動能 + 均值回歸（KD/RSI 極值、量價、跳空、貼近 MA5）
      短線   (1週內):  MA5/MA10 短均、MACD 交叉、KD、量能
      中線   (1個月+): MA20/MA60 排列與斜率、MACD 位置、多空格局
      長線   (半年+):  MA60/MA120 長多結構、半年報酬、長均斜率

    Returns {'ultra_short': {...}, 'short': {...}, 'medium': {...}, 'long': {...}}
    where each value is {'score': int, 'drivers': [str, ...]}.
    """
    empty = {"score": 50, "drivers": ["資料不足"]}
    if df.empty or len(df) < 20:
        return {k: dict(empty) for k in ("ultra_short", "short", "medium", "long")}

    last = df.iloc[-1]
    prev = df.iloc[-2]
    close = last["Close"]
    c = df["Close"]

    ma5 = last.get("MA5"); ma10 = last.get("MA10"); ma20 = last.get("MA20")
    ma60 = last.get("MA60"); ma120 = last.get("MA120")
    ma5_prev = prev.get("MA5"); ma20_prev = prev.get("MA20")
    ma60_prev = prev.get("MA60"); ma120_prev = prev.get("MA120")
    rsi = last.get("RSI")
    macd = last.get("MACD"); macd_sig = last.get("MACD_signal")
    prev_macd = prev.get("MACD"); prev_macd_sig = prev.get("MACD_signal")
    k = last.get("K"); d = last.get("D")
    prev_k = prev.get("K"); prev_d = prev.get("D")
    vol = last.get("Volume"); vol_ma20 = last.get("Vol_MA20")
    vol_ratio = (vol / vol_ma20) if (vol and vol_ma20 and vol_ma20 > 0) else 1.0
    up_today = close > prev["Close"]

    # ── 極短線 (1-3天) — 動能 + 均值回歸 ──────────────────────────────────────
    us = 50
    usd = []
    if rsi is not None:
        if rsi < 30:
            us += 16; usd.append(f"RSI {rsi:.0f} 超賣，短線易反彈 (+16)")
        elif rsi < 40:
            us += 8; usd.append(f"RSI {rsi:.0f} 偏低 (+8)")
        elif rsi > 78:
            us -= 16; usd.append(f"RSI {rsi:.0f} 嚴重超買，短線易回檔 (-16)")
        elif rsi > 68:
            us -= 8; usd.append(f"RSI {rsi:.0f} 超買 (-8)")
    if k is not None and d is not None:
        if k > d and prev_k is not None and prev_k <= prev_d:
            us += 14; usd.append("KD 當日黃金交叉 (+14)")
        elif k < d and prev_k is not None and prev_k >= prev_d:
            us -= 14; usd.append("KD 當日死亡交叉 (-14)")
        elif k < 20:
            us += 8; usd.append(f"KD 超賣 K={k:.0f} (+8)")
        elif k > 80:
            us -= 8; usd.append(f"KD 超買 K={k:.0f} (-8)")
    if vol_ratio > 1.5 and up_today:
        us += 12; usd.append(f"量增價漲 {vol_ratio:.1f}x (+12)")
    elif vol_ratio > 1.5 and not up_today:
        us -= 12; usd.append(f"量增價跌 {vol_ratio:.1f}x (-12)")
    if ma5 is not None:
        if close > ma5:
            us += 6; usd.append("站上 MA5 (+6)")
        else:
            us -= 6; usd.append("跌破 MA5 (-6)")
    if not usd:
        usd.append("短線訊號中性")

    # ── 短線 (1週內) — 短均 + MACD + 量 ───────────────────────────────────────
    sh = 50
    shd = []
    if ma5 is not None and ma10 is not None:
        if close > ma5 > ma10:
            sh += 14; shd.append("短均多頭（價>MA5>MA10）(+14)")
        elif close < ma5 < ma10:
            sh -= 14; shd.append("短均空頭（價<MA5<MA10）(-14)")
        elif close > ma5:
            sh += 6; shd.append("站上 MA5 (+6)")
        else:
            sh -= 6; shd.append("跌破 MA5 (-6)")
    if ma5 is not None and ma5_prev is not None:
        if ma5 > ma5_prev:
            sh += 6; shd.append("MA5 向上 (+6)")
        else:
            sh -= 6; shd.append("MA5 向下 (-6)")
    if macd is not None and macd_sig is not None:
        if macd > macd_sig and prev_macd is not None and prev_macd <= prev_macd_sig:
            sh += 14; shd.append("MACD 黃金交叉 (+14)")
        elif macd < macd_sig and prev_macd is not None and prev_macd >= prev_macd_sig:
            sh -= 14; shd.append("MACD 死亡交叉 (-14)")
        elif macd > macd_sig:
            sh += 7; shd.append("MACD 多頭 (+7)")
        else:
            sh -= 7; shd.append("MACD 空頭 (-7)")
    if rsi is not None and rsi > 75:
        sh -= 6; shd.append(f"RSI {rsi:.0f} 超買，短線追高風險 (-6)")
    if vol_ratio > 1.3 and up_today:
        sh += 5; shd.append("帶量走揚 (+5)")
    if not shd:
        shd.append("短線訊號中性")

    # ── 中線 (1個月+) — 中均排列 + 格局 ───────────────────────────────────────
    md = 50
    mdd = []
    if ma20 is not None and ma60 is not None:
        if close > ma20 > ma60:
            md += 18; mdd.append("多頭排列（價>MA20>MA60）(+18)")
        elif close < ma20 < ma60:
            md -= 18; mdd.append("空頭排列（價<MA20<MA60）(-18)")
        elif close > ma20:
            md += 8; mdd.append("站上 MA20 (+8)")
        else:
            md -= 8; mdd.append("跌破 MA20 (-8)")
    if ma20 is not None and ma20_prev is not None:
        if ma20 > ma20_prev:
            md += 8; mdd.append("MA20 走揚 (+8)")
        else:
            md -= 8; mdd.append("MA20 走弱 (-8)")
    if macd is not None and macd_sig is not None:
        if macd > macd_sig:
            md += 6; mdd.append("MACD 位於訊號線上 (+6)")
        else:
            md -= 6; mdd.append("MACD 位於訊號線下 (-6)")
    r20 = _pct_return(c, 20)
    if r20 is not None:
        if r20 > 25:
            md -= 5; mdd.append(f"月漲 {r20:.0f}% 偏多但過熱 (-5)")
        elif r20 > 0:
            md += 4; mdd.append(f"近月報酬 {r20:+.0f}% (+4)")
        elif r20 < -12:
            md -= 6; mdd.append(f"近月報酬 {r20:.0f}% 弱勢 (-6)")
    if not mdd:
        mdd.append("中線訊號中性")

    # ── 長線 (半年+) — 長多結構 + 半年報酬 ────────────────────────────────────
    lg = 50
    lgd = []
    if ma120 is not None:
        if close > ma120:
            lg += 14; lgd.append("站上季線 MA120，長多 (+14)")
        else:
            lg -= 14; lgd.append("跌破季線 MA120，長空 (-14)")
    if ma60 is not None and ma120 is not None:
        if ma60 > ma120:
            lg += 12; lgd.append("MA60 > MA120，長期均線多頭 (+12)")
        else:
            lg -= 12; lgd.append("MA60 < MA120，長期均線空頭 (-12)")
    if ma120 is not None and ma120_prev is not None:
        if ma120 > ma120_prev:
            lg += 8; lgd.append("季線向上 (+8)")
        else:
            lg -= 8; lgd.append("季線向下 (-8)")
    r120 = _pct_return(c, 120)
    if r120 is not None:
        if r120 > 60:
            lg += 4; lgd.append(f"半年漲 {r120:.0f}%，強勢但漲幅已大 (+4)")
        elif r120 > 10:
            lg += 10; lgd.append(f"半年報酬 {r120:+.0f}%，長期趨勢向上 (+10)")
        elif r120 < -15:
            lg -= 10; lgd.append(f"半年報酬 {r120:.0f}%，長期趨勢向下 (-10)")
    if not lgd:
        lgd.append("長線訊號中性")

    return {
        "ultra_short": {"score": max(0, min(100, int(us))), "drivers": usd},
        "short":       {"score": max(0, min(100, int(sh))), "drivers": shd},
        "medium":      {"score": max(0, min(100, int(md))), "drivers": mdd},
        "long":        {"score": max(0, min(100, int(lg))), "drivers": lgd},
    }
