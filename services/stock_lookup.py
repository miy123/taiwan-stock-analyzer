"""
股票查詢：支援用「代碼」或「中文名稱」查詢。

名稱→代碼對照表來自證交所（上市）與櫃買中心（上櫃）的公開基本資料 OpenAPI，
合併專案內建的 POPULAR_STOCKS / OTC_STOCKS（內建名稱優先顯示）。網路失敗時退回內建清單。
"""

import re
import requests
import streamlit as st

from services.stock_data import POPULAR_STOCKS, OTC_STOCKS

_TWSE_LIST = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"     # 上市
_TPEX_LIST = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"  # 上櫃
_HEADERS = {"User-Agent": "Mozilla/5.0"}

_CODE_RE = re.compile(r"^[0-9]{3,6}[A-Za-z]?$")


@st.cache_data(ttl=86400, show_spinner=False)
def _load_company_map() -> dict:
    """Return {code: {'abbrev': str, 'full': str}} merging TWSE + TPEX + built-in."""
    out = {}

    def _ingest(rows, code_key, full_key, abbrev_key):
        for d in rows:
            code = str(d.get(code_key, "")).strip()
            if not code:
                continue
            full = str(d.get(full_key, "")).strip()
            abbrev = str(d.get(abbrev_key, "")).strip() or full
            out[code] = {"abbrev": abbrev, "full": full}

    try:
        r = requests.get(_TWSE_LIST, timeout=20, headers=_HEADERS)
        _ingest(r.json(), "公司代號", "公司名稱", "公司簡稱")
    except Exception:
        pass
    try:
        r = requests.get(_TPEX_LIST, timeout=20, headers=_HEADERS)
        _ingest(r.json(), "SecuritiesCompanyCode", "CompanyName", "CompanyAbbreviation")
    except Exception:
        pass

    # Built-in curated names win for display (guaranteed even if APIs are down)
    for code, name in {**POPULAR_STOCKS, **OTC_STOCKS}.items():
        cur = out.get(code, {})
        out[code] = {"abbrev": name, "full": cur.get("full", name)}

    return out


def display_name(stock_id: str) -> str:
    m = _load_company_map().get(stock_id)
    return m["abbrev"] if m else stock_id


def resolve_company_name(stock_id: str, info: dict = None, limit_up_info: dict = None) -> str:
    """
    Preferred display name, Chinese first.

    yfinance's shortName/longName are ENGLISH for Taiwan stocks
    ("POU CHEN", "RADIANT OPTO-ELECTRONICS"), so they must be the LAST resort —
    the TWSE/TPEX company lists give proper Chinese 簡稱 (寶成, 瑞儀) for every
    listed company, and POPULAR_STOCKS covers the curated ones.
    """
    lu = (limit_up_info or {}).get("name")
    if lu:
        return lu
    cn = _load_company_map().get(stock_id, {}).get("abbrev")
    if cn and cn != stock_id:
        return cn
    info = info or {}
    return info.get("shortName") or info.get("longName") or stock_id


def resolve_query(query: str) -> dict:
    """
    Resolve a code or Chinese name to a stock.

    Returns {'stock_id': str|None, 'name': str, 'matched': bool,
             'is_code': bool, 'suggestions': [(code, name), ...]}.
    """
    q = (query or "").strip()
    empty = {"stock_id": None, "name": "", "matched": False, "is_code": False, "suggestions": []}
    if not q:
        return empty

    cmap = _load_company_map()

    # Pure code (e.g. 2330, 00671R) — use directly
    if _CODE_RE.match(q):
        code = q.upper() if q[-1].isalpha() else q
        return {
            "stock_id": code,
            "name": cmap.get(code, {}).get("abbrev", code),
            "matched": True, "is_code": True, "suggestions": [],
        }

    # Name search — rank each candidate; lower rank = better match:
    #   0 exact abbrev · 1 exact full · 2 abbrev prefix · 3 abbrev contains · 4 full contains
    # tie-break by shorter abbrev (more specific) then code.
    scored = []
    for code, names in cmap.items():
        ab, full = names.get("abbrev", ""), names.get("full", "")
        if q == ab:
            rank = 0
        elif q == full:
            rank = 1
        elif ab and ab.startswith(q):
            rank = 2
        elif ab and q in ab:
            rank = 3
        elif full and q in full:
            rank = 4
        else:
            continue
        scored.append((rank, len(ab or full), code, ab or full))

    if not scored:
        return empty

    scored.sort(key=lambda x: (x[0], x[1], x[2]))
    # dedupe by code, preserving best-first order
    seen, ordered = set(), []
    for rank, _, code, nm in scored:
        if code not in seen:
            seen.add(code)
            ordered.append((code, nm))

    best_code, best_nm = ordered[0]
    return {"stock_id": best_code, "name": best_nm, "matched": True, "is_code": False,
            "suggestions": ordered[:6]}
