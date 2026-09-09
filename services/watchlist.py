import json
import os

_WATCHLIST_FILE = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "watchlist.json")
)


def load_watchlist() -> list:
    """
    Return watchlist as [{"stock_id": "2330", "name": "台積電"}, ...], oldest-added first.

    ⚠️ 這個檔案是使用者可以手動編輯的，所以要跟 portfolio.load_holdings() 一樣
    先過濾格式：先前直接回傳整份 list，一筆缺 `name` 或不是 dict 的資料
    就會讓 `is_in_watchlist()` / `update_watchlist_name()` 拋 KeyError，
    而它們是在**側邊欄**呼叫的——整個 App 每一頁都會掛。
    """
    try:
        with open(_WATCHLIST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if isinstance(item, dict) and item.get("stock_id"):
            out.append({"stock_id": str(item["stock_id"]),
                        "name": item.get("name") or str(item["stock_id"])})
    return out


def _save_watchlist(items: list) -> None:
    with open(_WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def is_in_watchlist(stock_id: str) -> bool:
    return any(item["stock_id"] == stock_id for item in load_watchlist())


def add_to_watchlist(stock_id: str, name: str = "") -> None:
    items = load_watchlist()
    if not any(item["stock_id"] == stock_id for item in items):
        items.append({"stock_id": stock_id, "name": name or stock_id})
        _save_watchlist(items)


def remove_from_watchlist(stock_id: str) -> None:
    items = [item for item in load_watchlist() if item["stock_id"] != stock_id]
    _save_watchlist(items)


def update_watchlist_name(stock_id: str, name: str) -> None:
    """Refresh the display name of an already-watched stock (best-effort, no-op if absent)."""
    if not name:
        return
    items = load_watchlist()
    changed = False
    for item in items:
        if item["stock_id"] == stock_id and item["name"] != name:
            item["name"] = name
            changed = True
    if changed:
        _save_watchlist(items)
