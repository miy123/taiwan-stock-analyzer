"""
產業族群（題材）分析 —— 以官方產業別分群，算各族群的動能排名。

為什麼要有這個：
  動能在**族群層級**也有效：以官方產業別分群、取 60 日報酬中位數排名，
  「前 5 強族群 + 個股趨勢分最高」是一個可用的選股濾網（策略 `sectorhot`）。

⚠️ 但兩件事已被實測否定，不要重做：
  1. **補漲無效**：「強勢族群裡的落後股」超額 −0.25%、贏基準率僅 39.7%。
     均值回歸在族群與個股兩個層級都無效。原本的 `find_laggards()` 就是做這件事，
     已無呼叫端且與結論相反，已刪除。
  2. **族群濾網本身也輸給不加濾網**：同一次回測裡 `sectorhot` 輸給純趨勢分
     （見 strategy_comparison.json）。這是本專案第三次量到「多加一層過濾更差」。

資料來源：證交所／櫃買中心公司基本資料的「產業別」代碼（官方分類，非自行猜測）。
"""

import statistics as stat

import requests
import streamlit as st

_TWSE_LIST = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
_TPEX_LIST = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
_HEADERS = {"User-Agent": "Mozilla/5.0"}

# 證交所「公開發行公司行業別」代碼 → 名稱。
# 已用已知個股交叉驗證：2330/2454→24半導體、3008→26光電、2603→15航運、
# 2882→17金融保險、1101→01水泥、2308→28電子零組件。
INDUSTRY_NAMES = {
    "01": "水泥", "02": "食品", "03": "塑膠", "04": "紡織纖維",
    "05": "電機機械", "06": "電器電纜", "07": "化學生技醫療", "08": "玻璃陶瓷",
    "09": "造紙", "10": "鋼鐵", "11": "橡膠", "12": "汽車",
    "13": "電子工業", "14": "建材營造", "15": "航運", "16": "觀光餐旅",
    "17": "金融保險", "18": "貿易百貨", "19": "綜合", "20": "其他",
    "21": "化學工業", "22": "生技醫療", "23": "油電燃氣", "24": "半導體",
    "25": "電腦及週邊", "26": "光電", "27": "通信網路", "28": "電子零組件",
    "29": "電子通路", "30": "資訊服務", "31": "其他電子", "32": "文化創意",
    "33": "農業科技", "34": "電子商務", "35": "數位雲端", "36": "運動休閒",
    "37": "居家生活", "80": "管理股票",
}


def industry_name(code) -> str:
    c = str(code or "").strip().zfill(2)
    return INDUSTRY_NAMES.get(c, f"其他({c})")


@st.cache_data(ttl=86400, show_spinner=False)
def get_industry_map() -> dict:
    """{stock_id: {'code': '24', 'name': '半導體'}}（上市 + 上櫃）。"""
    out = {}
    try:
        for d in requests.get(_TWSE_LIST, timeout=30, headers=_HEADERS).json():
            code = str(d.get("公司代號", "")).strip()
            if code:
                ind = str(d.get("產業別", "")).strip()
                out[code] = {"code": ind, "name": industry_name(ind)}
    except Exception:
        pass
    try:
        for d in requests.get(_TPEX_LIST, timeout=30, headers=_HEADERS).json():
            code = str(d.get("SecuritiesCompanyCode", "")).strip()
            if code:
                ind = str(d.get("SecuritiesIndustryCode", "")).strip()
                out[code] = {"code": ind, "name": industry_name(ind)}
    except Exception:
        pass
    return out


# 同業本益比至少要幾檔才顯示。桶子太小，中位數就是一兩檔說了算——
# 實測玻璃陶瓷全市場只有 5 檔，中位數 49.9 完全由極端值決定。
PEER_MIN_N = 10


def _median_by_industry(value_by_code, min_n=None) -> dict:
    """
    {產業: {median, n}} —— 產業分組取中位數的**唯一實作**。

    一律用中位數而非平均：單一極端值（聯發科 73 倍本益比）就能把平均拉歪。
    檔數不足 `min_n` 的產業直接不回傳，寧可不顯示也不要給一個由一兩檔
    決定的「同業水準」。
    """
    min_n = PEER_MIN_N if min_n is None else min_n
    ind = get_industry_map()
    buckets = {}
    for code, v in value_by_code.items():
        meta = ind.get(code)
        if not meta or meta["code"] in ("", "80") or v is None:   # 排除管理股票
            continue
        buckets.setdefault(meta["name"], []).append(v)
    return {n: {"median": stat.median(x), "n": len(x)}
            for n, x in buckets.items() if len(x) >= min_n}


def _peer_of(stock_id, value, table, mode) -> dict:
    """
    單檔對照的共用骨架。取不到分類、或同業檔數不足就回 {}。

    mode 決定「相對」怎麼表示，兩種單位不能混：
      "rel_pct" —— 相對**比例**（%）。用在本益比：27.6 vs 中位 27.0 ＝ +2%。
      "rel_pp"  —— 相對**百分點**。用在毛利率等本身就是比率的指標：
                   低毛利產業 8.3% → 12% 是 +45%，但實質只差 3.7 個百分點，
                   用比例會把低基期產業的差距誇大。
    """
    if not stock_id:
        return {}
    meta = get_industry_map().get(str(stock_id))
    if not meta:
        return {}
    row = table.get(meta["name"])
    if not row:
        return {}
    out = {"industry": meta["name"], "median": row["median"], "n": row["n"]}
    if value is not None:
        med = row["median"]
        if mode == "rel_pct":
            out["rel_pct"] = (value / med - 1) * 100 if med else None
        else:
            out["rel_pp"] = (value - med) * 100
    return out


@st.cache_data(ttl=43200, show_spinner=False)   # 12h —— 跟著財報走
def industry_gross_margin() -> dict:
    """
    各產業的毛利率中位數 —— {產業: {median(分數), n}}。

    為什麼一定要相對同業：毛利率**極度吃產業**。實測產業中位數
    生技醫療 41.0% vs 電子通路 8.3%，差 33 個百分點；個股層級
    台積電 67.0% vs 鴻海 6.2%。直接套全市場級距等於系統性地給
    半導體／生技加分、給通路組裝扣分——正是本專案量過四次的
    「多加一層濾網反而更差」。
    """
    from services.financials import get_bulk_fundamentals
    return _median_by_industry(
        {c: v.get("gross_margin") for c, v in get_bulk_fundamentals().items()})


def peer_gross_margin(stock_id, gross_margin=None) -> dict:
    """
    這一檔的毛利率相對同業 —— {"industry", "median", "n", "rel_pp"}。

    `rel_pp` 的單位是**百分點**（percentage point）：毛利率 12% 而同業中位
    8.3% ＝ +3.7pp。用百分點而不是相對比例，是因為低毛利產業的比例變化會
    被放大（8.3% → 12% 是 +45%，但實質差距只有 3.7 個百分點）。

    金融業沒有毛利率，回 {}。
    """
    return _peer_of(stock_id, gross_margin, industry_gross_margin(), "rel_pp")


@st.cache_data(ttl=10800, show_spinner=False)
def industry_pe() -> dict:
    """
    各產業的本益比中位數 —— {產業名稱: {"median": float, "n": int}}。

    資料**全部來自已經在抓的兩份快照**（證交所／櫃買的官方本益比 +
    公開資訊觀測站的官方產業別），不新增任何請求。
    實測：1494 檔有本益比且可分類，落在 33 個產業。

    ⚠️ 一律用**中位數**，不要用平均：聯發科 73 倍一檔就能把半導體拉歪
    （160 檔的中位數是 26.6）。

    ⚠️ **只能顯示，不准進任何分數。** 本專案量過純低本益比持有 3 個月
    超額 −4.57%、t=−3.96，2026-09-15 才把估值移出體質分。「相對同業」這個
    版本從未回測過，讓它進計分等於把剛修掉的錯再犯一次。
    """
    from services.universe import get_full_market_snapshot
    # >200 倍多半是獲利趨近於零的極端值，排掉再取中位數
    return _median_by_industry({
        c: (v.get("pe") if (v.get("pe") is not None and 0 < v["pe"] <= 200) else None)
        for c, v in get_full_market_snapshot().items()})


def peer_pe(stock_id, pe=None) -> dict:
    """
    這一檔的同業對照 —— {"industry", "median", "n", "rel_pct"}，取不到就回 {}。

    `pe` 由呼叫端傳入（它手上那個已經是官方來源的本益比），
    這裡不自己再查一次，免得同一檔在不同地方用到兩個本益比。

    同業檔數不足 PEER_MIN_N 時**寧可不顯示**，不要給一個由一兩檔決定的
    「同業水準」讓使用者誤以為有代表性。
    """
    return _peer_of(stock_id, pe if (pe is not None and pe > 0) else None,
                    industry_pe(), "rel_pct")


def analyse_sectors(rows, min_members=4):
    """
    以掃描結果（rows，須含 stock_id 與 potential.r60 等）計算族群動能。

    回傳 list，每個族群一筆：
      name / n / mom（族群中位數 60 日報酬）/ mom_20 / breadth（上漲比率）
      / median_trend（族群趨勢結構分中位數）/ members（成員 rows）
    依 mom 由高到低排序 —— 越前面代表族群越強勢。
    """
    ind = get_industry_map()
    buckets = {}
    for r in rows:
        meta = ind.get(r.get("stock_id"))
        if not meta or meta["code"] in ("", "80"):     # 排除管理股票
            continue
        buckets.setdefault(meta["name"], []).append(r)

    out = []
    for name, members in buckets.items():
        if len(members) < min_members:
            continue
        r60 = [(m.get("potential") or {}).get("r60") for m in members]
        r60 = [x for x in r60 if x is not None]
        r20 = [(m.get("potential") or {}).get("r20") for m in members]
        r20 = [x for x in r20 if x is not None]
        # ⚠️ 用趨勢結構分，不要用 `horizon["long"]`（舊的離散長線分）：
        #    深度分析過的 row 已經沒有那個鍵，會被整批濾掉，
        #    於是這個「中位數」實際上只算了沒被深度分析的那些股票。
        trends = [m.get("trend_score") for m in members]
        trends = [x for x in trends if x is not None]
        if not r60:
            continue
        out.append({
            "name": name,
            "n": len(members),
            "mom": stat.median(r60),
            "mom_20": stat.median(r20) if r20 else None,
            "breadth": sum(1 for x in r60 if x > 0) / len(r60) * 100,
            "median_trend": stat.median(trends) if trends else None,
            "members": members,
        })
    out.sort(key=lambda s: -s["mom"])
    return out
