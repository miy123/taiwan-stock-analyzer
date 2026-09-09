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
)

HORIZON_ORDER = [("ultra_short", "極短"), ("short", "短"),
                 ("medium", "中"), ("long", "長")]


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


def evidence_badge(long_score, hold_days=20, min_width=150) -> str:
    """
    該檔長線分落在哪個實證區間 → 歷史勝率與超額報酬。

    ⚠️ long_score 必須傳**純技術**長線分（horizon_tech["long"]），
    因為實證門檻是在那個分數上量出來的。傳混合分會張冠李戴
    （台積電：純技術 94 vs 混合 81）——見 services/strategies._long。
    """
    if long_score is None:
        return f"<div style='min-width:{min_width}px;'></div>"
    b = (score_bucket_stats("長線+量能確認", long_score, hold_days)
         or score_bucket_stats("長線結構分", long_score, hold_days))
    if not b:
        return f"<div style='min-width:{min_width}px;'></div>"
    ok = b.get("excess", 0) > 0
    color = "#4caf50" if ok else "#f44336"
    return (
        f"<div style='min-width:{min_width}px;font-size:11px;'>"
        f"<div style='color:#78909c;'>長線分 {long_score} 實證區間 {b['range']}</div>"
        f"<div style='color:{color};font-weight:700;'>{'✅' if ok else '⚠️'} "
        f"勝率 {b.get('win_rate', 0):.0f}%　超額 {b.get('excess', 0):+.2f}%</div></div>"
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
    """統一的『幾分以上才值得買』說法，避免各頁講法不一致。"""
    thr = buy_threshold("長線+量能確認", hold_days)
    if not thr:
        return ""
    return (f"依 179 期回測，長線結構分 **低於 {thr:.0f} 分**的區間，"
            f"持有 1 個月的超額報酬全為負；**{thr:.0f} 分以上**才轉正。")


def long_threshold(hold_days=20, default=70.0) -> float:
    """三頁共用的買進門檻分數。"""
    return buy_threshold("長線+量能確認", hold_days) or default


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
