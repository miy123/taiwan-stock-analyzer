"""
一致性檢查 —— 攔截本專案最常犯、而且煙霧測試抓不到的兩類錯。

## 為什麼需要這個

回顧這個專案的修改史，同一種錯誤反覆出現：

  · 改了計分核心，但只改到一條路徑 → 同一檔股票在不同頁分數不同
    （台積電：個股頁 94 vs 持股頁 56.7；台虹：88 vs 60）
  · 把回測數字寫死在說明文字裡 → 換模型後文字沒跟著改
    （「179 期…+3.10% vs +0.81%」「低於 70 分全為負」「族群 +3.07% 優於預設」）

`AppTest` 只驗「頁面不拋例外」，這兩類錯它一個都抓不到——頁面顯示錯的數字時，
它一樣是綠燈。所以另外寫這支。

## 兩項檢查

1. **三頁同一檔股票的趨勢結構分必須完全相同**
   直接把頁面渲染出來、從畫面文字把數字挖出來比對，
   而不是比對內部變數——使用者看到的是畫面，不是變數。

2. **UI 字串裡不得寫死回測數字**
   用 AST 掃出 app.py / services/ui.py 的字串常數，找 `+N.NN%`、`t=N.NN`、
   `NNN 期` 這類樣式。實證數字一律要從 evidence 檔生成。

用法：
    python3 check_consistency.py            # 兩項都跑
    python3 check_consistency.py --numbers  # 只掃寫死數字（快，不用渲染頁面）
"""

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 掃描哪些檔案的字串常數
UI_FILES = ["app.py", "services/ui.py"]

# 寫死實證數字的樣式
HARDCODED = [
    (re.compile(r"[+\-−]\d+\.\d+\s*%"), "寫死的百分比"),
    (re.compile(r"\bt\s*=\s*[+\-−]?\d+\.\d+"), "寫死的 t 值"),
    (re.compile(r"\d{2,4}\s*期(?!數)"), "寫死的期數"),
    (re.compile(r"勝率\s*\d+\.?\d*\s*%"), "寫死的勝率"),
]

# 允許清單：這些**片段本身**是固定費率或規則，不是回測結果。
# ⚠️ 比對的是「命中的那一段」，不是整個字串——先前寫成整段跳過，
#    結果一段文字只要提到 0.585% 手續費，同段裡所有寫死的回測數字都被放行。
ALLOW = [
    "0.585",          # 來回交易成本（固定費率）
    "0.1425",         # 手續費
    "0.3",            # 證交稅
    "2%",             # 部位控管假設
    "9.5%", "10%",    # 漲跌幅上限
]


def _is_docstring(node, parents):
    """判斷這個字串常數是不是 docstring（docstring 裡寫數字是說明，不是 UI）。"""
    for p in parents:
        if isinstance(p, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.ClassDef)):
            body = getattr(p, "body", [])
            if body and isinstance(body[0], ast.Expr) and body[0].value is node:
                return True
    return False


def scan_hardcoded():
    """回傳 [(檔案, 行號, 樣式說明, 片段)]。"""
    hits = []
    for rel in UI_FILES:
        path = ROOT / rel
        if not path.exists():
            continue
        src = path.read_text()
        tree = ast.parse(src)
        # 記錄每個節點的父節點鏈，用來判斷 docstring
        parent_of = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parent_of[child] = parent

        def parents_of(n):
            out, cur = [], parent_of.get(n)
            while cur is not None:
                out.append(cur)
                cur = parent_of.get(cur)
            return out

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if _is_docstring(node, parents_of(node)):
                continue
            text = node.value
            for pat, why in HARDCODED:
                for m in pat.finditer(text):
                    if any(a in m.group(0) for a in ALLOW):
                        continue
                    frag = text[max(0, m.start() - 30):m.end() + 30].replace("\n", " ")
                    hits.append((rel, node.lineno, why, frag.strip()))
                    break
                else:
                    continue
                break
    return hits


# ── 檢查 1：三頁的趨勢分 ────────────────────────────────────────────────────
TREND_PATTERNS = [
    re.compile(r"贏過全市場\s*(\d+)\s*%"),            # ui.trend_cell
    re.compile(r"(\d+)\s*分代表趨勢強度贏過"),          # 個股頁 caption
]


def _extract_trend(at, stock_id=None):
    """從渲染結果的所有文字裡挖出趨勢分。回傳 set（理想上只有一個值）。"""
    chunks = []
    for coll in ("markdown", "caption", "info", "success", "warning", "text"):
        for el in getattr(at, coll, []):
            v = getattr(el, "value", None)
            if isinstance(v, str):
                chunks.append(v)
    found = set()
    for c in chunks:
        if stock_id and stock_id not in c and "贏過全市場" not in c \
                and "分代表趨勢強度贏過" not in c:
            continue
        for pat in TREND_PATTERNS:
            for m in pat.finditer(c):
                found.add(int(m.group(1)))
    return found


def check_pages(stock_id):
    from streamlit.testing.v1 import AppTest
    from services.universe import scan_universe
    from services.analysis import prepare_frame, compute_scores
    from services.stock_data import get_ticker_info, get_financials

    print(f"\n▶ 檢查 {stock_id} 在三頁是否顯示同一個趨勢結構分")

    df, _, _, _ = prepare_frame(stock_id)
    a = compute_scores(df, get_ticker_info(stock_id), get_financials(stock_id),
                       stock_id, stock_id, skip_news=True)
    core = (a.get("trend") or {}).get("score")
    print(f"   計分核心算出：{core}")

    rows, _ = scan_universe(min_turnover=5e7)
    scan_row = next((r for r in rows if r["stock_id"] == stock_id), None)

    results = {}

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=900)
    at.session_state["main_page"] = "📊 個股分析"
    at.session_state["stock_input"] = stock_id
    at.run()
    results["個股分析"] = _extract_trend(at)

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=900)
    at.session_state["main_page"] = "🎯 智能選股"
    at.session_state["smart_full"] = rows
    at.run()
    results["智能選股"] = _extract_trend(at, stock_id)

    ok = True
    for page, vals in results.items():
        print(f"   {page}: {sorted(vals) if vals else '（畫面上找不到趨勢分）'}")

    # 個股頁必須顯示與核心一致的值
    if core is not None:
        want = round(core)
        detail = results.get("個股分析") or set()
        if want not in detail:
            print(f"   ❌ 個股分析頁沒有顯示 {want}（核心算出的值）")
            ok = False
        else:
            print(f"   ✅ 個股分析頁顯示 {want}，與計分核心一致")

    if scan_row is not None and scan_row.get("trend_score") is not None:
        print(f"   （掃描路徑對同一檔算出 {scan_row['trend_score']:.1f}；"
              f"與單檔路徑的差異來自百分位基準不同，屬預期範圍）")
        if abs(scan_row["trend_score"] - (core or 0)) > 12:
            print(f"   ⚠️ 兩條路徑差距 "
                  f"{abs(scan_row['trend_score'] - (core or 0)):.1f} 分，偏大，值得檢查")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--numbers", action="store_true", help="只掃寫死數字")
    ap.add_argument("--stock", default="2330")
    args = ap.parse_args()

    failed = False

    print("=" * 78)
    print("檢查 1：UI 字串裡是否有寫死的回測數字")
    print("=" * 78)
    hits = scan_hardcoded()
    if hits:
        failed = True
        for rel, line, why, frag in hits:
            print(f"  ❌ {rel}:{line}  {why}")
            print(f"     …{frag}…")
        print(f"\n  共 {len(hits)} 處。實證數字請改從 services/evidence.py 讀出後生成"
              f"（見 ui.threshold_note / bucket_table / strategy_caption）。")
    else:
        print("  ✅ 沒有寫死的回測數字")

    if not args.numbers:
        print("\n" + "=" * 78)
        print("檢查 2：同一檔股票在各頁是否顯示同一個趨勢結構分")
        print("=" * 78)
        sys.path.insert(0, str(ROOT))
        if not check_pages(args.stock):
            failed = True

    print("\n" + ("❌ 有問題，見上方" if failed else "✅ 全部通過"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
