"""
趨勢結構分的歷史快照 —— 讓持股頁看得出「這檔最近是變強還是變弱」。

為什麼需要：
  分數只給當下的絕對值，看不出方向。一檔從 95 掉到 80 和一檔從 60 爬到 80，
  在畫面上長得一模一樣，但意義完全相反（前者轉弱、後者轉強）。

設計：
  · 每次持股頁重新評分就存一筆 {日期: {股號: 分數}}
  · 比較對象是**最近一個不同日期**的快照，不是「上一次評分」——
    同一天按五次重新評分不該產生五個比較基準
  · 檔案 `trend_history.json` 屬於個人資料，已列入 .gitignore
    （本專案已發生過個人持股被 commit 的事）
"""

import json
from datetime import date
from pathlib import Path

HISTORY_PATH = Path(__file__).resolve().parent.parent / "trend_history.json"
MAX_SNAPSHOTS = 120        # 約半年的交易日，夠用且不會無限長大


def _load() -> dict:
    try:
        d = json.loads(HISTORY_PATH.read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def record(scores: dict, on: str = None) -> None:
    """
    存一筆快照。scores = {股號: 趨勢分}。

    同一天重複呼叫會覆蓋當天那筆（而不是新增），這樣「最近一個不同日期」
    才會穩定指向真正的前一天。
    """
    scores = {k: float(v) for k, v in (scores or {}).items() if v is not None}
    if not scores:
        return
    hist = _load()
    hist[on or date.today().isoformat()] = scores
    for old in sorted(hist)[:-MAX_SNAPSHOTS]:
        hist.pop(old, None)
    try:
        HISTORY_PATH.write_text(json.dumps(hist, ensure_ascii=False))
    except Exception:
        pass


def previous(before: str = None) -> tuple:
    """
    回傳 (日期, {股號: 分數})——**最近一個早於今天的快照**。

    沒有歷史時回傳 (None, {})，呼叫端就不顯示變化，而不是顯示 0（假裝沒變）。
    """
    hist = _load()
    cutoff = before or date.today().isoformat()
    earlier = sorted(d for d in hist if d < cutoff)
    if not earlier:
        return None, {}
    return earlier[-1], hist[earlier[-1]]


def delta_for(stock_id: str, current, prev_scores: dict):
    """單檔的分數變化。缺任一邊就回 None（不要拿 0 當「沒變」）。"""
    if current is None:
        return None
    old = (prev_scores or {}).get(stock_id)
    if old is None:
        return None
    return float(current) - float(old)


def snapshot_count() -> int:
    return len(_load())
