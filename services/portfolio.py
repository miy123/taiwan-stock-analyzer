"""
持股（portfolio）儲存與損益計算。

與「自選股」的差別：自選股只是想追蹤的清單，持股則**實際持有**，因此需要記錄
股數與成本價，才能算出未實現損益，並把「系統評分」與「你的部位」放在一起看
——真正有用的是「我持有的東西，系統現在怎麼評價它」。

資料存在專案目錄的 holdings.json（純文字，可自行備份或編輯）。
"""

import json
import os
import datetime

_FILE = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "holdings.json")
)


def load_holdings() -> list:
    """[{stock_id, name, shares, cost, note, added}, ...] — oldest first."""
    try:
        with open(_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return [h for h in data if isinstance(h, dict) and h.get("stock_id")]
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return []


def _save(items: list) -> None:
    with open(_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def get_holding(stock_id: str) -> dict:
    for h in load_holdings():
        if h["stock_id"] == stock_id:
            return dict(h)
    return {}


def upsert_holding(stock_id: str, name: str, shares: float, cost: float, note: str = "") -> None:
    """Add a holding, or update shares/cost if it already exists."""
    items = load_holdings()
    for h in items:
        if h["stock_id"] == stock_id:
            h.update({"name": name or h.get("name") or stock_id,
                      "shares": float(shares), "cost": float(cost), "note": note})
            _save(items)
            return
    items.append({
        "stock_id": stock_id,
        "name": name or stock_id,
        "shares": float(shares),
        "cost": float(cost),
        "note": note,
        "added": datetime.date.today().isoformat(),
    })
    _save(items)


def remove_holding(stock_id: str) -> None:
    _save([h for h in load_holdings() if h["stock_id"] != stock_id])


def update_holding_name(stock_id: str, name: str) -> None:
    """Refresh the stored display name (best-effort)."""
    if not name:
        return
    items = load_holdings()
    changed = False
    for h in items:
        if h["stock_id"] == stock_id and h.get("name") != name:
            h["name"] = name
            changed = True
    if changed:
        _save(items)


def compute_position(holding: dict, current_price: float) -> dict:
    """Cost / market value / unrealised P&L for one holding."""
    shares = float(holding.get("shares") or 0)
    cost = float(holding.get("cost") or 0)
    cost_value = shares * cost
    market_value = shares * (current_price or 0)
    pnl = market_value - cost_value
    pnl_pct = (pnl / cost_value * 100) if cost_value else None
    return {
        "shares": shares,
        "cost": cost,
        "cost_value": cost_value,
        "market_value": market_value,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
    }


def portfolio_totals(positions: list) -> dict:
    """Aggregate totals across positions (each from compute_position)."""
    cost_value = sum(p["cost_value"] for p in positions)
    market_value = sum(p["market_value"] for p in positions)
    pnl = market_value - cost_value
    return {
        "cost_value": cost_value,
        "market_value": market_value,
        "pnl": pnl,
        "pnl_pct": (pnl / cost_value * 100) if cost_value else None,
        "count": len(positions),
    }
