"""
選股策略的**單一定義表** —— 一個策略的所有面向都寫在同一筆資料裡。

為什麼要有這張表：
  策略邏輯原本散在 app.py 的 12 個地方（選項清單、說明文字、篩選/排序的 if-elif、
  初篩鍵、圖表指標、圖表顏色、實證對照、是否吃週期選單…）。新增一個策略要記得
  12 處都改，實際上已經出過兩次事：
    · 加 `bestproven` 時漏掉初篩對照表 → KeyError 整頁掛掉
    · 加多個策略後，「週期選單」對 7 個策略中的 6 個其實沒作用，UI 卻看不出來
  改成宣告式之後，漏填欄位會在載入時就被 validate() 抓到，而不是等使用者點到才炸。

每個策略必須提供：
  key / label / caption / bar_note / sort_desc / prelim_key / color
  uses_horizon   — 排序是否真的會讀「週期評分」選單（決定該選單是否停用）
  evidence_model — 對應 backtest_results.json 裡的模型名稱（None = 未回測）
  metric(r)      — 卡片與長條圖顯示的主要數字
  select(results, ctx) — 篩選 + 排序，回傳依序排好的清單
                          ctx = {"buy_bar": float, "horizon_key": str|None}
"""


def _hscore(r, horizon_key):
    """依選定週期取分數；未選則用綜合評分。"""
    if not horizon_key:
        return r.get("total_score", 0)
    return _hz_tech(r, horizon_key)


def _long(r):
    """
    排序一律用**純技術**長線分（horizon_tech），不是混合分。

    ⚠️ 這兩個是不同數字（台積電：純技術 94 vs 混合 81）。回測與分數門檻分析
    用的是純技術分——基本面/目標價/新聞沒有歷史快照，無法納入回測。若改用
    混合分排序，等於把「未經驗證的 30% 目標價 + 45% 基本面」偷渡進一個
    宣稱有實證支持的策略裡，實證數字就不再適用。
    """
    ht = r.get("horizon_tech")
    if isinstance(ht, dict) and "long" in ht:
        return ht["long"]
    # 舊快取沒有 horizon_tech 時退回混合分（會有偏差，但不至於壞掉）
    return (r.get("horizon") or {}).get("long", {}).get("score", 0)


def _hz_tech(r, key):
    ht = r.get("horizon_tech")
    if isinstance(ht, dict) and key in ht:
        return ht[key]
    return (r.get("horizon") or {}).get(key, {}).get("score", 0)


# ── 各策略的篩選＋排序 ────────────────────────────────────────────────────────

def _sel_sectorhot(results, ctx):
    # 回測最佳（+3.07%, t=5.32，兩次獨立執行一致）：前5強族群 + 長線分最高
    from services.sector import analyse_sectors, get_industry_map
    secs = analyse_sectors(results, min_members=3)
    hot = {s["name"] for s in secs[:5]}
    ind = get_industry_map()
    view = [r for r in results
            if (ind.get(r.get("stock_id")) or {}).get("name") in hot
            and _long(r) >= ctx["buy_bar"]]
    view.sort(key=_long, reverse=True)
    return view


def _sel_bestproven(results, ctx):
    view = [r for r in results
            if _long(r) >= ctx["buy_bar"] and (r.get("volume_adj", 0) or 0) >= 0]
    if not view:      # 舊快取可能沒有 volume_adj，退回只用長線分
        view = [r for r in results if _long(r) >= ctx["buy_bar"]]
    view.sort(key=_long, reverse=True)
    return view


def _sel_momentum(results, ctx):
    hk = ctx.get("horizon_key")
    view = [r for r in results if _hscore(r, hk) >= ctx["buy_bar"]]
    view.sort(key=lambda r: _hscore(r, hk), reverse=True)
    return view


def _sel_lowpe(results, ctx):
    # 3–12 倍：<3 倍多為業外一次性收益灌大 EPS 的假低估（價值陷阱）
    view = [r for r in results
            if r.get("pe_ratio") is not None and 3 <= r["pe_ratio"] <= 12]
    view.sort(key=lambda r: r["pe_ratio"])
    return view


def _sel_limitup(results, ctx):
    view = [r for r in results if r.get("is_limit_up")]
    view.sort(key=lambda r: (r.get("max_streak", 0), r.get("total_score", 0)),
              reverse=True)
    return view


def _sel_sleeper(results, ctx):
    view = [r for r in results if (r.get("potential") or {}).get("qualifies")]
    view.sort(key=lambda r: (r.get("potential") or {}).get("total", 0), reverse=True)
    return view


def _sel_balanced(results, ctx):
    view = [r for r in results
            if r.get("total_score", 0) >= 48
            and (r.get("potential") or {}).get("low_base", 0) >= 45
            and (r.get("potential") or {}).get("total", 0) >= 45
            and (r.get("rr") is None or r["rr"] >= 1.5)]
    view.sort(key=lambda r: r.get("combined_score", 0), reverse=True)
    return view


STRATEGIES = [
    {
        "key": "sectorhot", "label": "🏭 強勢族群+長線分",
        "caption": "熱門族群中的強股　✅回測第一",
        "bar_note": "門檻：屬於動能前5強族群，依長線結構分排序（回測最佳）",
        "sort_desc": "**長線結構分**（族群動能前5強之內）",
        "prelim_key": "prelim_bestproven", "color": "#26a69a",
        "uses_horizon": False, "evidence_model": "強勢族群+長線分",
        "metric": _long, "select": _sel_sectorhot,
    },
    {
        "key": "bestproven", "label": "🏆 長線+量能確認",
        "caption": "長線結構強且量價未轉弱　✅次佳",
        "bar_note": "門檻：長線結構分 ≥ 買進線 且 量價未轉弱",
        "sort_desc": "**長線結構分**（量價未轉弱者）",
        "prelim_key": "prelim_bestproven", "color": "#66bb6a",
        "uses_horizon": False, "evidence_model": "長線+量能確認",
        "metric": _long, "select": _sel_bestproven,
    },
    {
        "key": "momentum", "label": "🚀 綜合強勢",
        "caption": "趨勢已成、順勢操作　✅回測有效",
        "bar_note": "門檻：所選週期評分 ≥ 買進線（依大盤環境動態調整）",
        "sort_desc": "**所選週期的評分**（可用上方選單切換）",
        "prelim_key": "prelim_momentum", "color": None,
        "uses_horizon": True, "evidence_model": "綜合強勢(技術)",
        "metric": lambda r: r.get("total_score", 0), "select": _sel_momentum,
    },
    {
        "key": "lowpe", "label": "💎 超低本益比",
        "caption": "本益比最低的便宜股　❔未驗證",
        "bar_note": "門檻：本益比 3–12 倍（排除 <3 倍的一次性收益假低估）",
        "sort_desc": "**本益比由低到高**",
        "prelim_key": "prelim_lowpe", "color": "#ffd54f",
        "uses_horizon": False, "evidence_model": None,
        "metric": lambda r: r.get("pe_ratio") or 0, "select": _sel_lowpe,
    },
    {
        "key": "limitup", "label": "🔥 漲停動能",
        "caption": "連日漲停高動能　❔未回測",
        "bar_note": "門檻：近期有連日漲停紀錄",
        "sort_desc": "**連續漲停天數 → 綜合評分**",
        "prelim_key": "prelim_momentum", "color": None,
        "uses_horizon": False, "evidence_model": None,
        "metric": lambda r: r.get("total_score", 0), "select": _sel_limitup,
    },
    {
        "key": "sleeper", "label": "🌱 潛力潛伏",
        "caption": "題材浮現但還沒漲　⛔多頭失效",
        "bar_note": "門檻：通過『有題材且尚未起漲』檢核",
        "sort_desc": "**潛力分**",
        "prelim_key": "prelim_sleeper", "color": "#7986cb",
        "uses_horizon": False, "evidence_model": "潛力潛伏",
        "metric": lambda r: (r.get("potential") or {}).get("total", 0),
        "select": _sel_sleeper,
    },
    {
        "key": "balanced", "label": "⚖️ 攻守兼備",
        "caption": "體質強又有餘裕　⛔多頭失效",
        "bar_note": "門檻：綜合評分不弱 + 尚未過熱 + 風報比 ≥ 1.5",
        "sort_desc": "**攻守兼備分**（綜合×潛力幾何平均）",
        "prelim_key": "prelim_balanced", "color": "#4dd0e1",
        "uses_horizon": False, "evidence_model": "攻守兼備",
        "metric": lambda r: r.get("combined_score", 0), "select": _sel_balanced,
    },
]

_REQUIRED = ("key", "label", "caption", "bar_note", "sort_desc", "prelim_key",
             "color", "uses_horizon", "evidence_model", "metric", "select")


def validate():
    """啟動時就檢查每個策略欄位齊全 —— 漏填要在這裡爆，而不是使用者點到才爆。"""
    seen = set()
    for s in STRATEGIES:
        missing = [f for f in _REQUIRED if f not in s]
        if missing:
            raise ValueError(f"策略 {s.get('key', '?')} 缺少欄位: {missing}")
        if s["key"] in seen:
            raise ValueError(f"策略 key 重複: {s['key']}")
        seen.add(s["key"])
    return True


validate()

BY_KEY = {s["key"]: s for s in STRATEGIES}
BY_LABEL = {s["label"]: s for s in STRATEGIES}
LABELS = [s["label"] for s in STRATEGIES]
CAPTIONS = [s["caption"] for s in STRATEGIES]


def get(key_or_label):
    return BY_KEY.get(key_or_label) or BY_LABEL.get(key_or_label)
