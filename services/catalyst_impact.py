"""
Rule-based catalyst impact estimates.

These are industry-average empirical ranges, NOT stock-specific calculations.
They give directional guidance when a specific type of news is detected.
All figures are drawn from publicly cited academic event studies and
financial press analysis of Taiwan tech / financial stocks.
"""

IMPACT_TABLE = {
    "tech_breakthrough": {
        "title": "技術突破",
        "emoji": "🔬",
        "stock_reaction":  "宣布後 1–4 週平均 +5%~+15%；成功量產確認後通常有第二波",
        "revenue_horizon": "6–18 個月後開始貢獻營收（視量產進度）",
        "revenue_impact":  "長期潛力 +10%~+30% 相關業務營收",
        "margin_impact":   "若進入更高價值製程，毛利率可提升 +1%~+5%",
        "risk_note":       "量產良率不穩定是最大不確定性；宣布 ≠ 量產",
        "confidence":      "低（僅方向性估算，數字因公司規模差異極大）",
        "color":           "#7b61ff",
    },
    "new_order": {
        "title": "重大訂單",
        "emoji": "📦",
        "stock_reaction":  "宣布後 1–2 週平均 +3%~+8%",
        "revenue_horizon": "通常 1–3 季內反映於出貨營收",
        "revenue_impact":  "單季營收潛在增幅視訂單規模，大型合約可 +5%~+20%",
        "margin_impact":   "規模效益下，毛利率小幅改善 +0.5%~+2%",
        "risk_note":       "訂單可能被取消或延遲；出貨時間不確定",
        "confidence":      "中（需知訂單金額才能量化）",
        "color":           "#4caf50",
    },
    "strategic_partnership": {
        "title": "策略合作",
        "emoji": "🤝",
        "stock_reaction":  "宣布當日平均 +2%~+6%，後續視合作落地進度",
        "revenue_horizon": "財務貢獻通常 2–4 季後才顯現",
        "revenue_impact":  "協議細節未公開時難以量化；技術授權型可帶來穩定授權收入",
        "margin_impact":   "視合作內容，研發成本分攤可提升利潤率 +0.5%~+3%",
        "risk_note":       "合作深度因案而異；MOU ≠ 正式合約",
        "confidence":      "低",
        "color":           "#29b6f6",
    },
    "capacity_expansion": {
        "title": "擴產佈局",
        "emoji": "🏭",
        "stock_reaction":  "宣布當日通常 +1%~+4%；建廠期間折舊壓力可能壓低短期利潤",
        "revenue_horizon": "新產能通常 12–24 個月後才貢獻營收",
        "revenue_impact":  "視擴產幅度；擴產 30% 通常目標帶動長期營收 +15%~+25%",
        "margin_impact":   "建廠初期毛利率下滑 -1%~-3%（折舊壓力），滿載後回升",
        "risk_note":       "建廠期間景氣變化、資本支出超支是主要風險",
        "confidence":      "中",
        "color":           "#26a69a",
    },
    "legal_risk": {
        "title": "法律/監管風險",
        "emoji": "⚖️",
        "stock_reaction":  "宣布後通常 -5%~-20%（視嚴重性）",
        "revenue_horizon": "若涉及禁令或下架，影響立即顯現",
        "revenue_impact":  "若涉及核心業務，潛在影響 -10%~-40% 相關收入",
        "margin_impact":   "罰款、和解金直接衝擊單季淨利",
        "risk_note":       "訴訟結果不確定，最壞情境需考量倒閉或停業",
        "confidence":      "低（結果取決於司法程序）",
        "color":           "#ef5350",
    },
    "competition": {
        "title": "競爭壓力",
        "emoji": "🥊",
        "stock_reaction":  "競爭對手發布重大消息後，目標公司股價通常 -2%~-8%",
        "revenue_horizon": "轉單效果通常在 2–4 季後反映於財報",
        "revenue_impact":  "市佔率每下滑 1%，對主要業務營收影響約 -1%~-3%",
        "margin_impact":   "競爭導致降價，毛利率可能被壓縮 -1%~-5%",
        "risk_note":       "競爭格局可能持續惡化，需追蹤市佔率數據",
        "confidence":      "中",
        "color":           "#ff7043",
    },
    "operational_issue": {
        "title": "營運問題",
        "emoji": "⚠️",
        "stock_reaction":  "視事件嚴重性，通常 -3%~-15%",
        "revenue_horizon": "短期直接影響出貨；若涉及停工則立即衝擊",
        "revenue_impact":  "停工 1 個月約等於 損失 8% 季度營收",
        "margin_impact":   "修復成本與產能浪費壓縮利潤率",
        "risk_note":       "連鎖供應鏈影響難以預估",
        "confidence":      "中（停工天數 × 日產能是較可靠的估算基礎）",
        "color":           "#ffa726",
    },
}


def get_catalyst_impact(category_key: str) -> dict:
    """Return the impact table entry for a given category key, or None."""
    return IMPACT_TABLE.get(category_key)
