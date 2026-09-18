"""
全市場財報（批次）—— 讓「體質」不再只有幾十檔算得出來。

## 為什麼要有這個檔

體質分（`fundamental.calculate_fundamental_score`）要的是 ROE／營收成長／淨利率／
負債比，而這些**逐檔用 yfinance 抓要 2.5 秒**，全市場 1900 檔等於要跑一個多小時。
所以掃描原本只對初篩最前的數十檔補齊財報，其餘一律標「無財報資料」——
使用者看到的是「一堆卡片都沒有體質可看」。

公開資訊觀測站其實有**批次**的財報 openapi，證交所與櫃買各一組，
一次請求就涵蓋全市場，這裡把它們接起來：

  · t187ap06_*（綜合損益表）→ 營業收入、淨利 → **淨利率**
  · t187ap07_*（資產負債表）→ 權益、負債總計 → **ROE**、**負債權益比**
  · t187ap05_*（月營收）    → 累計營收年增   → **營收成長**

本益比／淨值比／殖利率本來就在 `universe.get_full_market_snapshot()` 裡，
湊齊後體質分的六個因子就全部有了。

## 產業別表格不只一張

一般業是 `_ci`，金融相關另有 `_basi`(銀行) `_bd`(證券) `_ins`(保險) `_mim` `_fh`(金控)。
欄位名稱也不一樣——金控沒有「營業收入」而是「淨收益」，銀行是
「利息淨收益＋利息以外淨損益」。因此下面用**候選欄位名清單**去解析，
解析不到就讓那一檔維持「無財報」，而不是硬湊一個數字出來。

## ⚠️ 與 yfinance 的差異

官方表是**當年度累計**（年度/季別欄位標明到第幾季），yfinance 是 TTM。
兩者窗口不同，數字不會完全一樣，也可能連正負號都不同
（台泥 2026H1 ROE +2.8%，yfinance TTM −3.1%）。官方版比較新、而且是
point-in-time 正確的。ROE 會依季別年化（×4/季別）。
"""

import time

import requests
import streamlit as st

_HEADERS = {"User-Agent": "Mozilla/5.0"}
_TIMEOUT = 40

# 證交所：一般業 + 金融各業別；櫃買：一般業 + 銀行
_TWSE = "https://openapi.twse.com.tw/v1/opendata"
_TPEX = "https://www.tpex.org.tw/openapi/v1"
_GENERAL = ("ci",)
_FINANCIAL = ("basi", "bd", "ins", "mim", "fh")     # 銀行/證券/保險/金控
_INCOME_URLS = ([(f"{_TWSE}/t187ap06_L_{s}", s) for s in _GENERAL + _FINANCIAL]
                + [(f"{_TPEX}/mopsfin_t187ap06_O_{s}", s) for s in ("ci", "basi")])
_BALANCE_URLS = ([(f"{_TWSE}/t187ap07_L_{s}", s) for s in _GENERAL + _FINANCIAL]
                 + [(f"{_TPEX}/mopsfin_t187ap07_O_{s}", s) for s in ("ci", "basi")])
_REVENUE_URLS = [(f"{_TWSE}/t187ap05_L", "ci"), (f"{_TPEX}/mopsfin_t187ap05_O", "ci")]

# 同一個概念在不同業別／不同交易所的表上叫不同名字，依序嘗試。
# ⚠️ 櫃買的表用英文鍵（SecuritiesCompanyCode / Year / Season），
#    證交所用中文鍵（公司代號／年度／季別），兩邊都要認。
_CODE_FIELDS = ("公司代號", "SecuritiesCompanyCode")
_YEAR_FIELDS = ("年度", "Year")
_SEASON_FIELDS = ("季別", "Season")

# ⚠️ 金控表裡有一個叫「淨收益」的欄位，但它**不是**營收等價物
#    （國泰金 淨收益 408 萬 vs 利息淨收益 1,568 億），拿它當分母會算出
#    淨利率 1866% 這種數字。金融業的收入要用「利息淨收益＋利息以外淨收益」。
_BANK_REVENUE_PARTS = (("利息淨收益", "利息以外淨收益"),
                       ("利息淨收益", "利息以外淨損益"))
_NET_INCOME_FIELDS = ("淨利（淨損）歸屬於母公司業主", "淨利（損）歸屬於母公司業主",
                      "本期淨利（淨損）", "本期稅後淨利（淨損）")
# 金控的資產負債表用「總額」而非「總計」，且沒有「合計」二字
_EQUITY_FIELDS = ("歸屬於母公司業主之權益合計", "歸屬於母公司業主之權益", "權益總計", "權益總額")
_LIAB_FIELDS = ("負債總計", "負債總額")
_COST_FIELDS = ("營業成本",)
# 「每股參考淨值」＝ 歸屬母公司權益 ÷ 流通股數，官方已經算好、且**已調整稀釋**。
# 用它而不是權益總額：權益總額大只代表公司大，而現金增資也會讓它變大——
# 那是稀釋不是變強（財務文獻裡「資產成長」本身是負向因子）。
_BVPS_FIELDS = ("每股參考淨值",)
_TOTAL_EQUITY_FIELDS = ("權益總計", "權益總額",
                        "歸屬於母公司業主之權益合計", "歸屬於母公司業主之權益")


def _revenue_of(row):
    """營收等價物。一般業是「營業收入」；金融業要把利息與非利息收入加起來。"""
    v = _pick(row, ("營業收入",))
    if v:
        return v
    for a, c in _BANK_REVENUE_PARTS:
        va, vc = _num(row.get(a)), _num(row.get(c))
        if va is not None and vc is not None:
            return va + vc
    return _pick(row, ("收益合計",))


def _num(v):
    try:
        x = float(str(v).replace(",", "").strip())
        return x
    except (TypeError, ValueError):
        return None


def _pick(row, fields):
    for f in fields:
        if f in row:
            v = _num(row[f])
            if v is not None:
                return v
    return None


# 單張表失敗時的重試次數。
# ⚠️ 為什麼需要：`_fetch_all` 把失敗吞掉，而 `get_bulk_fundamentals` 有 12 小時
#    快取——櫃買端點只要瞬斷一次，**半套結果就會被快取一整天**，
#    全部上櫃股的體質分默默消失而畫面上看不出任何異常。
#    實測就撞到過一次：每股淨值 1083/1967（只剩證交所），重跑就變 1967/1967。
_RETRIES = 3
_RETRY_SLEEP = 1.5


def _fetch_all(urls) -> dict:
    """把多張表合併成 {公司代號: row}（row 會多帶一個 `_kind` 標明業別表）。
    單張表重試 `_RETRIES` 次仍失敗才跳過，不讓整批失敗。"""
    out = {}
    for u, kind in urls:
        rows = None
        for attempt in range(_RETRIES):
            try:
                rows = requests.get(u, timeout=_TIMEOUT, headers=_HEADERS).json()
                break
            except Exception:
                if attempt < _RETRIES - 1:
                    time.sleep(_RETRY_SLEEP * (attempt + 1))
        if not isinstance(rows, list):
            continue
        for d in rows:
            d["_kind"] = kind
        for d in rows:
            code = ""
            for k in _CODE_FIELDS:
                if d.get(k):
                    code = str(d[k]).strip()
                    break
            if code:
                out[code] = d
    return out


@st.cache_data(ttl=43200, show_spinner=False)   # 12h —— 財報一季才更新一次
def get_bulk_fundamentals() -> dict:
    """
    全市場財報指標。回傳 {code: {roe, profit_margin, gross_margin, revenue_growth,
    debt_to_equity, bvps, period}}，比率皆為 yfinance 慣用的**分數**（0.15 = 15%），
    好讓 `calculate_fundamental_score()` 不必分辨資料來源。

    `gross_margin`（毛利率）＝（營業收入 − 營業成本）÷ 營業收入。
    它在損益表的**上半部**，不像淨利率會被業外一次性損益汙染，是比較乾淨的
    品質訊號。⚠️ 但它**極度吃產業**（實測產業中位數：生技醫療 41% vs
    電子通路 8.3%，差 33 個百分點；台積電 67% vs 鴻海 6.2%），
    所以計分時**一律相對同業**，不要直接套全市場級距——那會變成系統性地
    給半導體/生技加分、給通路組裝扣分，正是本專案量過四次的
    「多加一層濾網反而更差」。相對同業的中位數見 `sector.peer_gross_margin()`。

    金融業沒有「營業成本」這個概念，`gross_margin` 一律 None。
    """
    inc = _fetch_all(_INCOME_URLS)
    bal = _fetch_all(_BALANCE_URLS)
    rev = _fetch_all(_REVENUE_URLS)
    out = {}

    for code, i in inc.items():
        b = bal.get(code) or {}
        q = 0
        for k in _SEASON_FIELDS:
            if i.get(k):
                try:
                    q = int(str(i[k]))
                except ValueError:
                    q = 0
                break
        revenue = _revenue_of(i)
        ni = _pick(i, _NET_INCOME_FIELDS)
        eq = _pick(b, _EQUITY_FIELDS)
        liab = _pick(b, _LIAB_FIELDS)
        teq = _pick(b, _TOTAL_EQUITY_FIELDS)
        cost = _pick(i, _COST_FIELDS)

        rec = {}
        if revenue and ni is not None:
            rec["profit_margin"] = ni / revenue
        # 毛利率：金融業沒有營業成本的概念，不算（與負債權益比同樣的道理）
        if revenue and cost is not None and i.get("_kind") not in _FINANCIAL:
            rec["gross_margin"] = (revenue - cost) / revenue
        bvps = _pick(b, _BVPS_FIELDS)
        if bvps is not None:
            rec["bvps"] = bvps
        if eq and ni is not None and q:
            # 累計數年化：第 2 季的累計淨利 ×2 才是年度水準
            rec["roe"] = (ni * (4.0 / q)) / eq
        # ⚠️ 金融業不計負債權益比：銀行的存款就是負債，D/E 本來就是 1000 以上
        #    （兆豐金 1200、國泰金 1224），而評分的分級是以一般產業校準的
        #    （p50≈30、p75≈81），>250 扣 12 分。照算會把每一家金融股都打成
        #    「財務風險大」，那不是體質差，是business model 不同。
        if liab is not None and teq and i.get("_kind") not in _FINANCIAL:
            # yfinance 的 debtToEquity 是百分比數字（30 = 30%），保持一致
            rec["debt_to_equity"] = liab / teq * 100
        g = _num((rev.get(code) or {}).get("累計營業收入-前期比較增減(%)"))
        if g is not None:
            rec["revenue_growth"] = g / 100
        if rec:
            rec["is_financial"] = i.get("_kind") in _FINANCIAL
            yr = next((i[k] for k in _YEAR_FIELDS if i.get(k)), "")
            rec["period"] = f"{yr}Q{q}" if q else str(yr)
            out[code] = rec
    return out
