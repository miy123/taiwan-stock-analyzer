"""
財報快照累積 —— 讓「毛利率／每股淨值是不是越來越高」這種**趨勢**問題將來答得出來。

## 為什麼要自己累積

公開資訊觀測站的三張表（損益表 t187ap06、資產負債表 t187ap07、營益分析 t187ap17）
**都只給當期一個數字**，沒有任何前期欄位。月營收表有「去年同期」，所以營收年增做得
出來；毛利率與每股淨值沒有對應欄位，所以做不出來。

兩條路：
  (a) 自己每季存一份快照  ← 本檔
  (b) yfinance 逐檔季報（有 4~5 季歷史）

選 (a) 的理由有二：
  1. yfinance 每檔約 2.5 秒，全市場 1900 檔要跑一個多小時；本檔是**零成本**——
     掃描本來就已經抓了 `get_bulk_fundamentals()`，這裡只是把它寫下來。
  2. yfinance 是 **TTM** 窗口，官方是**當年度累計**。把兩種窗口混在同一個欄位
     正是本專案反覆踩過的坑（台泥 ROE 官方 +2.8% vs yfinance TTM −3.1%，
     連正負號都不一樣）。自己累積的快照與現用資料同源，不會有這個問題。

代價是**要等**：第一個趨勢要等下一季財報公布（約 2~3 個月）才會出現。
在那之前 `trend()` 一律回 `{}`，畫面必須明說「尚在累積」——
**不要拿 0 當「沒變化」**（持股頁的趨勢分變化早就是這個規矩）。

## point-in-time 正確

快照以**財報期別**（如 `115Q2`）為鍵，不是抓取日期。同一季重複抓只會覆蓋同一個
檔案，不會生出假的時間序列；而且每一筆都是「當時官方公布的數字」，
沒有前視偏誤。

## ⚠️ 無法回補

官方端點只給當期，所以**過去的期別補不回來**。想要更長的歷史只能從現在開始存。
這也表示任何用到這裡的因子短期內都**不可能回測**，畫面上要講清楚。
"""

import json
import re
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / ".histcache"
CACHE_DIR.mkdir(exist_ok=True)

_PREFIX = "fin_"
# 只存趨勢會用到的欄位。全部存下來會讓每季的檔案肥一倍，而 ROE／營收成長
# 本來就能從當期算出來，不需要歷史。
_KEEP = ("gross_margin", "bvps", "profit_margin", "roe", "debt_to_equity")


def _parse(period):
    """'115Q2' → (115, 2)；無法解析回 None（排序與 YoY 都靠它）。"""
    m = re.fullmatch(r"(\d+)Q(\d)", str(period or "").strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def _path(period):
    return CACHE_DIR / f"{_PREFIX}{period}.json"


def save_snapshot(bulk=None) -> dict:
    """
    把目前的批次財報依「期別」落地。回傳 {期別: 檔數}。

    bulk: `financials.get_bulk_fundamentals()` 的結果。呼叫端（掃描）手上
          本來就有，傳進來就不必再抓一次。

    同一季重複呼叫＝覆蓋同一個檔案，不會累積重複資料。
    不同公司可能落在不同期別（有人早報、有人晚報），所以依各自的 `period`
    分檔，而不是硬塞進同一個「本季」。
    """
    if bulk is None:
        from services.financials import get_bulk_fundamentals
        bulk = get_bulk_fundamentals()
    by_period = {}
    for code, rec in (bulk or {}).items():
        p = rec.get("period")
        if not _parse(p):
            continue
        slim = {k: rec[k] for k in _KEEP if rec.get(k) is not None}
        if slim:
            by_period.setdefault(p, {})[code] = slim

    out = {}
    for p, data in by_period.items():
        try:
            # 既有檔案先讀進來再更新：同一期別可能分兩次抓到（例如上櫃那批
            # 端點第一次瞬斷），直接覆寫會把先前存到的公司弄丟。
            old = {}
            if _path(p).exists():
                old = json.loads(_path(p).read_text())
            old.update(data)
            _path(p).write_text(json.dumps(old))
            out[p] = len(old)
        except Exception:
            continue
    return out


def periods() -> list:
    """已累積的期別，由舊到新。"""
    ps = []
    for f in CACHE_DIR.glob(f"{_PREFIX}*.json"):
        p = f.stem[len(_PREFIX):]
        if _parse(p):
            ps.append(p)
    return sorted(ps, key=_parse)


def _load(period) -> dict:
    try:
        return json.loads(_path(period).read_text())
    except Exception:
        return {}


def history(code) -> list:
    """[(期別, {欄位}), ...]，由舊到新。沒有資料回 []。"""
    out = []
    for p in periods():
        rec = _load(p).get(str(code))
        if rec:
            out.append((p, rec))
    return out


def trend(code) -> dict:
    """
    這一檔的財報趨勢。**資料不足就回 {}** —— 不要拿 0 當「沒變化」。

    回傳可能含：
      gm_delta_pp      毛利率相對**上一期**的變化（百分點）
      gm_yoy_pp        毛利率相對**去年同季**（百分點）
      bvps_delta_pct   每股淨值相對上一期（%）
      bvps_yoy_pct     每股淨值相對去年同季（%）
      prev / yoy       實際拿來比較的期別——畫面一定要標明「跟哪一期比」，
                       否則使用者不知道 +3pp 是一季還是一年的變化
      n_periods        目前累積了幾期

    ⚠️ 每股淨值用的是「每股」而非權益總額：現金增資會讓權益總額變大，
       那是稀釋不是變強（財務文獻裡「資產成長」本身是負向因子）。
    """
    hist = history(code)
    if len(hist) < 2:
        return {}
    cur_p, cur = hist[-1]
    prev_p, prev = hist[-2]
    out = {"prev": prev_p, "current": cur_p, "n_periods": len(hist)}

    def _delta_pp(a, b):
        return (a - b) * 100 if (a is not None and b is not None) else None

    def _growth_pct(a, b):
        return (a / b - 1) * 100 if (a is not None and b and b > 0) else None

    out["gm_delta_pp"] = _delta_pp(cur.get("gross_margin"), prev.get("gross_margin"))
    out["bvps_delta_pct"] = _growth_pct(cur.get("bvps"), prev.get("bvps"))

    # 去年同季 —— 財報有明顯季節性（Q4 通常最大），跟上一季比會把季節性
    # 誤讀成趨勢，所以只要湊得到同季就一併給。
    cp = _parse(cur_p)
    if cp:
        yoy_p = f"{cp[0] - 1}Q{cp[1]}"
        yoy = dict(hist).get(yoy_p)
        if yoy:
            out["yoy"] = yoy_p
            out["gm_yoy_pp"] = _delta_pp(cur.get("gross_margin"), yoy.get("gross_margin"))
            out["bvps_yoy_pct"] = _growth_pct(cur.get("bvps"), yoy.get("bvps"))
    return out


def stats() -> dict:
    """累積現況 —— 畫面要誠實說明「還在累積」時用。"""
    ps = periods()
    return {"periods": ps, "n_periods": len(ps),
            "latest": ps[-1] if ps else None,
            "codes_latest": len(_load(ps[-1])) if ps else 0}
