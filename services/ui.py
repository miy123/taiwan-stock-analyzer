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
    horizon_efficacy, score_bucket_stats, buy_threshold,
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
    四格週期分數，附實證效力標記（✅有效／🟡偏弱／🔴雜訊）。

    selected_key: 目前排序依據的週期會加框，讓使用者知道該看哪一格。
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


def long_threshold(hold_days=20, default=None) -> float:
    """三頁共用的買進門檻分數（趨勢分量表）。"""
    from services.scoring import BUY_BAR
    return trend_threshold(hold_days) or default or BUY_BAR


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
    卡片上每個數字是什麼、哪個能拿來選股 —— 三頁共用同一份說明。

    使用者實際問過「這些分數各自代表啥、哪個比較能當選股指標」，
    表示光靠標籤不夠。這裡直接把答案寫在頁面上。
    """
    return """
| 卡片上的數字 | 代表什麼 | 能當選股指標嗎 |
|---|---|---|
| **趨勢結構分** ★ | 距季線／均線排列／季線斜率在**當日全市場的百分位**。80 分＝趨勢強度贏過八成股票 | ✅ **就是用它**。139期回測持有3個月超額 **+7.18%**、t=5.52、贏大盤 66% |
| 綜合評分 | 技術40%＋基本面30%＋消息15%＋目標價15% 的體質總覽 | 🟡 弱。超額僅 +0.40%、t=1.06（**不顯著**）。當健檢用，不要拿來排序 |
| 潛力分 | 低基期×題材，找「還沒漲的」 | ⛔ 多頭失效（−0.98%），只有空頭轉正（+2.23%） |
| 極短／短／中 週期評分 | 各持有期的技術強弱（1–3天／1週／1個月） | 🟡 越短越弱：中線 +1.90%、短線 +1.29%、極短 +0.23%（不顯著） |
| 本益比 | 估值 | ⛔ **單獨用會虧錢**：買最低本益比持有3個月超額 −4.48%、t=−3.82 |
| 融資使用率 | 籌碼風險 | ⛔ 無選股訊號（t=−0.14），只當**風險警示**用 |

**結論：選股看「趨勢結構分」，其他都是背景資訊。**
但它是純技術的**相對排名**，不看貴不貴——高分常常正是因為已經漲很多，
請搭配 🔥 過熱警示與本益比一起看。
"""


def bucket_table(hold_days=60) -> str:
    """分數區間 → 後續表現的表格（由實證檔生成，畫面上不寫死任何數字）。"""
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
