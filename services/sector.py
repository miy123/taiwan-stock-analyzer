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
