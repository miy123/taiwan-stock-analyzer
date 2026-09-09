"""
預抓回測要用的歷史資券／估值，落地到 .histcache/。

一次抓好，之後所有回測都直接讀快取，不再打證交所 API——
這樣才不會像上次那樣被限流擋掉一半日期、還把「抓不到」誤判成「因子無效」。

用法：
    python3 prefetch_histdata.py --years 4          # 估值(每月) + 資券(每5交易日)
"""

import argparse
import sys
from datetime import date, timedelta

from services.histdata import (
    valuation_snapshot, margin_snapshot, cache_stats,
)


def month_ends(years):
    """每月取一個交易日（用該月 3 號附近，避開月初休假）。"""
    out, today = [], date.today()
    d = date(today.year - int(years), today.month, 1)
    while d < today:
        out.append(d.strftime("%Y%m%d"))
        d = date(d.year + (d.month == 12), (d.month % 12) + 1, 3)
    return out


def business_days(years, every):
    out, today = [], date.today()
    d = today - timedelta(days=int(years * 365))
    while d < today:
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d += timedelta(days=every)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=4.0)
    ap.add_argument("--every", type=int, default=7, help="資券每隔幾個日曆日抓一次")
    ap.add_argument("--skip-valuation", action="store_true")
    ap.add_argument("--skip-margin", action="store_true")
    args = ap.parse_args()

    if not args.skip_valuation:
        dates = month_ends(args.years)
        print(f"① 估值快照（每月，共 {len(dates)} 個日期）—— 證交所限流嚴，需慢慢來")
        ok = 0
        for i, d in enumerate(dates):
            snap = valuation_snapshot(d)
            if snap:
                ok += 1
            print(f"   {i+1}/{len(dates)} {d}: "
                  f"{'✅ ' + str(len(snap)) + ' 檔' if snap else '— 無資料/被擋'}",
                  flush=True)
        print(f"   估值成功 {ok}/{len(dates)}\n")

    if not args.skip_margin:
        dates = business_days(args.years, args.every)
        print(f"② 資券快照（共 {len(dates)} 個日期）")
        ok = 0
        for i, d in enumerate(dates):
            snap = margin_snapshot(d)
            if snap:
                ok += 1
            if i % 20 == 0 or snap:
                print(f"   {i+1}/{len(dates)} {d}: "
                      f"{'✅ ' + str(len(snap)) + ' 檔' if snap else '— 假日/無資料'}",
                      flush=True)
        print(f"   資券成功 {ok}/{len(dates)}\n")

    print("快取現況:", cache_stats())


if __name__ == "__main__":
    sys.exit(main())
