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
  key / label / caption / sort_desc / prelim_key / color
  evidence_model — 對應 backtest_results.json 裡的模型名稱（None = 未回測）
  evidence_run   — 該模型出自哪一次回測 run
  metric(r)      — 卡片與長條圖顯示的主要數字
  filters        — [(說明, fn(r, ctx) -> (bool, 細節字串))]，**唯一的條件來源**：
                   select() 篩選、explain() 解釋、bar_note() 產生說明文字都讀它
  sort_key(r, ctx) — 排序鍵
選填：
  note           — bar_note 括號裡的補充說明

`select(results, ctx)` 的 ctx = {"buy_bar": float, "trend_bar": float}。
"""


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


def _tiebreak(r):
    """破平手用的連續值：距季線幅度。缺值排最後，不要讓 None 參與比較。"""
    v = r.get("above_ma120")
    return v if v is not None else -999


# ── 篩選條件（單一來源）──────────────────────────────────────────────────────
# 每個條件寫成 (說明, 判斷函式)。select() 與 explain() **共用同一份**，
# 所以「為什麼沒選到」的解釋不可能與實際篩選結果不一致。
# 先前只有 select()，使用者看到自己持股沒出現在選股結果時完全無從得知原因
# （例：萬海長線分 94 很高，卻因量價轉弱被「長線+量能確認」濾掉）。

def _f_hot_sector(r, ctx):
    hot = ctx.get("hot_sectors") or set()
    ind = ctx.get("industry_of") or {}
    name = (ind.get(r.get("stock_id")) or {}).get("name")
    return name in hot, f"屬於動能前5強族群（本檔：{name or '未分類'}）"


def _f_pe_band(r, ctx):
    pe = r.get("pe_ratio")
    ok = pe is not None and 3 <= pe <= 12
    return ok, f"本益比 3–12 倍（本檔：{pe if pe else '無'}）"


def _f_is_limitup(r, ctx):
    return bool(r.get("is_limit_up")), "近期有連日漲停紀錄"


def _f_lowbase_45(r, ctx):
    v = (r.get("potential") or {}).get("low_base", 0)
    return v >= 45, f"低基期分 {v} ≥ 45（尚未過熱）"


def _f_pot_45(r, ctx):
    v = (r.get("potential") or {}).get("total", 0)
    return v >= 45, f"潛力分 {v} ≥ 45"


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


def bar_note(sdef) -> str:
    """
    「門檻：…」說明文字 —— **由 filters 的標籤生成**，不是另外手寫一份。

    ⚠️ 手寫版本已經出過錯：`contrarian` 的 bar_note 寫「低基期且潛力分達標，
    **或**體質不弱且風報比 ≥1.5」，但它的 filters 只有低基期與潛力分兩條，
    那個「或…」的分支在整併時就被刪了，說明卻留著。本專案的規矩是
    「篩選條件即說明」（select 與 explain 共用同一份），bar_note 也該進來。

    `note` 是可選的補充說明（例如買進線的意義、為什麼排除某個區間）。
    """
    labels = "，且".join(label for label, _ in sdef["filters"])
    extra = sdef.get("note")
    return f"門檻：{labels}" + (f"（{extra}）" if extra else "")


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
        # 措辭刻意避開「贏過全市場 N%」這個句型——`check_consistency.py` 就是靠它
        # 從畫面文字挖出各頁的趨勢分來比對，說明文字裡再出現一次會混進去。
        "note": (f"分數就是百分位，{BUY_BAR:.0f} 分代表趨勢強度"
                 f"排在全市場前 {100 - BUY_BAR:.0f}%"),
        "sort_desc": "**連續趨勢結構分**（距季線／均線排列／季線斜率的橫斷面百分位）",
        "prelim_key": "prelim_bestproven", "color": "#66bb6a",
        "evidence_model": "T1 趨勢(連續)", "evidence_run": "factor_3y",
        "metric": _trend,
        "filters": [("趨勢結構分達買進線", _f_trend_bar)],
        "sort_key": lambda r, c: _trend(r),
    },
    {
        "key": "sectorhot", "label": "🏭 強勢族群＋趨勢分",
        "caption": "限動能前5強族群　（同次回測不如純趨勢分）",
        "sort_desc": "**趨勢結構分**（限動能前5強族群）",
        "prelim_key": "prelim_bestproven", "color": "#26a69a",
        "evidence_model": "強勢族群+長線分", "evidence_run": "main_3y",
        "metric": _trend,
        "filters": [("屬於前5強族群", _f_hot_sector),
                    ("趨勢結構分達買進線", _f_trend_bar)],
        "sort_key": lambda r, c: _trend(r),
    },
    {
        "key": "lowpe", "label": "💎 超低本益比",
        "caption": "本益比最低的便宜股　⛔回測為負且不穩定",
        "note": "排除 <3 倍者——多半是業外一次性收益灌大 EPS 的假低估",
        "sort_desc": "**本益比由低到高**",
        "prelim_key": "prelim_lowpe", "color": "#ffd54f",
        "evidence_model": "P1 純低本益比", "evidence_run": "factor_3y",
        "metric": lambda r: r.get("pe_ratio") or 0,
        "filters": [("本益比 3–12 倍", _f_pe_band)],
        "sort_key": lambda r, c: -(r.get("pe_ratio") or 999),
    },
    {
        "key": "limitup", "label": "🔥 漲停動能",
        "caption": "近期連日漲停　（1個月最強，但波動極大）",
        "sort_desc": "**連續漲停天數 → 趨勢結構分**",
        # 初篩鍵決定「哪些股票會被補齊新聞/財報」。用 total_score 與漲停毫無關係，
        # 改用趨勢分初篩鍵，至少讓被深度分析的是趨勢也不差的漲停股。
        "prelim_key": "prelim_bestproven", "color": None,
        "evidence_model": None, "evidence_run": "main_3y",
        "metric": _trend,
        "filters": [("近期連日漲停", _f_is_limitup)],
        "sort_key": lambda r, c: (r.get("max_streak", 0), _trend(r)),
    },
    {
        "key": "contrarian", "label": "🌱 逆勢潛伏（低基期）",
        "caption": "低基期且有題材　⛔前後半段皆為負",
        "sort_desc": "**潛力分**（低基期 × 題材）",
        "prelim_key": "prelim_sleeper", "color": "#7986cb",
        "evidence_model": "潛力潛伏", "evidence_run": "main_3y",
        "metric": lambda r: (r.get("potential") or {}).get("total", 0),
        "filters": [("低基期 ≥45", _f_lowbase_45), ("潛力分 ≥45", _f_pot_45)],
        "sort_key": lambda r, c: ((r.get("potential") or {}).get("total", 0),
                                  _tiebreak(r)),
    },
]

# `bar_note` 已改為由 filters 生成（見 bar_note()），`uses_horizon` 在策略
# 整併為 5 個之後全為 False、選單也移除了，兩個欄位都不再是必填。
_REQUIRED = ("key", "label", "caption", "sort_desc", "prelim_key",
             "color", "evidence_model", "evidence_run",
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
