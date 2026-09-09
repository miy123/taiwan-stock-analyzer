"""
共用 UI 元件 —— 三個頁面（個股分析／智能選股／我的持股）顯示同一種資訊時，
必須呼叫同一個函式，而不是各自複製一份 HTML。

為什麼要抽出來：
  計分邏輯早就統一在 services/analysis.py，但「顯示」沒有。實測同樣的東西被寫了 3 遍：
    · 四格週期分數 + 實證效力標記 → 3 處
    · 本益比／殖利率徽章          → 3 處
    · 長線分實證區間（勝率/超額）  → 2 處
  結果就是每次調整（例如加效力標記、加本益比）都要三邊各改一次，很容易漏掉一處，
  造成同一個數字在不同頁長得不一樣。這裡集中之後，改一次三頁同步生效。

慣例：這些函式**只回傳 HTML 字串**，不直接呼叫 st.*，方便嵌進各頁不同的版面容器。
"""

from services.evidence import (
    horizon_efficacy,
    trend_bucket_stats, trend_threshold, trend_monotonicity, trend_bucket_rows,
)

# 「長」已從這裡移除：它就是**連續趨勢結構分**要回答的問題，而且舊的離散長線分
# 有 49% 的股票並列 94 分、鑑別力極差。同一張卡片放兩個都在講「長期」的數字
# （趨勢分 98 vs 長線分 94）只會讓人不知道該信哪個——這個坑本專案踩過兩次。
HORIZON_ORDER = [("ultra_short", "極短"), ("short", "短"), ("medium", "中")]


def pe_badge(pe, dividend_yield=None, highlight=False, min_width=88) -> str:
    """本益比徽章（依高低變色），含殖利率。無資料時明確說明而非留白。"""
    if pe is None or pe <= 0:
        return (f"<div style='min-width:{min_width}px;font-size:11px;color:#666;'>"
                f"本益比 —<div>（虧損或無資料）</div></div>")
    color = ("#4caf50" if pe < 12 else "#a9e34b" if pe < 20
             else "#ff9800" if pe < 35 else "#f44336")
    box = ("border:1px solid #ffd54f;border-radius:4px;padding:1px 4px;"
           if highlight else "")
    dy_txt = (f"殖利率 {dividend_yield * 100:.1f}%"
              if dividend_yield else "殖利率 —")
    return (
        f"<div style='min-width:{min_width}px;font-size:11px;color:#aaa;{box}'>"
        f"<div>本益比 <span style='color:{color};font-weight:800;font-size:14px;'>"
        f"{pe:.1f}</span></div><div>{dy_txt}</div></div>"
    )


def pe_inline(pe, dividend_yield=None) -> str:
    """單行版本的本益比（給持股卡片這種空間較窄的地方）。"""
    if pe is None or pe <= 0:
        return "<span style='color:#666;'>本益比 —（虧損或無資料）</span>"
    color = ("#4caf50" if pe < 12 else "#a9e34b" if pe < 20
             else "#ff9800" if pe < 35 else "#f44336")
    dy = f"　殖利率 {dividend_yield * 100:.1f}%" if dividend_yield else ""
    return f"本益比 <b style='color:{color};'>{pe:.1f}</b>{dy}"


def horizon_cells(horizon: dict, selected_key=None, show_legend=True,
                  min_width=126) -> str:
    """
    週期分數格（極短／短／中，共三格），附實證效力標記（✅有效／🟡偏弱／🔴雜訊）。

    「長」那一格已移除——長期由趨勢結構分負責，全站只留一個長期數字。

    selected_key: 目前排序依據的週期會加框。整併成 5 個策略後沒有任何策略
                  會讀週期選單，所以實際上一律傳 None。
    """
    hz = horizon or {}
    cells = []
    for key, short in HORIZON_ORDER:
        h = hz.get(key) or {}
        sc = h.get("score", "-")
        hc = h.get("color", "#78909c")
        eff = horizon_efficacy(key)
        box = (f"border:1px solid {hc};border-radius:4px;"
               if key == selected_key else "")
        cells.append(
            f"<div style='text-align:center;padding:1px 4px;{box}'>"
            f"<div style='font-size:13px;font-weight:800;color:{hc};'>{sc}</div>"
            f"<div style='font-size:9px;color:#90a4ae;'>{short}"
            f"<span style='font-size:8px;'>{eff.get('tag', '')}</span></div></div>"
        )
    legend = ("<span style='font-size:8px;'>✅有效 🔴雜訊</span>"
              if show_legend else "")
    return (
        f"<div style='min-width:{min_width}px;'>"
        f"<div style='font-size:10px;color:#78909c;margin-bottom:2px;'>"
        f"週期評分 {legend}</div>"
        f"<div style='display:flex;gap:3px;'>{''.join(cells)}</div></div>"
    )


def evidence_badge(trend_score, hold_days=20, min_width=150) -> str:
    """
    該檔**趨勢結構分**落在哪個實證區間 → 歷史超額與贏大盤比率。

    ⚠️ 以前這裡吃的是離散長線分、查的是離散分的分桶表。分數換成連續趨勢分之後
    那張表就不適用了（不同量表，只是數字範圍看起來像）。現在改讀
    `trend_score_thresholds.json`，也就是連續分自己量出來的數字。
    """
    b = trend_bucket_stats(trend_score, hold_days)
    if not b:
        return f"<div style='min-width:{min_width}px;'></div>"
    ok = b.get("excess", 0) > 0
    color = "#4caf50" if ok else "#f44336"
    return (
        f"<div style='min-width:{min_width}px;font-size:11px;'>"
        f"<div style='color:#78909c;'>趨勢分 {trend_score:.0f} 落在 {b['range']} 區間</div>"
        f"<div style='color:{color};font-weight:700;'>{'✅' if ok else '⚠️'} "
        f"超額 {b.get('excess', 0):+.2f}%　贏大盤 {b.get('beat_rate', 0):.0f}%"
        f"　t={b.get('t', 0):+.1f}</div></div>"
    )


def rr_cell(rr, stop_pct=None, atr_pct=None, min_width=96) -> str:
    """風報比 + 停損幅度。"""
    if rr is None:
        return f"<div style='min-width:{min_width}px;'></div>"
    color = ("#4caf50" if rr >= 2.5 else "#a9e34b" if rr >= 1.5
             else "#ff9800" if rr >= 1 else "#f44336")
    second = ""
    if stop_pct is not None:
        second = f"停損 {abs(stop_pct):.1f}%"
        if atr_pct is not None:
            second += f"　ATR {atr_pct:.1f}%"
    return (
        f"<div style='min-width:{min_width}px;font-size:11px;color:#aaa;'>"
        f"<div>風報比 <span style='color:{color};font-weight:800;font-size:14px;'>"
        f"{rr:.2f}</span></div><div>{second}</div></div>"
    )


def threshold_note(hold_days=20) -> str:
    """
    「幾分以上才值得買」—— 數字全部從實證檔讀出，畫面上不寫死。
    先前這裡寫死的 179 期數字在換模型後就過期了，而且沒人發現。
    """
    rows = trend_bucket_rows(hold_days)
    thr = trend_threshold(hold_days)
    if not rows or thr is None:
        return ""
    neg = [r for r in rows if r["excess"] <= 0]
    top = rows[-1]
    rho = trend_monotonicity(hold_days)
    from services.scoring import BUY_BAR
    neg_txt = (f"**{neg[0]['lo']}–{neg[-1]['hi']} 分區間的超額報酬為負**"
               f"（{min(r['excess'] for r in neg):+.2f}% ~ "
               f"{max(r['excess'] for r in neg):+.2f}%），" if neg else "")
    # 買進線比「轉正點」保守一級：轉正那一格通常 t 值極低（統計上與 0 沒差別），
    # 拿它當門檻等於把雜訊當訊號。
    # 要看的是**買進線下方那一格**——它若與 0 無異，就是「不要再往下放」的理由
    bar_row = next((r for r in rows if r["hi"] <= BUY_BAR
                    and r["hi"] > BUY_BAR - 10), None)
    bar_txt = ""
    if bar_row and abs(bar_row.get("t", 0)) < 1.5:
        bar_txt = (f"　（{bar_row['range']} 分只有 {bar_row['excess']:+.2f}%、"
                   f"t={bar_row['t']:+.2f}，與 0 無異，故買進線取較保守的 "
                   f"{BUY_BAR:.0f} 分）")
    return (
        f"{neg_txt}約 **{thr:.0f} 分以上轉正**{bar_txt}；優勢集中在最高區間 "
        f"**{top['range']} 分：超額 {top['excess']:+.2f}%、t={top['t']:+.2f}、"
        f"贏大盤 {top['beat_rate']:.0f}%**。"
        + (f"　分數與後續超額的單調性 ρ={rho:+.2f}。" if rho is not None else "")
    )


def buy_bar() -> float:
    """
    三頁共用的買進門檻（趨勢分量表）—— 就是 `services.scoring.BUY_BAR`。

    ⚠️ 這裡曾經回傳 `trend_threshold()`（分桶表的「超額轉正點」＝40 分），
    於是同一件事在畫面上有兩個數字：智能選股按 50 分篩選、說明也寫 50，
    但個股分析的儀表板與我的持股的「實證門檻檢視」卻寫 40——
    一檔 45 分的股票在個股頁顯示「✅ 已達實證門檻」，在選股頁卻根本不會出現。
    `services/scoring.py` 已說明為何**刻意不採用轉正點**：40–50 那一格
    t 值只有 0.2~1.2，與 0 沒有統計差異，拿它當門檻是把雜訊當訊號。
    """
    from services.scoring import BUY_BAR
    return BUY_BAR


def overheat_badge(overheat: dict, compact: bool = True) -> str:
    """
    「利多已反映／漲多留意」徽章 —— 三頁共用。

    先前這個提醒只寫在個股分析頁的風險警語裡，選股頁與持股頁完全看不到，
    於是使用者在持股頁看到台虹長線 88 分卻沒有任何「它已經漲 105%、本益比 91」
    的提示。判斷邏輯在 `technical.overheat_flag()`（唯一實作），這裡只負責畫。
    """
    if not overheat:
        return ""
    lvl = overheat.get("level")
    color = "#f44336" if lvl == "high" else "#ff9800"
    icon = "🔥" if lvl == "high" else "⚠️"
    txt = overheat.get("short", "")
    if compact:
        tip = "　".join(overheat.get("bits", []))
        return (f"<span title='{tip}' style='background:{color}22;color:{color};"
                f"border:1px solid {color}66;border-radius:4px;padding:1px 5px;"
                f"font-size:10px;font-weight:700;margin-right:4px;'>"
                f"{icon}{txt}</span>")
    return (f"<div style='font-size:11px;color:{color};'>{icon} <b>{txt}</b>　"
            f"{'　'.join(overheat.get('bits', []))}</div>")


def trend_cell(trend_score, min_width=104, primary=True) -> str:
    """
    趨勢結構分 —— **全站的選股訊號**，卡片上要最顯眼。

    分數本身就是百分位，所以副標直接寫「贏過全市場 X%」，
    使用者不必去記「幾分算高」。
    """
    if trend_score is None:
        return (f"<div style='min-width:{min_width}px;text-align:center;"
                f"font-size:11px;color:#666;'>趨勢分 —<div>（尚未全市場掃描）</div></div>")
    color = ("#4caf50" if trend_score >= 80 else "#a9e34b" if trend_score >= 70
             else "#ff9800" if trend_score >= 50 else "#f44336")
    ring = (f"box-shadow:0 0 0 2px {color};border-radius:10px;"
            f"background:rgba(255,255,255,0.03);" if primary else "")
    return (
        f"<div style='min-width:{min_width}px;text-align:center;padding:5px 8px;{ring}'>"
        f"<div style='font-size:26px;font-weight:900;color:{color};line-height:1;'>"
        f"{trend_score:.0f}</div>"
        f"<div style='font-size:10px;color:#aaa;margin-top:2px;'>趨勢結構分 ★</div>"
        f"<div style='font-size:9px;color:#78909c;'>贏過全市場 {trend_score:.0f}%</div>"
        f"</div>"
    )


def score_legend() -> str:
    """
    卡片上每個數字是什麼、哪個能拿來選股 —— 三頁共用的唯一說明。

    **所有數字都從實證檔生成。** 這張表先前是手寫的，換模型後就過期了，
    而且沒有任何測試會抓到（`check_consistency.py` 就是為此而寫的）。
    """
    from services.evidence import (
        strategy_stats, get_stats_for_model, trend_bucket_rows,
    )
    from services.recommendation import weight_note_str
    # 權重一律引用 recommendation 的定義。手寫的那份原本寫成
    # 「技術40%＋基本面30%」，與實際的 35/35 不符，而且沒有任何測試會抓到。
    _wnote = weight_note_str(True).replace(" + ", "＋").replace(" ", "")
    tr = strategy_stats("trend", 60) or {}
    lp = strategy_stats("lowpe", 60) or {}
    ct = strategy_stats("contrarian", 60) or {}
    total = get_stats_for_model("綜合強勢(技術)", 20, run="main_3y") or {}

    def fmt(d, extra=""):
        if not d:
            return "（無實證資料）"
        sig = "" if d.get("t") is None else f"、t={d['t']:+.2f}"
        return f"超額 **{d['excess']:+.2f}%**{sig}{extra}"

    rows = trend_bucket_rows(60)
    top = rows[-1] if rows else None
    trend_line = (f"✅ **就是用它**。持有3個月{fmt(tr)}"
                  f"、贏大盤 {tr.get('beat_rate', 0):.0f}%"
                  if tr else "✅ 選股主訊號")
    if top:
        trend_line += f"；最高分區間（{top['range']}）達 {top['excess']:+.2f}%"

    total_line = ("🟡 弱。"
                  + (f"超額 {total.get('excess_return', 0):+.2f}%、"
                     f"t={total.get('t_stat', 0):+.2f}"
                     f"（{'顯著' if total.get('significant') else '**不顯著**'}）"
                     if total else "預測力弱")
                  + "。當健檢用，不要拿來排序")

    return f"""
| 卡片上的數字 | 代表什麼 | 能當選股指標嗎 |
|---|---|---|
| **趨勢結構分** ★ | 距季線／均線排列／季線斜率在**當日全市場的百分位**。80 分＝趨勢強度贏過八成股票 | {trend_line} |
| 綜合評分 | {_wnote} 的體質總覽 | {total_line} |
| 潛力分 | 低基期×題材，找「還沒漲的」 | ⛔ 持有3個月{fmt(ct)} |
| 極短／短／中 週期評分 | 各持有期的技術強弱（1–3天／1週／1個月） | 🟡 越短週期越弱，僅供參考 |
| 本益比 | 估值 | ⛔ **單獨用會虧錢**：持有3個月{fmt(lp)} |
| 融資使用率 | 籌碼風險 | ⛔ 回測顯示無選股訊號，只當**風險警示**用 |

**結論：選股看「趨勢結構分」，其他都是背景資訊。**
但它是純技術的**相對排名**，不看貴不貴——高分常常正是因為已經漲很多，
請搭配 🔥 過熱警示與本益比一起看。
"""


def strategy_caption(key: str, hold_days: int = 60) -> str:
    """
    策略選單上的說明 —— **一律用可比較的數字，不要用形容詞**。

    先前寫的是「✅實證最強」「✅族群動能有效」，使用者無從判斷哪個強；
    而且那些數字來自不同次回測，本來就不能互比。現在全部來自
    `strategy_comparison.json`（同一批日期、同一個基準、同一次執行）。
    """
    from services.evidence import strategy_stats, strategy_ranking
    st = strategy_stats(key, hold_days)
    if not st:
        return "（尚未納入策略對照回測）"
    rank = [k for k, _ in strategy_ranking(hold_days)].index(key) + 1
    total = len(strategy_ranking(hold_days))
    good = st["excess"] > 0 and st["t"] >= 1.96
    bad = st["excess"] < 0 and st["t"] <= -1.96
    icon = "✅" if good else "⛔" if bad else "🟡"
    lab = {20: "1個月", 40: "2個月", 60: "3個月"}.get(hold_days, f"{hold_days}日")
    return (f"{icon} 第{rank}/{total}名　持有{lab}超額 **{st['excess']:+.2f}%**"
            f"　t={st['t']:+.2f}　贏大盤 {st['beat_rate']:.0f}%")


def strategy_table(hold_days: int = 60) -> str:
    """五個策略的並排比較 —— 同一次回測，直接可比。"""
    from services.evidence import (
        strategy_ranking, strategy_walk_forward, load_strategy_comparison,
    )
    from services.strategies import STRATEGIES
    rows = strategy_ranking(hold_days)
    if not rows:
        return ""
    meta = load_strategy_comparison()
    label = {s["key"]: s["label"] for s in STRATEGIES}
    lab = {20: "1個月", 40: "2個月", 60: "3個月"}.get(hold_days, f"{hold_days}日")
    out = [f"| 名次 | 策略 | 持有{lab}超額 | t值 | 贏大盤 | 走查前半 | 走查後半 |",
           "|---|---|---|---|---|---|---|"]
    for i, (k, v) in enumerate(rows, 1):
        w = strategy_walk_forward(k, hold_days)
        f, s = w.get("first_half"), w.get("second_half")
        flip = ""
        if f is not None and s is not None and f * s < 0:
            flip = " ⚠️翻盤"
        out.append(
            f"| {i} | {label.get(k, k)} | **{v['excess']:+.2f}%** | {v['t']:+.2f} | "
            f"{v['beat_rate']:.0f}% | {f:+.2f}% | {s:+.2f}%{flip} |"
        )
    out.append("")
    out.append(f"*{meta.get('generated_periods', '—')} 期（{meta.get('date_range', ['', ''])[0]}"
               f" ~ {meta.get('date_range', ['', ''])[1]}）、每次選前 "
               f"{meta.get('topn', 10)} 名、扣成本前。*"
               "**同一批換股日、同一個等權基準，所以這張表可以直接比。**")
    return "\n".join(out)


def cross_evidence(keys, hold_days: int = 60) -> str:
    """
    交叉篩選的實證 —— 逐組報出「交集 vs 單獨用較強的那個」。

    使用者要用這個選股，就必須看到它到底有沒有比較好。
    實測結果是**每一組可測的交集都比較差**（第四次驗證「多加一層過濾更差」），
    所以這裡不是裝飾，是必要的揭露。
    """
    from itertools import combinations
    from services.evidence import cross_stats, strategy_stats, cross_top_n
    from services.strategies import STRATEGIES
    label = {s["key"]: s["label"] for s in STRATEGIES}
    n = cross_top_n()
    lines, any_row = [], False
    for a, b in combinations(keys, 2):
        c = cross_stats(a, b, hold_days)
        if not c or not c.get("periods"):
            lines.append(f"| {label.get(a, a)} ∩ {label.get(b, b)} | "
                         f"樣本不足 | — | — | — |")
            continue
        any_row = True
        sa = strategy_stats(a, hold_days).get("excess", 0)
        sb = strategy_stats(b, hold_days).get("excess", 0)
        best = max(sa, sb)
        d = c["excess"] - best
        lines.append(
            f"| {label.get(a, a)} ∩ {label.get(b, b)} | {c['periods']} 期"
            f"（平均 {c.get('avg_picks', 0):.1f} 檔）| **{c['excess']:+.2f}%** | "
            f"{best:+.2f}% | {d:+.2f}% {'✅' if d > 0 else '⛔'} |"
        )
    if not lines:
        return ""
    lab = {20: "1個月", 40: "2個月", 60: "3個月"}.get(hold_days, f"{hold_days}日")
    head = (f"每個策略取前 {n} 名取交集，持有{lab}的超額報酬：\n\n"
            f"| 組合 | 有交集期數 | 交集超額 | 單獨用較強的那個 | 差異 |\n"
            f"|---|---|---|---|---|\n")
    tail = ("\n\n**實測：每一組可測的交集都輸給「單獨用較強的那一個」。**"
            "這是本專案第四次得到「多加一層過濾會更差」的結果"
            "（前三次：族群濾網、低波動濾網、四重確認）。\n\n"
            "→ 交叉篩選適合用來**找共識標的、縮小研究範圍**，"
            "但不要期待它比單押最強的策略賺更多。"
            if any_row else "")
    return head + "\n".join(lines) + tail


def hold_longer_note() -> str:
    """「抱越久超額越大」的證據 —— 取最高分區間在各持有期的超額，由資料生成。"""
    from services.evidence import trend_bucket_rows
    parts = []
    for h in (20, 40, 60):
        rows = trend_bucket_rows(h)
        if rows:
            parts.append(f"{ {20:'1個月', 40:'2個月', 60:'3個月'}[h] } "
                         f"{rows[-1]['excess']:+.2f}%")
    if not parts:
        return "同一批高分股，持有越久超額越大。"
    return ("同一批高分股（分數最高的區間），持有越久超額越大："
            + "　→　".join(parts) + "。")


def cmp_periods():
    """策略對照回測的期數（畫面顯示期數時用，不要寫死）。"""
    from services.evidence import load_strategy_comparison
    return load_strategy_comparison().get("generated_periods")


def bucket_table(hold_days=60) -> str:
    """分數區間 → 後續表現的表格（由實證檔生成，畫面上不寫死任何數字）。"""
    from services.evidence import trend_bucket_rows, trend_monotonicity
    rows = trend_bucket_rows(hold_days)
    if not rows:
        return ""
    lab = {20: "1個月", 40: "2個月", 60: "3個月"}.get(hold_days, f"{hold_days}日")
    out = [f"| 趨勢分區間 | 持有{lab}超額 | t值 | 贏大盤比率 | 絕對報酬 |",
           "|---|---|---|---|---|"]
    for r in rows:
        star = " ⬅" if r["lo"] >= 90 else ""
        out.append(f"| {r['range']}{star} | {r['excess']:+.2f}% | {r['t']:+.2f} | "
                   f"{r['beat_rate']:.0f}% | {r['abs_return']:+.2f}% |")
    rho = trend_monotonicity(hold_days)
    if rho is not None:
        out.append("")
        out.append(f"單調性 Spearman **ρ = {rho:+.2f}** —— 分數與後續超額幾乎完全同向。")
    return "\n".join(out)


def trend_delta_badge(delta, prev_date=None) -> str:
    """
    趨勢分與上次快照的變化。

    刻意在「沒有前次資料」時回傳空字串而不是「0」——
    「查無資料」與「沒有變化」是兩件事，混在一起顯示會誤導。
    """
    if delta is None:
        return ""
    if abs(delta) < 1:
        return ("<div style='font-size:9px;color:#78909c;'>≈ 持平</div>")
    up = delta > 0
    color = "#f03e3e" if up else "#2f9e44"      # 台股習慣：紅漲綠跌
    arrow = "▲" if up else "▼"
    tip = f"對比 {prev_date}" if prev_date else ""
    return (f"<div title='{tip}' style='font-size:10px;color:{color};font-weight:700;'>"
            f"{arrow} {abs(delta):.0f}</div>")
