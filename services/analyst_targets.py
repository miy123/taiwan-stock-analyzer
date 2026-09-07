"""
Analyst target price extraction from news headlines + article excerpts.

Two-layer approach:
  1. Regex on the headline (fast, always works)
  2. Fetch article excerpt (slow first time, cached; skipped if headline already found a TWD price)

Currency rules:
  - Explicit TWD markers (NT$, TWD, 元, 新台幣, 台幣) → TWD
  - Explicit USD markers (USD, US$, 美元, dollar) → USD
  - Bare "$XXX" — if price > 400 → TWD, else USD (most Taiwan stocks don't have USD ADR > $400)
  - "unknown" — shown with ⚠️ and not included in the weighted target calculation

ADR notes:
  - Some Taiwan companies list ADRs on US exchanges; 1 ADR ≠ 1 TWD share.
  - An approximate ratio table is included below for the most common ones.
  - ADR USD prices are shown with a conversion note using a live USDTWD rate.
"""

import re
import requests
import streamlit as st
from xml.etree import ElementTree

# ── Taiwan ADR reference table ────────────────────────────────────────────────
# adr_ratio: how many Taiwan ordinary shares per 1 ADR
# e.g. TSMC TSM: 1 ADR = 5 ordinary shares (as of 2023)
ADR_STOCKS = {
    "2330": {"adr_ticker": "TSM",  "adr_ratio": 5,  "name": "台積電"},
    "2303": {"adr_ticker": "UMC",  "adr_ratio": 2,  "name": "聯電"},
    "3711": {"adr_ticker": "ASX",  "adr_ratio": 1,  "name": "日月光投控"},
    "8150": {"adr_ticker": "IMOS", "adr_ratio": 1,  "name": "南茂"},
    "3081": {"adr_ticker": "HIMX", "adr_ratio": 2,  "name": "奇景光電"},
    "2498": {"adr_ticker": "HMC",  "adr_ratio": 1,  "name": "宏達電"},  # approximate
    "2357": {"adr_ticker": "AUO",  "adr_ratio": 10, "name": "友達"},
    "2409": {"adr_ticker": "AUO",  "adr_ratio": 10, "name": "友達"},
}

# ── Known investment banks ────────────────────────────────────────────────────
BANKS = {
    "大摩":           ("Morgan Stanley", "🇺🇸"),
    "摩根士丹利":     ("Morgan Stanley", "🇺🇸"),
    "小摩":           ("J.P. Morgan",    "🇺🇸"),
    "摩根大通":       ("J.P. Morgan",    "🇺🇸"),
    "jpmorgan":       ("J.P. Morgan",    "🇺🇸"),
    "j.p. morgan":    ("J.P. Morgan",    "🇺🇸"),
    "高盛":           ("Goldman Sachs",  "🇺🇸"),
    "goldman sachs":  ("Goldman Sachs",  "🇺🇸"),
    "goldman":        ("Goldman Sachs",  "🇺🇸"),
    "瑞銀":           ("UBS",            "🇨🇭"),
    "ubs":            ("UBS",            "🇨🇭"),
    "美銀美林":       ("BofA",           "🇺🇸"),
    "美銀":           ("BofA",           "🇺🇸"),
    "merrill lynch":  ("BofA",           "🇺🇸"),
    "bank of america":("BofA",           "🇺🇸"),
    "花旗":           ("Citigroup",      "🇺🇸"),
    "citigroup":      ("Citigroup",      "🇺🇸"),
    "citi":           ("Citigroup",      "🇺🇸"),
    "德意志":         ("Deutsche Bank",  "🇩🇪"),
    "德銀":           ("Deutsche Bank",  "🇩🇪"),
    "deutsche bank":  ("Deutsche Bank",  "🇩🇪"),
    "野村":           ("Nomura",         "🇯🇵"),
    "nomura":         ("Nomura",         "🇯🇵"),
    "瑞穗":           ("Mizuho",         "🇯🇵"),
    "mizuho":         ("Mizuho",         "🇯🇵"),
    "巴克萊":         ("Barclays",       "🇬🇧"),
    "barclays":       ("Barclays",       "🇬🇧"),
    "里昂":           ("CLSA",           "🇫🇷"),
    "clsa":           ("CLSA",           "🇫🇷"),
    "麥格理":         ("Macquarie",      "🇦🇺"),
    "macquarie":      ("Macquarie",      "🇦🇺"),
    "法巴":           ("BNP Paribas",    "🇫🇷"),
    "bnp paribas":    ("BNP Paribas",    "🇫🇷"),
    "富瑞":           ("Jefferies",      "🇺🇸"),
    "jefferies":      ("Jefferies",      "🇺🇸"),
    "匯豐":           ("HSBC",           "🇬🇧"),
    "hsbc":           ("HSBC",           "🇬🇧"),
    "瑞信":           ("Credit Suisse",  "🇨🇭"),
    "credit suisse":  ("Credit Suisse",  "🇨🇭"),
    "凱基":           ("KGI",            "🇹🇼"),
    "元大":           ("Yuanta",         "🇹🇼"),
    "富邦":           ("Fubon",          "🇹🇼"),
    "群益":           ("Capital",        "🇹🇼"),
    "永豐金":         ("Sinopac",        "🇹🇼"),
    "兆豐":           ("Mega",           "🇹🇼"),
    "凱投":           ("KGI Securities", "🇹🇼"),
}

RATING_MAP = {
    "強烈買進":    ("強烈買進", "#00c853"),
    "買進":        ("買進",     "#4caf50"),
    "增持":        ("增持",     "#4caf50"),
    "優於大盤":    ("優於大盤", "#4caf50"),
    "outperform":  ("優於大盤", "#4caf50"),
    "overweight":  ("增持",     "#4caf50"),
    "buy":         ("買進",     "#4caf50"),
    "持有":        ("持有",     "#ff9800"),
    "中立":        ("中立",     "#ff9800"),
    "equal weight":("中立",     "#ff9800"),
    "neutral":     ("中立",     "#ff9800"),
    "hold":        ("持有",     "#ff9800"),
    "賣出":        ("賣出",     "#f44336"),
    "減持":        ("減持",     "#f44336"),
    "劣於大盤":    ("劣於大盤", "#f44336"),
    "underperform":("劣於大盤", "#f44336"),
    "underweight": ("減持",     "#f44336"),
    "sell":        ("賣出",     "#f44336"),
}

# ── Currency helpers ──────────────────────────────────────────────────────────

_TWD_MARKERS = ["nt$", "twd", "ntd", "新台幣", "台幣"]
_USD_MARKERS = ["usd", "us$", "u.s.$", "美元", "dollar"]


def _detect_currency(text: str, price: float) -> str:
    """
    Return 'TWD', 'USD', or 'unknown' based on text context around the price.

    Rules (priority order):
      1. Explicit TWD label near the number → TWD
      2. Explicit USD label near the number → USD
      3. Bare '$' with price > 400 → TWD  (Taiwan stocks rarely hit $400 USD)
      4. Bare '$' with price ≤ 400 → USD  (ADR price range)
      5. No currency symbol → unknown
    """
    t = text.lower()
    # Check for NT$/TWD/元 in text
    if any(m in t for m in _TWD_MARKERS):
        return "TWD"
    # Check for USD/美元 in text
    if any(m in t for m in _USD_MARKERS):
        return "USD"
    # Bare $ — use price magnitude as proxy
    if "$" in text:
        return "TWD" if price > 400 else "USD"
    # No currency symbol, but reasonable TWD range
    return "unknown"


def _extract_price_with_currency(text: str):
    """
    Try to extract a target price AND its currency from `text`.
    Returns (price: float, currency: str) or (None, 'unknown').
    """
    t_lower = text.lower()
    has_usd = any(m in t_lower for m in _USD_MARKERS)
    has_twd = any(m in t_lower for m in _TWD_MARKERS)

    # ── Patterns that carry an explicit TWD label ─────────────────────────────
    explicit_twd_pats = [
        r'(?:NT\$|TWD|NTD|新台幣|台幣)\s*(\d{2,5}(?:\.\d+)?)',
        r'(\d{2,5}(?:\.\d+)?)\s*(?:元|台幣|新台幣)',
    ]
    for pat in explicit_twd_pats:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1))
                if 5 <= val <= 15000:
                    return val, "TWD"
            except (ValueError, IndexError):
                continue

    # ── Ambiguous patterns (context-aware currency assignment) ────────────────
    # "目標價 180", "調升至 200", etc. — could be TWD or USD depending on surrounding text
    ambiguous_pats = [
        r'目標(?:股)?價\D{0,15}?(\d{2,5}(?:\.\d+)?)',
        r'(?:上調|下調|調升|調降|調整|維持)\D{0,8}?(\d{2,5}(?:\.\d+)?)',
        r'(?:至|調至|升至|降至)\s*(\d{2,5}(?:\.\d+)?)',
    ]
    for pat in ambiguous_pats:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1))
                if 5 <= val <= 15000:
                    # If text explicitly mentions USD and no TWD marker → USD
                    if has_usd and not has_twd:
                        return val, "USD"
                    return val, "TWD"
            except (ValueError, IndexError):
                continue

    # ── Explicit USD-labeled patterns ─────────────────────────────────────────
    usd_pats = [
        r'(?:USD|US\$|U\.S\.\$)\s*(\d{1,4}(?:\.\d+)?)',
        r'(\d{1,4}(?:\.\d+)?)\s*(?:美元|USD)',
    ]
    for pat in usd_pats:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1))
                if 1 <= val <= 3000:
                    return val, "USD"
            except (ValueError, IndexError):
                continue

    # ── English target price phrases ──────────────────────────────────────────
    en_pats = [
        r'(?:price target|PT|target price)\D{0,10}?(\d{2,5}(?:\.\d+)?)',
        r'(?:raises?|lowers?|cuts?|maintains?|trims?)\D{0,10}?(?:to|at)\D{0,5}?(\d{2,5}(?:\.\d+)?)',
        r'(?:to|at)\s*\$\s*(\d{1,4}(?:\.\d+)?)',
    ]
    for pat in en_pats:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1))
                if val <= 0:
                    continue
                cur = _detect_currency(text, val)
                if 1 <= val <= 15000:
                    return val, cur
            except (ValueError, IndexError):
                continue

    # ── Bare $ fallback ───────────────────────────────────────────────────────
    m = re.search(r'\$\s*(\d{1,5}(?:\.\d+)?)', text)
    if m:
        try:
            val = float(m.group(1))
            if 1 <= val <= 15000:
                return val, _detect_currency(text, val)
        except (ValueError, IndexError):
            pass

    return None, "unknown"


# ── USD→TWD conversion (live rate via yfinance cache) ─────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def _get_usd_twd_rate() -> float:
    """Fetch live USD/TWD exchange rate. Falls back to 32.0 on failure."""
    try:
        import yfinance as yf
        ticker = yf.Ticker("USDTWD=X")
        hist = ticker.history(period="5d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return 32.0  # reasonable fallback


def _adr_conversion_note(stock_id: str, usd_price: float) -> str:
    """Return a human-readable ADR → TWD conversion note, or empty string."""
    adr = ADR_STOCKS.get(stock_id)
    if not adr:
        return ""
    rate = _get_usd_twd_rate()
    twd_equiv = usd_price * rate / adr["adr_ratio"]
    return (
        f"（{adr['adr_ticker']} ADR，1 ADR≈{adr['adr_ratio']} 股，"
        f"換算每股約 TWD {twd_equiv:.0f}，匯率 {rate:.1f}）"
    )


# ── Bank & rating helpers ──────────────────────────────────────────────────────

def _detect_bank(text: str):
    t = text.lower()
    for key, val in BANKS.items():
        if key in t:
            return val  # (display_name, flag)
    return None, None


def _detect_rating(text: str):
    t = text.lower()
    for key, val in RATING_MAP.items():
        if key in t:
            return val  # (display, color)
    return None, "#aaa"


def _detect_direction(text: str) -> str:
    up = ["上調", "調升", "提高", "上修", "raises", "hikes", "upgrades", "lifts"]
    dn = ["下調", "調降", "降低", "下修", "cuts", "lowers", "reduces", "trims", "downgrades"]
    if any(w in text for w in up):
        return "↑"
    if any(w in text for w in dn):
        return "↓"
    return "→"


# ── News fetching ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def _google_news_raw(query: str) -> list:
    url = (
        "https://news.google.com/rss/search"
        f"?q={requests.utils.quote(query)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    )
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.content)
        return [
            {
                "title":    item.findtext("title", ""),
                "link":     item.findtext("link", ""),
                "pub_date": item.findtext("pubDate", ""),
                "source":   item.findtext("source", ""),
            }
            for item in root.findall(".//item")[:20]
        ]
    except Exception:
        return []


# ── Article excerpt (lazy, cached) ────────────────────────────────────────────

@st.cache_data(ttl=7200, show_spinner=False)
def _fetch_excerpt_for_analyst(url: str) -> str:
    """
    Try to fetch a short article excerpt for analyst target price pages.
    Returns '' on failure — never raises.
    """
    if not url or url in ("#", ""):
        return ""
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower()
        # Skip known paywall / blocked sites
        for blocked in ("bloomberg.com", "wsj.com", "ft.com", "barrons.com"):
            if blocked in domain:
                return ""

        resp = requests.get(
            url, timeout=8, allow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
            }
        )
        if resp.status_code != 200:
            return ""
        ct = resp.headers.get("Content-Type", "")
        if "html" not in ct:
            return ""

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.content, "lxml")

        for tag in soup(["script", "style", "nav", "header", "footer",
                          "aside", "figure", "figcaption", "form",
                          "button", "iframe", "noscript"]):
            tag.decompose()

        content_el = (
            soup.find("article") or
            soup.select_one(".news__article__content") or
            soup.select_one(".caas-body") or
            soup.select_one(".article-content") or
            soup.find("main")
        )
        paras = content_el.find_all("p") if content_el else soup.find_all("p")
        texts = [p.get_text(" ", strip=True) for p in paras
                 if len(p.get_text(strip=True)) >= 20]

        raw = " ".join(texts[:8])
        raw = re.sub(r"\s+", " ", raw).strip()
        # Strip noisy suffixes
        raw = re.sub(r"(廣告|Advertisement|訂閱|Subscribe).*", "", raw).strip()
        if len(raw) < 40:
            return ""
        return raw[:320] + ("…" if len(raw) > 320 else "")
    except Exception:
        return ""


# ── Main public function ───────────────────────────────────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def get_analyst_targets(stock_id: str, company_name: str, current_price: float) -> list:
    """
    Scrape Google News for investment-bank target price updates for this stock.

    Two-pass price extraction:
      Pass 1: regex on headline
      Pass 2: if headline gives no TWD price, fetch article excerpt and retry

    Returns list of dicts, deduplicated by bank (newest / TWD price preferred).
    """
    # Normalize stock_id to bare 4-digit code (strip .TW / .TWO suffixes)
    bare_id = re.sub(r'\.(TW|TWO)$', '', stock_id, flags=re.IGNORECASE)

    queries = [
        f"{company_name} 目標價",
        f"{bare_id} {company_name} 法人 評等",
        f"{company_name} target price analyst",
    ]
    seen_titles = set()
    raw = []
    for q in queries:
        for item in _google_news_raw(q):
            if item["title"] not in seen_titles:
                seen_titles.add(item["title"])
                raw.append(item)

    is_adr_stock = bare_id in ADR_STOCKS

    # Relevance tokens: bare stock ID + first 3 chars of company name (catches abbreviations)
    name_short = company_name[:3] if len(company_name) >= 3 else company_name
    relevance_tokens = {bare_id, company_name, name_short}
    if len(company_name) > 3:
        relevance_tokens.add(company_name[:4])

    parsed = []
    for item in raw:
        title = item["title"]

        # Skip articles not about this stock — bank name alone is not enough
        title_lower = title.lower()
        if not any(tok.lower() in title_lower for tok in relevance_tokens if tok):
            continue

        bank, flag = _detect_bank(title)
        if not bank:
            continue

        # ── Pass 1: headline ──────────────────────────────────────────────────
        price, currency = _extract_price_with_currency(title)

        # ── Pass 2: article excerpt if headline didn't give a TWD price ──────
        excerpt = ""
        if (price is None or currency != "TWD") and item.get("link"):
            excerpt = _fetch_excerpt_for_analyst(item["link"])
            if excerpt:
                ex_price, ex_cur = _extract_price_with_currency(excerpt)
                if ex_price is not None and (price is None or ex_cur == "TWD"):
                    price, currency = ex_price, ex_cur

        if price is None:
            continue

        rating, r_color = _detect_rating(title)
        direction = _detect_direction(title)

        # ── Build display fields based on currency ────────────────────────────
        if currency == "USD":
            upside = None
            adr_note = ""
            if is_adr_stock:
                adr_note = _adr_conversion_note(bare_id, price)
            price_note = f"USD ${price:.0f}" + (" ▶ ADR" if is_adr_stock else "")
            currency_label = "USD"
        elif currency == "TWD":
            upside = (price - current_price) / current_price * 100 if current_price else None
            price_note = f"TWD {price:.0f}"
            adr_note = ""
            currency_label = "TWD"
        else:
            # unknown — show as-is, flag it
            upside = None
            price_note = f"{price:.0f}（幣別不明）"
            adr_note = ""
            currency_label = "unknown"

        parsed.append({
            "bank":           bank,
            "flag":           flag,
            "target":         price,
            "currency":       currency_label,
            "price_note":     price_note,
            "adr_note":       adr_note,
            "is_usd":         currency == "USD",
            "upside_pct":     upside,
            "direction":      direction,
            "rating":         rating,
            "rating_color":   r_color,
            "title":          title,
            "excerpt":        excerpt,
            "link":           item["link"],
            "pub_date":       item["pub_date"],
            "source":         item["source"],
        })

    # ── Deduplicate by bank — prefer TWD entry; if tie, prefer non-None upside ─
    bank_best = {}
    for p in parsed:
        b = p["bank"]
        existing = bank_best.get(b)
        if existing is None:
            bank_best[b] = p
        elif existing["currency"] != "TWD" and p["currency"] == "TWD":
            bank_best[b] = p  # upgrade to TWD
        elif existing["upside_pct"] is None and p["upside_pct"] is not None:
            bank_best[b] = p

    result = sorted(bank_best.values(), key=lambda x: (x["upside_pct"] or 0), reverse=True)
    return result
