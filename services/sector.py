"""
產業族群（題材）分析 —— 找「強勢族群裡還沒跟上的落後股」。

為什麼要有這個：
  原本的「潛力潛伏」是**個股層級**的低基期，回測顯示它在多頭失效（買還沒漲的會輸）。
  但「題材股」的真正邏輯不同：**族群動能 + 個股落後**。
  同族群基本面連動（同樣的下游需求、同樣的漲價循環），龍頭先漲、落後股補漲，
  是台股常見的資金輪動型態。這是「族群層面的動能」，不是「個股層面的抄底」——
  兩者可能一好一壞，必須分別驗證，不能拿個股低基期的結論套用。

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
      / median_long（族群長線分中位數）/ members（成員 rows）
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
        longs = [(m.get("horizon") or {}).get("long", {}).get("score")
                 for m in members]
        longs = [x for x in longs if x is not None]
        if not r60:
            continue
        out.append({
            "name": name,
            "n": len(members),
            "mom": stat.median(r60),
            "mom_20": stat.median(r20) if r20 else None,
            "breadth": sum(1 for x in r60 if x > 0) / len(r60) * 100,
            "median_long": stat.median(longs) if longs else None,
            "members": members,
        })
    out.sort(key=lambda s: -s["mom"])
    return out


def find_laggards(sectors, top_sectors=5, max_pos=60, min_long=55):
    """
    在最強勢的幾個族群裡，挑出「自己還沒跟上」的個股（補漲候選）。

    條件：
      · 屬於動能前 top_sectors 名的族群（族群要熱）
      · 個股 52 週位階 <= max_pos（自己還在低檔 = 還沒跟上）
      · 長線結構分 >= min_long（排除純粹爛股，落後要是「還沒動」而非「壞掉」）

    回傳 list of dict，含 gap（族群動能 − 個股動能，越大代表落後越多）。
    """
    picks = []
    for s in sectors[:top_sectors]:
        for m in s["members"]:
            p = m.get("potential") or {}
            pos = p.get("position_pct")
            r60 = p.get("r60")
            lg = (m.get("horizon") or {}).get("long", {}).get("score", 0)
            if pos is None or r60 is None:
                continue
            if pos <= max_pos and lg >= min_long:
                picks.append({
                    **m,
                    "sector": s["name"],
                    "sector_mom": s["mom"],
                    "sector_breadth": s["breadth"],
                    "own_mom": r60,
                    "gap": s["mom"] - r60,
                })
    picks.sort(key=lambda x: -x["gap"])
    return picks
