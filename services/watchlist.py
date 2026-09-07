import json
import os

_WATCHLIST_FILE = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "watchlist.json")
)


def load_watchlist() -> list:
    """Return watchlist as [{"stock_id": "2330", "name": "台積電"}, ...], oldest-added first."""
    try:
        with open(_WATCHLIST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return []


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
