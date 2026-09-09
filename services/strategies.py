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


def _hscore_key(r, horizon_key):
    """
    週期評分的排序鍵 —— **一律附上距季線幅度破平手**。

    週期分和長線分一樣是離散跳點疊出來的，實測「綜合強勢」用長線排序時
    有 156 檔並列 94，前 10 名等於按股號由小到大取（1102、1216、1301…），
    完全不是「最強的 10 檔」。凡是用分數排序的地方都要破平手，
    這裡與 _long_key 用同一個連續指標。
    """
    return (_hscore(r, horizon_key), _tiebreak(r))


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


def _long_key(r):
    """
    排序鍵＝(長線分, 距季線幅度)。

    長線分是 50 加減幾個離散跳點疊出來的，全市場只有 18 種值，且 156/566 檔
    並列滿分 94——只用它排序等於在同分群裡亂數挑。回測（兩次獨立執行一致）：
      不破平手  1個月 +0.52%(t=1.42 不顯著) / 3個月 -1.08%
      距季線破  1個月 +2.51%(t=5.17)        / 3個月 +5.05%
    """
    return (_long(r), _tiebreak(r))


def _tiebreak(r):
    """破平手用的連續值：距季線幅度。缺值排最後，不要讓 None 參與比較。"""
    v = r.get("above_ma120")
    return v if v is not None else -999


def _combined(r):
    """
    攻守兼備分＝綜合評分與潛力分的幾何平均。

    以前只在 app.py 算好塞進 row，strategies 直接讀 r["combined_score"]——
    呼叫者忘記塞就整個策略沉默地按股號排序。改成這裡自己算，讀不到才回退。
    """
    v = r.get("combined_score")
    if v is not None:
        return v
    pt = (r.get("potential") or {}).get("total", 0)
    return int(round((max(r.get("total_score", 0), 0) * max(pt, 0)) ** 0.5))


def _hz_tech(r, key):
    ht = r.get("horizon_tech")
    if isinstance(ht, dict) and key in ht:
        return ht[key]
    return (r.get("horizon") or {}).get(key, {}).get("score", 0)


# ── 篩選條件（單一來源）──────────────────────────────────────────────────────
# 每個條件寫成 (說明, 判斷函式)。select() 與 explain() **共用同一份**，
# 所以「為什麼沒選到」的解釋不可能與實際篩選結果不一致。
# 先前只有 select()，使用者看到自己持股沒出現在選股結果時完全無從得知原因
# （例：萬海長線分 94 很高，卻因量價轉弱被「長線+量能確認」濾掉）。

def _f_long_bar(r, ctx):
    return _long(r) >= ctx["buy_bar"], f"長線分 {_long(r)} ≥ 買進線 {ctx['buy_bar']:.0f}"


def _f_vol_ok(r, ctx):
    v = r.get("volume_adj", 0) or 0
    return v >= 0, f"量價未轉弱（量價分 {v:+d}）"


def _f_hot_sector(r, ctx):
    hot = ctx.get("hot_sectors") or set()
    ind = ctx.get("industry_of") or {}
    name = (ind.get(r.get("stock_id")) or {}).get("name")
    return name in hot, f"屬於動能前5強族群（本檔：{name or '未分類'}）"


def _f_hscore_bar(r, ctx):
    v = _hscore(r, ctx.get("horizon_key"))
    return v >= ctx["buy_bar"], f"所選週期評分 {v} ≥ 買進線 {ctx['buy_bar']:.0f}"


def _f_pe_band(r, ctx):
    pe = r.get("pe_ratio")
    ok = pe is not None and 3 <= pe <= 12
    return ok, f"本益比 3–12 倍（本檔：{pe if pe else '無'}）"


def _f_is_limitup(r, ctx):
    return bool(r.get("is_limit_up")), "近期有連日漲停紀錄"


def _f_sleeper_ok(r, ctx):
    return bool((r.get("potential") or {}).get("qualifies")), "通過『有題材且尚未起漲』檢核"


def _f_total_48(r, ctx):
    return r.get("total_score", 0) >= 48, f"綜合評分 {r.get('total_score', 0)} ≥ 48"


def _f_lowbase_45(r, ctx):
    v = (r.get("potential") or {}).get("low_base", 0)
    return v >= 45, f"低基期分 {v} ≥ 45（尚未過熱）"


def _f_pot_45(r, ctx):
    v = (r.get("potential") or {}).get("total", 0)
    return v >= 45, f"潛力分 {v} ≥ 45"


def _f_rr_15(r, ctx):
    rr = r.get("rr")
    return (rr is None or rr >= 1.5), f"風報比 ≥ 1.5（本檔：{rr if rr else '無'}）"


def prepare_ctx(results, ctx):
    """族群排名等「需要全體才能算」的資訊，先算好放進 ctx 供各條件使用。"""
    ctx = dict(ctx)
    if "hot_sectors" not in ctx:
        try:
            from services.sector import analyse_sectors, get_industry_map
            secs = analyse_sectors(results, min_members=3)
            ctx["hot_sectors"] = {s["name"] for s in secs[:5]}
            ctx["industry_of"] = get_industry_map()
        except Exception:
            ctx["hot_sectors"], ctx["industry_of"] = set(), {}
    return ctx


def select(sdef, results, ctx):
    """套用該策略的所有條件並排序。"""
    ctx = prepare_ctx(results, ctx)
    view = [r for r in results
            if all(f(r, ctx)[0] for _, f in sdef["filters"])]
    view.sort(key=lambda r: sdef["sort_key"](r, ctx), reverse=sdef.get("desc", True))
    return view


def explain(sdef, r, results, ctx):
    """這一檔為何入選／未入選——與 select() 用同一份條件，不會不一致。"""
    ctx = prepare_ctx(results, ctx)
    out = []
    for label, f in sdef["filters"]:
        ok, detail = f(r, ctx)
        out.append({"ok": ok, "label": label, "detail": detail})
    return out


from services.scoring import BUY_BAR


def _trend(r):
    """
    連續趨勢結構分（0~100 百分位）—— **全站排序的主訊號**。

    139 期回測、+60日超額 +7.18%(t=5.52、勝率66%)，是所有測過的模型裡最高的 t 值。
    加動能/量能/資券/估值都更差（見 services/scoring 的表）。

    舊列（快取或舊版掃描）沒有 trend_score 時退回離散長線分——會偏差但不至於壞掉。
    """
    v = r.get("trend_score")
    return v if v is not None else _long(r)


def _f_trend_bar(r, ctx):
    bar = ctx.get("trend_bar", BUY_BAR)
    return _trend(r) >= bar, f"趨勢結構分 {_trend(r):.0f} ≥ 買進線 {bar:.0f}"


STRATEGIES = [
    {
        "key": "trend", "label": "📈 趨勢結構分（主力）",
        "caption": "趨勢結構分最高者　（強弱見策略對照表）",
        "bar_note": (f"門檻：趨勢結構分 ≥ {BUY_BAR:.0f}"
                     f"（即贏過全市場 {BUY_BAR:.0f}% 的股票；實測 40 分以下超額為負）"),
        "sort_desc": "**連續趨勢結構分**（距季線／均線排列／季線斜率的橫斷面百分位）",
        "prelim_key": "prelim_bestproven", "color": "#66bb6a",
        "uses_horizon": False,
        "evidence_model": "T1 趨勢(連續)", "evidence_run": "factor_3y",
        "metric": _trend,
        "filters": [("趨勢結構分達買進線", _f_trend_bar)],
        "sort_key": lambda r, c: _trend(r),
    },
    {
        "key": "sectorhot", "label": "🏭 強勢族群＋趨勢分",
        "caption": "限動能前5強族群　（同次回測不如純趨勢分）",
        "bar_note": f"門檻：屬於動能前5強族群，且趨勢結構分 ≥ {BUY_BAR:.0f}",
        "sort_desc": "**趨勢結構分**（限動能前5強族群）",
        "prelim_key": "prelim_bestproven", "color": "#26a69a",
        "uses_horizon": False,
        "evidence_model": "強勢族群+長線分", "evidence_run": "main_3y",
        "metric": _trend,
        "filters": [("屬於前5強族群", _f_hot_sector),
                    ("趨勢結構分達買進線", _f_trend_bar)],
        "sort_key": lambda r, c: _trend(r),
    },
    {
        "key": "lowpe", "label": "💎 超低本益比",
        "caption": "本益比最低的便宜股　⛔回測為負且不穩定",
        "bar_note": "門檻：本益比 3–12 倍（排除 <3 倍的一次性收益假低估）",
        "sort_desc": "**本益比由低到高**",
        "prelim_key": "prelim_lowpe", "color": "#ffd54f",
        "uses_horizon": False,
        "evidence_model": "P1 純低本益比", "evidence_run": "factor_3y",
        "metric": lambda r: r.get("pe_ratio") or 0,
        "filters": [("本益比 3–12 倍", _f_pe_band)],
        "sort_key": lambda r, c: -(r.get("pe_ratio") or 999),
    },
    {
        "key": "limitup", "label": "🔥 漲停動能",
        "caption": "近期連日漲停　（1個月最強，但波動極大）",
        "bar_note": "門檻：近期有連日漲停紀錄",
        "sort_desc": "**連續漲停天數 → 趨勢結構分**",
        "prelim_key": "prelim_momentum", "color": None,
        "uses_horizon": False, "evidence_model": None, "evidence_run": "main_3y",
        "metric": _trend,
        "filters": [("近期連日漲停", _f_is_limitup)],
        "sort_key": lambda r, c: (r.get("max_streak", 0), _trend(r)),
    },
    {
        "key": "contrarian", "label": "🌱 逆勢潛伏（低基期）",
        "caption": "低基期且有題材　⛔前後半段皆為負",
        "bar_note": "門檻：低基期且潛力分達標，或體質不弱且風報比 ≥1.5",
        "sort_desc": "**潛力分**（低基期 × 題材）",
        "prelim_key": "prelim_sleeper", "color": "#7986cb",
        "uses_horizon": False,
        "evidence_model": "潛力潛伏", "evidence_run": "main_3y",
        "metric": lambda r: (r.get("potential") or {}).get("total", 0),
        "filters": [("低基期 ≥45", _f_lowbase_45), ("潛力分 ≥45", _f_pot_45)],
        "sort_key": lambda r, c: ((r.get("potential") or {}).get("total", 0),
                                  _tiebreak(r)),
    },
]

_REQUIRED = ("key", "label", "caption", "bar_note", "sort_desc", "prelim_key",
             "color", "uses_horizon", "evidence_model", "evidence_run",
             "metric", "filters", "sort_key")


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
