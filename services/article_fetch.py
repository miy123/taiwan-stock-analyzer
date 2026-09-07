"""
Article excerpt fetcher.

Tries to follow redirects and extract the first ~250 meaningful characters
from a news article page. Many sites block scrapers or require login —
failures are silent and return empty string.

Used only for "catalyst" news items and analyst target articles
(not every headline) to keep latency reasonable.
"""

import re
import requests
import streamlit as st

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Sites that typically block automated requests — skip immediately
_BLOCKED_DOMAINS = {
    "bloomberg.com", "wsj.com", "ft.com", "barrons.com",
    "reuters.com", "nytimes.com", "seekingalpha.com",
}


def _domain(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def _clean_text(raw: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", raw).strip()
    # Remove common noise patterns
    text = re.sub(r"(廣告|Advertisement|ADVERTISEMENT|訂閱|Subscribe).*", "", text)
    text = text.strip()
    if len(text) < 40:
        return ""
    return text[:max_chars] + ("…" if len(text) > max_chars else "")


@st.cache_data(ttl=7200, show_spinner=False)
def fetch_article_excerpt(url: str, max_chars: int = 260) -> str:
    """
    Return a brief text excerpt from the article at `url`.
    Returns empty string on any failure (paywall, block, timeout, parse error).
    """
    if not url or url in ("#", ""):
        return ""
    if _domain(url) in _BLOCKED_DOMAINS:
        return ""

    try:
        resp = requests.get(url, timeout=7, allow_redirects=True, headers=_HEADERS)
        if resp.status_code != 200:
            return ""
        # Reject binary / non-HTML content
        ct = resp.headers.get("Content-Type", "")
        if "html" not in ct:
            return ""
    except Exception:
        return ""

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.content, "lxml")

        # Strip noise
        for tag in soup(["script", "style", "nav", "header", "footer",
                         "aside", "figure", "figcaption", "form", "button",
                         "iframe", "noscript"]):
            tag.decompose()

        # Try structured article selectors first (common Taiwan finance sites)
        selectors = [
            # Generic
            "article", "main",
            # Cnyes 鉅亨
            ".news__article__content", ".paragraph__text",
            # Yahoo Finance TW
            ".caas-body",
            # CMoney
            ".article-content",
            # MoneyDJ
            "#MainContent_Contents",
            # ETtoday
            ".story",
            # UDN
            ".article-body__editor",
            # 工商時報
            ".article-body",
        ]

        content_el = None
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                content_el = el
                break

        if content_el:
            paras = content_el.find_all("p")
        else:
            paras = soup.find_all("p")

        # Keep paragraphs with at least 20 characters of real text
        texts = [
            p.get_text(" ", strip=True)
            for p in paras
            if len(p.get_text(strip=True)) >= 20
        ]

        if not texts:
            # Fallback: just grab body text
            body = soup.find("body")
            raw = body.get_text(" ", strip=True) if body else ""
        else:
            raw = " ".join(texts[:6])

        return _clean_text(raw, max_chars)

    except Exception:
        return ""
