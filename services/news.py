"""
News service with category-aware analysis.

Instead of just counting positive/negative keywords, we detect the *type*
of news event (tech breakthrough, new order, legal risk, …) and apply a
category-specific score impact.  This means a headline like
「台積電突破 2nm 製程」is scored much higher than a generic positive headline.
"""

import requests
import streamlit as st
from xml.etree import ElementTree


# ── Category definitions ───────────────────────────────────────────────────────
# score_impact: base score delta applied when this category is detected.
# A higher weight means the category matters more to future price.

NEWS_CATEGORIES = {
    "tech_breakthrough": {
        "name": "技術突破",
        "emoji": "🔬",
        "color": "#7b61ff",
        "sentiment": "positive",
        "score_impact": 30,
        "catalyst": True,          # Flag: likely not yet in price
        "keywords": [
            "突破", "研發成功", "量產", "新製程", "新技術", "專利", "創新技術",
            "良率提升", "升級", "世代", "奈米", "nm", "製程技術", "晶片",
            "AI晶片", "先進封裝", "HBM", "CoWoS",
        ],
    },
    "new_order": {
        "name": "訂單大增",
        "emoji": "📦",
        "color": "#4caf50",
        "sentiment": "positive",
        "score_impact": 22,
        "catalyst": True,
        "keywords": [
            "大單", "訂單", "合約", "得標", "中標", "代工", "拿單",
            "新客戶", "客戶導入", "出貨", "供貨", "供應商", "備貨",
            "爆單", "旺季", "訂單能見度",
        ],
    },
    "strategic_partnership": {
        "name": "策略合作",
        "emoji": "🤝",
        "color": "#29b6f6",
        "sentiment": "positive",
        "score_impact": 18,
        "catalyst": True,
        "keywords": [
            "合作", "結盟", "聯盟", "合資", "入股", "策略合作",
            "簽約", "MOU", "協議", "授權", "技術轉移",
        ],
    },
    "capacity_expansion": {
        "name": "擴產佈局",
        "emoji": "🏭",
        "color": "#26a69a",
        "sentiment": "positive",
        "score_impact": 15,
        "catalyst": True,
        "keywords": [
            "擴廠", "擴產", "新廠", "增設", "投資", "海外佈局",
            "美國廠", "日本廠", "德國廠", "新產線", "資本支出",
        ],
    },
    "earnings_beat": {
        "name": "業績超預期",
        "emoji": "💰",
        "color": "#66bb6a",
        "sentiment": "positive",
        "score_impact": 20,
        "catalyst": False,          # Typically already in price quickly
        "keywords": [
            "超預期", "優於預期", "創新高", "業績亮眼", "大幅成長",
            "超車", "強勁", "淨利成長", "EPS創高", "季增",
        ],
    },
    "general_positive": {
        "name": "一般利多",
        "emoji": "🟢",
        "color": "#a5d6a7",
        "sentiment": "positive",
        "score_impact": 8,
        "catalyst": False,
        "keywords": [
            "成長", "獲利", "利多", "看好", "買進", "上漲", "漲停",
            "推薦", "佳績", "增加", "好消息", "beat", "growth",
            "upgrade", "buy", "outperform", "rally", "profit",
            "record", "strong", "positive", "bullish",
        ],
    },
    "legal_risk": {
        "name": "法律風險",
        "emoji": "⚖️",
        "color": "#ef5350",
        "sentiment": "negative",
        "score_impact": -28,
        "catalyst": True,
        "keywords": [
            "訴訟", "罰款", "調查", "違規", "制裁", "仲裁",
            "侵權", "反壟斷", "下架", "禁令", "起訴",
        ],
    },
    "operational_issue": {
        "name": "營運問題",
        "emoji": "⚠️",
        "color": "#ffa726",
        "sentiment": "negative",
        "score_impact": -22,
        "catalyst": True,
        "keywords": [
            "裁員", "停工", "缺料", "良率下滑", "罷工", "意外",
            "災害", "供應中斷", "延誤", "資安", "駭客",
        ],
    },
    "earnings_miss": {
        "name": "財報不佳",
        "emoji": "📉",
        "color": "#ef5350",
        "sentiment": "negative",
        "score_impact": -25,
        "catalyst": False,
        "keywords": [
            "虧損", "下修", "衰退", "低於預期", "獲利下滑",
            "獲利衰退", "營收下滑", "盈利下降", "miss", "downgrade",
        ],
    },
    "competition": {
        "name": "競爭壓力",
        "emoji": "🥊",
        "color": "#ff7043",
        "sentiment": "negative",
        "score_impact": -15,
        "catalyst": True,
        "keywords": [
            "市佔流失", "被替代", "競爭加劇", "降價壓力", "殺價",
            "失去訂單", "轉單", "對手", "rivals",
        ],
    },
    "general_negative": {
        "name": "一般利空",
        "emoji": "🔴",
        "color": "#ef9a9a",
        "sentiment": "negative",
        "score_impact": -8,
        "catalyst": False,
        "keywords": [
            "下跌", "跌停", "利空", "賣出", "危機", "警示", "風險",
            "看壞", "警告", "降", "崩", "大跌", "疲弱",
            "sell", "underperform", "loss", "decline", "weak",
            "negative", "bearish", "warning", "risk", "concern",
        ],
    },
}


def _detect_category(text: str) -> tuple:
    """
    Returns (category_key, category_dict) for the best-matching category,
    or ("neutral", None) if nothing matches.
    Checks specific categories before generic ones so a tech headline isn't
    mis-scored as just "general positive".
    """
    text_lower = text.lower()

    # Priority order: specific categories first, generic last
    priority = [
        "tech_breakthrough", "new_order", "strategic_partnership",
        "capacity_expansion", "legal_risk", "operational_issue",
        "competition", "earnings_beat", "earnings_miss",
        "general_positive", "general_negative",
    ]

    for key in priority:
        cat = NEWS_CATEGORIES[key]
        if any(kw in text_lower for kw in cat["keywords"]):
            return key, cat

    return "neutral", None


@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_google_news(query: str) -> list:
    url = (
        "https://news.google.com/rss/search"
        f"?q={requests.utils.quote(query)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    )
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.content)
        items = []
        for item in root.findall(".//item")[:15]:
            items.append({
                "title": item.findtext("title", ""),
                "link": item.findtext("link", ""),
                "pub_date": item.findtext("pubDate", ""),
                "source": item.findtext("source", ""),
            })
        return items
    except Exception:
        return []


def get_all_news(stock_id: str, company_name: str, yf_news: list) -> list:
    combined = []

    # yfinance news
    for item in yf_news[:8]:
        content = item.get("content", {})
        title = (content.get("title") if isinstance(content, dict) else None) or item.get("title", "")
        link = item.get("link") or item.get("url", "")
        publisher = item.get("publisher", "")
        pub_time = item.get("providerPublishTime", 0)

        if title:
            cat_key, cat = _detect_category(title)
            combined.append({
                "title": title,
                "link": link,
                "source": publisher,
                "pub_date": pub_time,
                "category_key": cat_key,
                "category": cat,
                "sentiment": cat["sentiment"] if cat else "neutral",
                "score_impact": cat["score_impact"] if cat else 0,
                "is_catalyst": cat["catalyst"] if cat else False,
                "lang": "en",
            })

    # Google News (Chinese)
    query = f"{stock_id} {company_name} 股票"
    for item in _fetch_google_news(query)[:10]:
        title = item["title"]
        cat_key, cat = _detect_category(title)
        combined.append({
            "title": title,
            "link": item["link"],
            "source": item["source"] or "Google News",
            "pub_date": item["pub_date"],
            "category_key": cat_key,
            "category": cat,
            "sentiment": cat["sentiment"] if cat else "neutral",
            "score_impact": cat["score_impact"] if cat else 0,
            "is_catalyst": cat["catalyst"] if cat else False,
            "lang": "zh",
        })

    return combined


def calculate_news_sentiment_score(news_list: list) -> tuple:
    """
    Returns (score 0-100, reasons list).

    Score is based on the sum of category-specific score_impacts, clamped
    to 0-100 around a neutral baseline of 50.
    Category-specific impacts are much stronger than generic keyword counts:
      - 技術突破 headline = +30
      - 大單 = +22
      - 訴訟 = -28
      - 一般利多 = +8
    """
    if not news_list:
        return 50, ["無法取得新聞資料"]

    total_impact = sum(n["score_impact"] for n in news_list)
    n = len(news_list)

    # Normalise: each headline contributes up to ±30, n headlines → clamp ±60
    # Then map [-60, +60] → [10, 90] around 50
    clamped = max(-60, min(60, total_impact))
    score = int(50 + clamped * 40 / 60)
    score = max(0, min(100, score))

    # Category counts
    cat_counts = {}
    for item in news_list:
        if item["category"]:
            name = item["category"]["name"]
            cat_counts[name] = cat_counts.get(name, 0) + 1

    pos_count = sum(1 for n in news_list if n["sentiment"] == "positive")
    neg_count = sum(1 for n in news_list if n["sentiment"] == "negative")
    catalyst_count = sum(1 for n in news_list if n["is_catalyst"] and n["sentiment"] == "positive")

    reasons = [
        f"分析 {len(news_list)} 篇新聞（正面 {pos_count} 篇，負面 {neg_count} 篇）",
    ]
    if cat_counts:
        top_cats = sorted(cat_counts.items(), key=lambda x: -x[1])[:3]
        reasons.append("主要類別：" + "、".join(f"{k} {v}篇" for k, v in top_cats))
    if catalyst_count > 0:
        reasons.append(f"偵測到 {catalyst_count} 則潛在尚未完全反映的利多催化劑")

    if total_impact > 20:
        reasons.append("整體消息面偏多，市場信心強")
    elif total_impact < -20:
        reasons.append("整體消息面偏空，市場疑慮較多")
    else:
        reasons.append("消息面中性，市場觀望")

    return score, reasons


def get_catalysts(news_list: list) -> dict:
    """
    Extract positive and negative catalysts — news events that are likely
    NOT yet fully reflected in the stock price.
    Returns {"positive": [...], "negative": [...]}
    """
    pos = [n for n in news_list if n["is_catalyst"] and n["sentiment"] == "positive"]
    neg = [n for n in news_list if n["is_catalyst"] and n["sentiment"] == "negative"]
    return {"positive": pos, "negative": neg}
