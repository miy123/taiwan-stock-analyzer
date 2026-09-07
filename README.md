# 📈 台灣股票分析系統

以 Streamlit 打造的台股分析工具，涵蓋**個股深度分析**與**全上市智能選股**：技術面、基本面、
消息面、目標價、量價關係、融資籌碼、風險控管，並支援**歷史回測**驗證建議準確度。

> ⚠️ 本系統僅供研究與參考，**不構成投資建議**。股市有風險，投資需謹慎。

---

## 🚀 快速開始（本機執行）

### 1. 前置需求

- **Python 3.9 以上**（macOS 內建的 `/usr/bin/python3` 即可）
- 可連網（需即時抓取 Yahoo Finance 與證交所資料）

確認 Python 版本：

```bash
python3 --version
```

### 2. 安裝套件

```bash
cd taiwan-stock-analyzer
python3 -m pip install -r requirements.txt
```

### 3. 啟動網站

```bash
python3 -m streamlit run app.py
```

啟動後終端機會顯示網址，瀏覽器通常會自動開啟：

```
  Local URL: http://localhost:8501
```

沒自動開啟就手動貼上 **http://localhost:8501**。

### 4. 停止網站

在終端機按 `Ctrl + C`。

---

## 🧯 常見問題

<details>
<summary><b>輸入 <code>streamlit</code> 顯示 command not found</b></summary>

用 `pip --user` 安裝時，執行檔會放在**沒有加進 PATH** 的目錄（macOS 通常是
`~/Library/Python/3.9/bin`）。有兩個解法：

**解法 A（推薦，免設定）** — 用模組方式啟動：

```bash
python3 -m streamlit run app.py
```

**解法 B** — 把該目錄加進 PATH（以 zsh 為例）：

```bash
echo 'export PATH="$HOME/Library/Python/3.9/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```
</details>

<details>
<summary><b>電腦裝了多個 Python，套件裝到別的版本去了</b></summary>

macOS 常同時有系統 Python（`/usr/bin/python3`）與 Homebrew Python
（`/opt/homebrew/bin/python3`），兩者的套件**互不相通**。請確保
「安裝」和「執行」用**同一個** Python：

```bash
# 查看目前有哪些 python3
which -a python3

# 指定同一個直譯器安裝並執行（範例：用系統 Python）
/usr/bin/python3 -m pip install -r requirements.txt
/usr/bin/python3 -m streamlit run app.py
```

想更乾淨可用虛擬環境：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```
</details>

<details>
<summary><b>出現 <code>NotOpenSSLWarning: urllib3 v2 only supports OpenSSL...</code></b></summary>

這是 macOS 內建 LibreSSL 的警告訊息，**不影響功能**，可直接忽略。
</details>

<details>
<summary><b>Port 8501 已被占用</b></summary>

```bash
python3 -m streamlit run app.py --server.port 8502
```
</details>

<details>
<summary><b>資料抓不到／顯示 N/A</b></summary>

資料來自 Yahoo Finance 與證交所的公開介面，可能因為**盤中未收盤、假日、
來源暫時不可用**而抓不到。稍後重試即可；系統對多數欄位都有容錯處理。
</details>

---

## 🧭 功能簡介

### 📊 個股分析

輸入**股票代碼或中文名稱**（例如 `2330` 或 `台積電`）即可分析：

| 分頁 | 內容 |
|------|------|
| 技術分析 | K線、均線、布林通道、MACD、RSI、KD、多尺度均量 |
| 新聞消息 | 依「事件類型」加權評分，標示尚未反映的催化劑 |
| 基本面 | P/E、ROE、營收獲利趨勢、財務結構 |
| 目標價位 | 五種估值方法加權，另抓法人目標價 |
| 投資建議 | 綜合評分＋**分時間週期建議**＋潛力面＋量價＋融資＋風險控管 |

**特色**

- **分時間週期建議**：極短線（1–3天）／短線（1週）／中線（1個月+）／長線（半年+）
  各自評分，同一檔常出現「短線偏空、長線偏多」的合理分歧
- **風險控管計畫**：以 ATR 換算**停損、停利、風報比 R:R、建議部位上限**
- **📅 回測模式**：側邊欄選過去日期，把分析還原到當天，並**事後驗證**當時建議準不準

> 📊 **選股模型已做過實證驗證** — 近3年139期滾動回測結果與重要結論見
> **[BACKTEST_FINDINGS.md](BACKTEST_FINDINGS.md)**。簡言之：**長線／中線趨勢分有效**，
> 而**「潛力潛伏／攻守兼備」歷史上顯著輸給「隨便買」**。App 內已直接標示各策略的實證判定。

### 🎯 智能選股

一次掃描、四種策略，切換策略**免重新掃描**：

| 策略 | 找什麼 |
|------|--------|
| 🚀 綜合強勢 | 趨勢已成、達買進門檻，順勢操作 |
| 🔥 漲停動能 | 近期連日漲停的高動能股 |
| 🌱 潛力潛伏 | **有題材、有成長，但股價還沒漲** |
| ⚖️ 攻守兼備 | 體質強又有餘裕，且風報比合理 |

**掃描範圍**

- 🌏 **全上市股票（完整）** — 對**每一檔**上市股實際計算技術面、量價、低基期位階、
  融資籌碼、估值、風報比（非抽樣粗篩），再對排名最前者補齊新聞與詳細財報
- ⭐ **熱門股池（快速）** — 只掃 31 檔熱門股，速度快

還可依**持有週期**排序、設定**流動性門檻**，並用 **🔍 策略回測**驗證選股邏輯：
回到過去某天跑同一套選股，計算 `選股超額報酬 = 前N名平均 − 全池平均`。

### 💼 我的持股

記錄**實際持有的部位**（股數＋成本價），把「你的部位」與「系統評分」放在一起看：

- **總覽**：持股檔數、總成本、總市值、**未實現損益**、持股平均評分與**市值加權評分**
  （加權分數才反映你的錢實際暴露在什麼品質的標的上）
- **持股排名**：依系統綜合評分由高到低排列，每檔顯示現價、損益金額／％、
  綜合評分、投資建議、潛力分、四週期評分、風報比與停損幅度
- **⚠️ 警訊提示**：自動標出「你持有、但評分已低於 48 分」的部位，提醒檢視減碼或停損
- **視覺化**：市值配置圓餅圖 ＋ 各持股評分長條圖（含買進線／觀望線）

新增方式有兩種：持股頁的表單，或在**個股分析頁**直接用「💼 加入我的持股」快速存入
（會自動帶入目前股價當預設成本價）。

### ⭐ 自選股

側邊欄可加入／移除自選股（只追蹤、不記錄部位），存於 `watchlist.json`。

---

## 📁 專案結構

```
taiwan-stock-analyzer/
├── app.py                      # Streamlit 主程式（UI 與頁面）
├── BACKTEST_FINDINGS.md        # ★ 選股模型實證結論（改邏輯前必讀）
├── backtest_research.py        # 多期滾動回測研究程式
├── backtest_results.json       # 回測結果（App 直接讀取顯示）
├── requirements.txt
├── .streamlit/config.toml      # 深色主題、預設 port
├── watchlist.json              # 自選股（自動產生）
├── holdings.json               # 我的持股（自動產生，含股數與成本）
└── services/
    ├── analysis.py             # ★ 共用計分核心（兩頁共用，確保結果一致）
    ├── universe.py             # 全上市批次掃描
    ├── stock_data.py           # 股價／財報／盤中報價
    ├── stock_lookup.py         # 中文名稱查詢與顯示
    ├── technical.py            # 技術指標、分週期評分、量價、風險計畫
    ├── fundamental.py          # 基本面評分
    ├── news.py                 # 新聞分類與情緒評分
    ├── target_price.py         # 目標價估值
    ├── analyst_targets.py      # 法人目標價擷取
    ├── margin.py               # 融資籌碼
    ├── market.py               # 大盤環境判讀
    ├── potential.py            # 潛力（潛伏股）評分
    ├── recommendation.py       # 建議生成與理由
    ├── evidence.py             # 回測實證資料載入（供 App 顯示策略勝率）
    ├── portfolio.py            # 持股儲存與損益計算
    ├── limit_up.py             # 漲停股偵測
    ├── catalyst_impact.py      # 催化劑影響估算
    ├── article_fetch.py        # 新聞內文擷取
    └── watchlist.py            # 自選股存取
```

---

## 🔌 資料來源

| 資料 | 來源 |
|------|------|
| 股價、財報、盤中報價 | Yahoo Finance（透過 `yfinance`） |
| 融資融券、全市場行情、本益比／殖利率 | 台灣證券交易所公開 API |
| 上市／上櫃公司中文名稱 | 證交所、櫃買中心 OpenAPI |
| 新聞 | Yahoo Finance + Google News RSS |

均為**免費公開資料**，無需 API Key。

### 已知限制

- 股價非官方即時報價，可能有數分鐘延遲（盤中會以分鐘線更新，快取 60 秒）
- 回測時技術面／量價／融資／大盤可還原到基準日，但**基本面與新聞仍為當前值**
  （免費資料源無歷史快照），存在**前視偏誤**，介面上已標示
- 目前全市場掃描僅涵蓋**上市**股票；上櫃需另接櫃買中心 API

---

## 📜 免責聲明

本系統的所有評分、建議與回測結果，皆為**公開資料的量化模型輸出**，
不代表任何投資建議，也無法預測技術突破、政策變化、市場黑天鵝等非線性事件。
請務必結合個人判斷與風險承受能力，自行為投資決策負責。
