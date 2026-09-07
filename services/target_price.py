import pandas as pd


def calculate_target_price(df: pd.DataFrame, info: dict, fundamentals: dict) -> dict:
    """
    Multi-method target price calculation.
    Uses price-multiple expansion (safer than raw EPS/BV for Taiwan stocks via yfinance)
    to avoid currency/unit inconsistencies in per-share data.
    """
    current_price = df["Close"].iloc[-1]
    result = {
        "current_price": current_price,
        "methods": [],
        "recommended_target": None,
        "upside_pct": None,
        "target_low": None,
        "target_high": None,
    }

    weighted_targets = []  # (price, weight)

    # ── Method 1: Analyst Consensus ──────────────────────────────────────────
    analyst_mean = info.get("targetMeanPrice")
    analyst_low = info.get("targetLowPrice")
    analyst_high = info.get("targetHighPrice")
    analyst_count = info.get("numberOfAnalystOpinions") or 0

    if analyst_mean and analyst_mean > 0:
        upside = (analyst_mean - current_price) / current_price * 100
        lo_str = f"{analyst_low:.0f}" if analyst_low else "N/A"
        hi_str = f"{analyst_high:.0f}" if analyst_high else "N/A"
        result["methods"].append({
            "name": "分析師共識目標價",
            "emoji": "👨‍💼",
            "target": analyst_mean,
            "target_low": analyst_low,
            "target_high": analyst_high,
            "upside_pct": upside,
            "detail": f"{analyst_count} 位分析師　低 {lo_str} ／ 均 {analyst_mean:.0f} ／ 高 {hi_str}",
            "confidence": "高",
            "confidence_color": "#4caf50",
        })
        weighted_targets.append((analyst_mean, 4))

    # ── Method 2: P/E Multiple Expansion ────────────────────────────────────
    # Derive fair P/E from growth, compare to current P/E, project target
    trailing_pe = info.get("trailingPE")
    forward_pe = info.get("forwardPE")
    current_pe = forward_pe or trailing_pe

    rev_growth = (info.get("revenueGrowth") or 0) * 100
    earn_growth = (info.get("earningsGrowth") or 0) * 100
    growth_rate = max(rev_growth, earn_growth, 0)

    if current_pe and 3 < current_pe < 200:
        # Fair P/E based on growth (conservative)
        if growth_rate > 25:
            fair_pe = 35
        elif growth_rate > 15:
            fair_pe = 27
        elif growth_rate > 8:
            fair_pe = 20
        elif growth_rate > 0:
            fair_pe = 15
        else:
            fair_pe = 12

        expansion = fair_pe / current_pe
        # Skip if ratio is extreme — indicates yfinance .TW P/E data quality issue
        if 0.4 < expansion < 1.6:
            pe_target = current_price * expansion
            upside = (pe_target - current_price) / current_price * 100
            pe_used = "預估" if forward_pe else "歷史"
            result["methods"].append({
                "name": "本益比評估 (P/E)",
                "emoji": "📊",
                "target": pe_target,
                "target_low": None,
                "target_high": None,
                "upside_pct": upside,
                "detail": (f"目前{pe_used}P/E {current_pe:.1f} → 合理P/E {fair_pe} "
                           f"（成長率 {growth_rate:.1f}%）"),
                "confidence": "中",
                "confidence_color": "#ff9800",
            })
            weighted_targets.append((pe_target, 2))

    # ── Method 3: P/B Multiple Expansion ────────────────────────────────────
    current_pb = info.get("priceToBook")
    roe_pct = (info.get("returnOnEquity") or 0) * 100

    if current_pb and 0.1 < current_pb < 50 and roe_pct > 0:
        if roe_pct > 25:
            fair_pb = 5.0
        elif roe_pct > 20:
            fair_pb = 3.5
        elif roe_pct > 15:
            fair_pb = 2.5
        elif roe_pct > 10:
            fair_pb = 2.0
        elif roe_pct > 5:
            fair_pb = 1.5
        else:
            fair_pb = 1.0

        pb_target = current_price * (fair_pb / current_pb)
        if pb_target > 0:
            upside = (pb_target - current_price) / current_price * 100
            result["methods"].append({
                "name": "股價淨值比 (P/B)",
                "emoji": "🏦",
                "target": pb_target,
                "target_low": None,
                "target_high": None,
                "upside_pct": upside,
                "detail": (f"目前P/B {current_pb:.1f} → 合理P/B {fair_pb} "
                           f"（ROE {roe_pct:.1f}%）"),
                "confidence": "中",
                "confidence_color": "#ff9800",
            })
            weighted_targets.append((pb_target, 1))

    # ── Method 4: Dividend Yield Reversion ──────────────────────────────────
    # If dividend yield is significantly above/below fair yield, price reverts
    # NOTE: yfinance's dividendYield is a PERCENT number (3.84 = 3.84%); normalise
    # to a fraction so the displayed "目前殖利率" isn't 100x too large.
    from services.fundamental import _normalize_dividend_yield
    div_yield = _normalize_dividend_yield(info) or 0
    div_rate = info.get("dividendRate") or 0
    if div_yield > 0.005 and div_rate > 0:
        # Fair yield for Taiwan stable stocks ~3.5–4%
        fair_yield = 0.035
        ddm_target = div_rate / fair_yield
        if ddm_target > 0:
            upside = (ddm_target - current_price) / current_price * 100
            result["methods"].append({
                "name": "殖利率還原法",
                "emoji": "💰",
                "target": ddm_target,
                "target_low": None,
                "target_high": None,
                "upside_pct": upside,
                "detail": (f"年度股利 {div_rate:.2f} ÷ 合理殖利率 {fair_yield*100:.1f}%"
                           f"（目前 {div_yield*100:.1f}%）"),
                "confidence": "低",
                "confidence_color": "#78909c",
            })
            weighted_targets.append((ddm_target, 1))

    # ── Method 5: Technical — 6-Month High ───────────────────────────────────
    if len(df) >= 20:
        lookback = min(126, len(df))
        recent_high = df["High"].tail(lookback).max()
        upside_r = (recent_high - current_price) / current_price * 100
        result["methods"].append({
            "name": "近期技術阻力（6個月高點）",
            "emoji": "📈",
            "target": recent_high,
            "target_low": None,
            "target_high": None,
            "upside_pct": upside_r,
            "detail": f"近6個月最高 {recent_high:.2f}，為短線技術壓力區",
            "confidence": "低",
            "confidence_color": "#78909c",
        })
        weighted_targets.append((recent_high, 1))

    # ── Compute weighted recommended target ──────────────────────────────────
    # Exclude outliers: keep within 50%–170% of current price (1–2 yr reasonable range)
    filtered = [
        (t, w) for t, w in weighted_targets
        if 0.5 * current_price < t < 1.7 * current_price
    ]

    if filtered:
        total_w = sum(w for _, w in filtered)
        rec_target = sum(t * w for t, w in filtered) / total_w
        all_prices = [t for t, _ in filtered]

        result["recommended_target"] = round(rec_target, 1)
        result["upside_pct"] = (rec_target - current_price) / current_price * 100
        result["target_low"] = min(all_prices)
        result["target_high"] = max(all_prices)
    elif weighted_targets:
        # Fallback: use all targets
        all_prices = [t for t, _ in weighted_targets]
        result["recommended_target"] = round(
            sum(t * w for t, w in weighted_targets) / sum(w for _, w in weighted_targets), 1
        )
        result["upside_pct"] = (result["recommended_target"] - current_price) / current_price * 100
        result["target_low"] = min(all_prices)
        result["target_high"] = max(all_prices)

    return result
