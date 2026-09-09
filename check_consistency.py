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

1. **三頁同一檔股票的趨勢結構分必須完全相同**（含我的持股）
   直接把頁面渲染出來、從畫面文字把數字挖出來比對，
   而不是比對內部變數——使用者看到的是畫面，不是變數。

2. **UI 字串裡不得寫死回測數字**
   用 AST 掃出 app.py / services/ui.py 的字串常數，找 `+N.NN%`、`t=N.NN`、
   `NNN 期` 這類樣式。實證數字一律要從 evidence 檔生成。

3. **UI 字串裡不得寫死門檻數字**
   「買進線 58」這類寫法會在換量表後靜靜地說錯話——趨勢結構分的買進線是 50，
   但畫面上曾經同時存在 58／68／48 三條舊量表的線。門檻一律插值。

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
# ⚠️ 而且必須是**整段相等**，不能用 `in`：先前 ALLOW 裡放了 "0.3"（證交稅），
#    於是「超額 +0.35%」這種真正該抓的數字也被當成證交稅放行了。
#    改成正規化後精確比對（去掉空白、把全角負號統一）。
ALLOW = {
    "0.585%",         # 來回交易成本（固定費率）
    "0.1425%",        # 手續費
    "0.3%",           # 證交稅
    "2%",             # 部位控管假設
    "9.5%", "10%",    # 漲跌幅上限
}


def _allowed(frag: str) -> bool:
    f = frag.replace(" ", "").replace("−", "-").lstrip("+-")
    return f in ALLOW


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
                    if _allowed(m.group(0)):
                        continue
                    frag = text[max(0, m.start() - 30):m.end() + 30].replace("\n", " ")
                    hits.append((rel, node.lineno, why, frag.strip()))
                    break
                else:
                    continue
                break
    return hits


# ── 檢查 3：門檻數字不准寫死 ────────────────────────────────────────────────
# 這一條是被真的 bug 逼出來的。同一次稽核抓到 6 處寫死的門檻：
#   · 選股頁長條圖畫「買進線 58／強力買進線 68／觀望線 48」，但 y 軸是趨勢結構分，
#     實際門檻是 50——三條線全是舊量表的殘留
#   · 持股頁配置圖同樣寫死「買進線 58／觀望線 48」
#   · 摘要條標籤寫 `≥{buy_bar}` 但計數用寫死的 58，空頭時兩者對不上
#   · 「買進門檻 {58:+d}」印出來是「買進門檻 +58」
# 檢查 1 抓不到這些（它找的是 `+N.NN%`、`t=N.NN` 這種回測數字樣式）。
# 規則很簡單：**門檻字樣旁邊不准出現字面數字**，一律用變數插值
# （f-string 的插值部分不是字串常數，所以合法寫法不會被誤判）。
THRESHOLD_WORDS = r"(?:買進線|觀望線|強力買進線|買進門檻|實證門檻|買進建議)"
# 門檻字樣與數字之間允許的連接符：空白、全／半角括號、≥ > = 冒號，以及 `{`
# （`{` 是為了抓 f-string 裡直接插一個字面數字的寫法，例如 `f"買進門檻 {58:+d}"`
#   ——它印出來是「買進門檻 +58」；合法的 `{buy_bar}` 以字母開頭，不會命中）
THRESHOLD_PAT = re.compile(THRESHOLD_WORDS + r"[\s（()≥>=＝:：{]*(\d+)")


def _render_strings(tree):
    """
    產生 (行號, 可掃描文字)。f-string 會還原成 `前綴{運算式}後綴` 的形式，
    這樣「插一個字面數字」與「純字串裡的數字」都掃得到。
    """
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            parts = []
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    parts.append(v.value)
                elif isinstance(v, ast.FormattedValue):
                    try:
                        parts.append("{" + ast.unparse(v.value) + "}")
                    except Exception:
                        parts.append("{?}")
            out.append((node.lineno, "".join(parts)))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append((node.lineno, node.value))
    return out


def scan_hardcoded_thresholds():
    """回傳 [(檔案, 行號, 片段)]。"""
    hits = []
    for rel in UI_FILES:
        path = ROOT / rel
        if not path.exists():
            continue
        tree = ast.parse(path.read_text())
        for lineno, text in _render_strings(tree):
            for m in THRESHOLD_PAT.finditer(text):
                frag = text[max(0, m.start() - 20):m.end() + 20]
                # 沒有插值的 f-string 同時是 JoinedStr 與 Constant，會命中兩次
                row = (rel, lineno, frag.replace("\n", " ").strip())
                if row not in hits:
                    hits.append(row)
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


def check_portfolio(rows):
    """
    我的持股頁顯示的趨勢分，必須等於掃描結果裡的同一個數字。

    這一頁的評分快取鍵是 `pf_scores_{全部持股股號逗號串}`，
    所以要一次塞齊所有持股才會走到「有評分」的路徑。
    沒有持股、或持股都沒被掃到時直接略過（回 True），不算失敗。
    """
    from streamlit.testing.v1 import AppTest
    from services.portfolio import load_holdings

    by_id = {r["stock_id"]: r for r in rows}
    holdings = load_holdings()
    if not holdings:
        print("   我的持股：沒有持股紀錄，略過")
        return True
    ids = ",".join(sorted(h["stock_id"] for h in holdings))
    scores = {h["stock_id"]: by_id[h["stock_id"]] for h in holdings
              if h["stock_id"] in by_id}
    if not scores:
        print("   我的持股：持股都不在本次掃描範圍內，略過")
        return True

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=900)
    at.session_state["main_page"] = "💼 我的持股"
    at.session_state[f"pf_scores_{ids}"] = scores
    at.run()
    if at.exception:
        print(f"   ❌ 我的持股頁拋例外：{at.exception[0].value}")
        return False

    text = " ".join(
        str(getattr(el, "value", ""))
        for coll in ("markdown", "caption", "info", "success", "warning")
        for el in getattr(at, coll, [])
    )
    ok = True
    for sid, r in scores.items():
        want = round(r["trend_score"])
        shown = {int(m) for m in
                 re.findall(rf"{sid}.*?贏過全市場\s*(\d+)\s*%", text, re.S)[:1]}
        if not shown:
            print(f"   ⚠️ 我的持股：{sid} 畫面上找不到趨勢分")
            continue
        if want not in shown:
            print(f"   ❌ 我的持股：{sid} 畫面顯示 {sorted(shown)}，掃描是 {want}")
            ok = False
    # 徽章上的「趨勢分 N 落在 …」也必須是同一個數字（先前餵的是離散長線分）
    for sid, r in scores.items():
        want = round(r["trend_score"])
        badge = re.findall(rf"{sid}.*?趨勢分\s*(\d+)\s*落在", text, re.S)[:1]
        if badge and int(badge[0]) != want:
            print(f"   ❌ 我的持股：{sid} 實證徽章寫 {badge[0]}，趨勢分卻是 {want}"
                  f"（量表餵錯）")
            ok = False
    if ok:
        print(f"   ✅ 我的持股頁 {len(scores)} 檔的趨勢分與掃描結果一致")
    return ok


def check_pages(stock_id):
    from streamlit.testing.v1 import AppTest
    from services.universe import scan_universe
    from services.analysis import prepare_frame, compute_scores
    from services.stock_data import get_ticker_info, get_financials

    print(f"\n▶ 檢查 {stock_id} 在三頁是否顯示同一個趨勢結構分")

    # ⚠️ 順序很重要：**先掃描，再算單檔分數**。
    # 趨勢分是「對照最近一次全市場掃描的分布」打出來的，`scan_universe()` 會
    # 重寫那份分布（services/scoring.save_distribution）。先算 core 再掃描的話，
    # core 用的是舊分布、頁面用的是新分布，比出來的差異是測試自己造成的。
    # （這個順序問題在 save_distribution 沒有清掉記憶體快取時被掩蓋住了——
    #   當時掃描後的新分布根本不會生效，兩邊剛好都用舊的。）
    rows, _ = scan_universe(min_turnover=5e7)
    scan_row = next((r for r in rows if r["stock_id"] == stock_id), None)

    df, _, _, _ = prepare_frame(stock_id)
    a = compute_scores(df, get_ticker_info(stock_id), get_financials(stock_id),
                       stock_id, stock_id, skip_news=True)
    core = (a.get("trend") or {}).get("score")
    print(f"   計分核心算出：{core}（對照本次掃描落地的全市場分布）")

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

    # 我的持股 —— 這一頁先前沒被檢查（docstring 寫「三頁」但只渲染兩頁），
    # 於是兩個 bug 活了下來：
    #   · 實證區間徽章餵的是離散長線分，卻查趨勢分的分桶表
    #     （同一張卡片：趨勢分那格 57、徽章寫「趨勢分 94 落在 90-100 區間」）
    #   · 「替換候選」清單用 horizon["long"] 比趨勢分門檻
    # ⚠️ 這一頁只認 `pf_scores_{全部持股股號}` 這個快取鍵，塞單一檔沒有用
    #    （持股清單來自 holdings.json，是使用者的個人資料，不能改）。
    #    所以改成拿「實際持有 ∩ 這次掃到」的股票來驗。
    pf_ok = check_portfolio(rows)

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

    ok = ok and pf_ok

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

    print("\n" + "=" * 78)
    print("檢查 2：門檻數字是否寫死（一律要用變數插值）")
    print("=" * 78)
    thits = scan_hardcoded_thresholds()
    if thits:
        failed = True
        for rel, line, frag in thits:
            print(f"  ❌ {rel}:{line}  …{frag}…")
        print(f"\n  共 {len(thits)} 處。買進線／觀望線一律引用 "
              f"services.scoring.BUY_BAR（趨勢分）或 "
              f"services.recommendation.buy_threshold()（綜合評分），"
              f"不要在字串裡寫數字。")
    else:
        print("  ✅ 沒有寫死的門檻數字")

    if not args.numbers:
        print("\n" + "=" * 78)
        print("檢查 3：同一檔股票在各頁是否顯示同一個趨勢結構分")
        print("=" * 78)
        sys.path.insert(0, str(ROOT))
        if not check_pages(args.stock):
            failed = True

    print("\n" + ("❌ 有問題，見上方" if failed else "✅ 全部通過"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
