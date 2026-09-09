"""
Point-in-time 歷史資料層 —— 回測要「站在當天」只用當天拿得到的資料。

為什麼需要這一層：
  價量可以從 yfinance 一次抓完再切片，但**資券與本益比沒有歷史序列可切**，
  必須逐日向證交所要。之前 `backtest_research.get_hist_valuation()` 連打 179 個
  日期被限流，60 個裡有 43 個被擋，導致「抓不到資料」偽裝成「因子無效」，
  估值類結論整批作廢（見 CLAUDE.md）。

這裡的兩個對策：
  1. **落地磁碟快取**（`.histcache/`）—— 抓過的日期永久保存，重跑不再打 API。
  2. **本益比改用「季度 EPS + 每日股價」重建**，不逐日抓：
       EPS = 當日股價 ÷ 當日本益比       （用季度快照反推）
       任一天的本益比 = 當天股價 ÷ 最近一次已知的 EPS
     這樣 3 年只需要約 12~16 次 API 呼叫，而不是 150 次，限流問題自然消失。
     更重要的是：**它天生沒有前視偏誤**——真實世界的投資人在某一天能用的，
     本來就只有「最近一次公布的 EPS」加上「當天的股價」。
"""

import json
import os
import time
from pathlib import Path

import requests

CACHE_DIR = Path(__file__).resolve().parent.parent / ".histcache"
CACHE_DIR.mkdir(exist_ok=True)

_VAL_URL = ("https://www.twse.com.tw/exchangeReport/BWIBBU_d"
            "?response=json&date={d}&selectType=ALL")
_UA = {"User-Agent": "Mozilla/5.0"}


def _num(x):
    try:
        v = float(str(x).replace(",", "").strip())
        return v if v == v else None
    except Exception:
        return None


def _cache_path(kind, key):
    return CACHE_DIR / f"{kind}_{key}.json"


def _load(kind, key):
    p = _cache_path(kind, key)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return None


def _save(kind, key, data):
    try:
        _cache_path(kind, key).write_text(json.dumps(data))
    except Exception:
        pass


# ── 估值快照（季度）────────────────────────────────────────────────────────
def valuation_snapshot(date_str, retries=4, base_sleep=4.0):
    """
    {code: {pe, dy, pb}} 於指定日期。抓不到回 None（**不是空 dict**）——
    呼叫端才分得出「當天沒資料」與「被限流擋掉」，這正是上次踩雷的地方。
    """
    cached = _load("val", date_str)
    if cached is not None:
        return cached or None

    for attempt in range(retries):
        try:
            time.sleep(base_sleep * (1 + attempt))     # 4s → 8s → 12s → 16s
            j = requests.get(_VAL_URL.format(d=date_str),
                             timeout=30, headers=_UA).json()
        except ValueError:                              # 非 JSON = 被擋
            continue
        except Exception:
            continue
        if j.get("stat") == "OK" and j.get("data"):
            out = {}
            for row in j["data"]:
                if len(row) >= 7:
                    out[str(row[0]).strip()] = {
                        "pe": _num(row[5]), "dy": _num(row[3]), "pb": _num(row[6]),
                    }
            _save("val", date_str, out)
            return out
        return None                                     # 正常回應但當天無交易
    return None


def build_eps_timeline(snapshot_dates, close_on):
    """
    由季度估值快照反推每檔的 EPS 時間軸。

    close_on(code, date_str) -> 當日收盤價（呼叫端從已下載的歷史提供）。
    回傳 {code: [(date_str, eps), ...]}，日期由舊到新。

    EPS = 股價 ÷ 本益比。證交所的本益比用的是近四季 EPS，所以反推出來的
    就是「當時市場採用的近四季 EPS」——正是那一天投資人看得到的數字。
    """
    timeline = {}
    for d in sorted(snapshot_dates):
        snap = valuation_snapshot(d)
        if not snap:
            continue
        for code, v in snap.items():
            pe = v.get("pe")
            if not pe or pe <= 0:
                continue
            px = close_on(code, d)
            if not px:
                continue
            timeline.setdefault(code, []).append((d, px / pe))
    return timeline


def pe_from_timeline(timeline, code, date_str, price):
    """
    某日的本益比 = 當日股價 ÷ **該日之前**最近一次已知 EPS。

    嚴格只看 date_str 以前的快照，避免用到未來才公布的獲利。
    """
    hist = timeline.get(code)
    if not hist or not price:
        return None
    eps = None
    for d, e in hist:
        if d <= date_str:
            eps = e
        else:
            break
    if not eps or eps <= 0:
        return None
    return price / eps


# ── 資券快照（每個換股日）──────────────────────────────────────────────────
def margin_snapshot(date_str):
    """
    {code: {usage_pct, change_pct, short_ratio}} 於指定日期。

    short_ratio = 融券餘額 ÷ 融資餘額（券資比）。這個比率高代表空方壓力大，
    但也代表軋空題材——方向不預設，交給回測決定。
    """
    cached = _load("mgn", date_str)
    if cached is not None:
        return cached

    out = {}
    try:
        from services.margin import _fetch_twse_margin_table
        tbl = _fetch_twse_margin_table.__wrapped__(date_str)
        for code, rec in (tbl or {}).items():
            bal = rec.get("balance") or 0
            sht = rec.get("short_balance") or 0
            out[code] = {
                "usage_pct": rec.get("usage_pct"),
                "change_pct": rec.get("change_pct"),
                "short_ratio": (sht / bal * 100) if bal else None,
            }
    except Exception:
        pass
    if out:
        _save("mgn", date_str, out)
    return out


def cache_stats():
    """快取現況 —— 讓回測報表能誠實說明有多少資料是真的抓到的。"""
    v = list(CACHE_DIR.glob("val_*.json"))
    m = list(CACHE_DIR.glob("mgn_*.json"))
    return {"valuation_dates": len(v), "margin_dates": len(m),
            "dir": str(CACHE_DIR)}
