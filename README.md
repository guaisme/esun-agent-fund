# ESUN AI Agent ETF Competition 2026

第一階段是使用 **Google ADK + Gemini AI Studio + ToAlpha Streamable HTTP MCP** 的台灣股票 Research Agent。
Research Agent 仍只負責市場、月營收與法人研究。Phase 3–7 在獨立模組加入模擬帳務、時點安全因子、成本檢查、Investment Agent 與 D-Plan 本地生成；沒有券商交易或競賽自動提交。

技術驗收見 [驗證狀態](docs/validation_status.md)。詳細金融驗收保留本機 reports/phase3_7_acceptance.md，不發布GitHub。正式 competition-ready 狀態為 **BLOCKED**，請勿將成功產生草稿等同正式驗收。

## Windows 11 / Conda 啟動

在 Anaconda PowerShell Prompt 執行。已有 Python 3.11 環境時直接 activate 該環境，不須重新建立或修改 Conda 全域設定。

```powershell
git clone https://github.com/guaisme/esun-agent-fund.git
cd esun-agent-fund
git switch feature/phase3-7-integrated
conda create -n esun-agent-fund python=3.11 -y
conda activate esun-agent-fund
python --version
python -m pip install -r requirements.txt
if (-not (Test-Path research_agent/.env)) { Copy-Item research_agent/.env.example research_agent/.env }
notepad research_agent/.env
```

在 `.env` 中將 `YOUR_API_KEY_HERE` 換成 [Google AI Studio](https://aistudio.google.com/apikey) 的 Gemini API Key。
不要貼到程式、日誌、測試報告或 GitHub。`.env` 已被 Git 忽略；`.env.example` 只有佔位符。
既有 shell 環境變數優先於 `.env`；變更後重新啟動 Web。

若 Gemini 回傳 HTTP 404，代表模型不存在或帳號無法使用。2026-10-11 實際驗證中，
此帳號的 `gemini-2.5-flash` 回傳「不再提供給新使用者」；改用 `GEMINI_MODEL=gemini-3.8-flash` 後 API 與自主 MCP 工具呼叫成功。
預設值仍依第一階段原始需求保留 gemini-2.5-flash；請以帳號當下可用模型設定 `.env`，不要將模型存取錯誤解讀成 MCP 網路問題。
模型生命週期參考 [Google 官方文件](https://ai.google.dev/gemini-api/docs/deprecations/)。

```dotenv
GOOGLE_API_KEY=YOUR_API_KEY_HERE
GOOGLE_GENAI_USE_VERTEXAI=FALSE
GEMINI_MODEL=gemini-2.5-flash
TOALPHA_MCP_URL=https://toalpha.tw/mcp
```

在 **repository 根目錄** 啟動：

```powershell
adk web
```

開啟 <http://localhost:8000>，選擇 `research_agent`，輸入中文研究問題。`research_agent/__init__.py` 匯出 agent 模組，`agent.py` 提供 ADK 的 `root_agent`。
執行不需要 Codex CLI、VS Code 擴充或任何 Codex 套件。

## 套件與檔案

目標 Python **3.11**；程式使用官方 SDK，requirements 固定直接依賴：

| 套件 | 版本 | 用途 |
| --- | --- | --- |
| google-adk[mcp] | 2.11.0 | LlmAgent、Gemini、McpToolset、Web UI |
| google-genai | 2.29.0 | 官方 Gemini client |
| mcp | 2.3.0 | 官方 MCP Python SDK |
| python-dotenv | 1.2.4 | 讀取 agent 旁的 `.env` |
| pytest | 9.1.1 | 測試 |
| requests | 2.34.2 | 官方歷史 API 的依序 HTTP client |
| pyarrow | 26.0.0 | Parquet 讀寫 |

Gemini client 與 MCP Python SDK 亦固定為已驗證版本，其他間接依賴由套件相依規則解析。非同步測試使用 Python asyncio.run，不需要額外 pytest plugin。

```text
research_agent/__init__.py
research_agent/agent.py
research_agent/.env.example
tests/test_research_agent.py
requirements.txt
.gitignore
README.md
```

## ToAlpha MCP 與研究規則

使用 [ToAlpha 官方文件](https://toalpha.tw/mcp-docs) 與 [ADK 官方 MCP 文件](https://adk.dev/tools-custom/mcp-tools/)。
連線是 `StreamableHTTPConnectionParams`，HTTP timeout 15 秒、SSE read timeout 30 秒；工具探索整體最多 45 秒。
Gemini 每次請求預設 60 秒，SDK attempts=1；Phase 2 應用層最多重試三次，整輪研究預設 240 秒總時限。
終端會顯示 `Gemini request start` 與 `Gemini response received`，方便區分模型等待與 MCP 等待。
僅暴露以下五個工具；schema 從服務即時取得，不在程式手動重建：

| 工具 | 文件中的參數 |
| --- | --- |
| resolve_stock | query |
| market_overview | 無 |
| monthly_revenue | stock_id、months |
| institutional_flows | stock_id、days |
| data_status | 無 |

若文件與實際 `tools/list` 不同，以 live schema 為準；五個必要名稱缺少任何一個時拒絕探索結果。
這五個公開資料工具不需要 ToAlpha 金鑰；Gemini 金鑰不會傳送到 MCP。

Agent 自主規劃工具：先確認資料更新狀態，個股先解析名稱，再取得對應資料。
每輪重置資料證據，若未成功取得市場、營收或法人資料，程式會替換最終回答為資料不足提示。
每輪模型規劃最多八次。工具錯誤回傳明確失敗訊息，API 錯誤回傳設定／額度／網路檢查提示。
日誌只記工具名稱、成功接收或失敗、錯誤類型；不記參數或金融回應內容。LogRecord factory 遮罩目前的 API Key 與常見 key 表示，包括 exception 日誌。
請保持預設 INFO；ADK Web 的 session／event trace 可能保存問題與工具內容，避免在問題中輸入秘密。

回答需列資料來源、連結、日期和單位，區分客觀事實與推論。沒有資料不能補值。
程式保守拒絕包含未來 ISO 月／日期的整份工具結果；季別、公告可知時間及指定歷史截止日期另由研究指示檢查。
ToAlpha 研究工具不保證歷史可知時間；Phase 2 本地資料層另外實作 point-in-time 過濾，不能混用。

## 測試

### 離線 mock 單元測試

```powershell
python -m pytest -q
```

測試使用真實 ADK 類別，網路失敗情境使用 mock，不使用 Gemini 或真實金融資料。
涵蓋 import、agent/model 設定、MCP URL／timeout／allowlist、日誌金鑰遮罩、工具探索／呼叫／API 錯誤、每輪工具證據、未來資料拒絕與無 Codex runtime 依賴。
live test 預設 skip，不能將 mock 通過解讀為實際服務成功。

### 真實 MCP 整合測試（不需要 Gemini API Key）

```powershell
$env:RUN_TOALPHA_INTEGRATION = "1"
python -m pytest -q -s tests/test_research_agent.py -k real_toalpha
Remove-Item Env:RUN_TOALPHA_INTEGRATION
```

此測試直接連線 `https://toalpha.tw/mcp`，執行 initialize、tools/list，印出五個工具的 live schemas，
再真正呼叫 data_status、resolve_stock、2330 月營收、market_overview、2454 法人資料。輸出為服務原始結果，不是合成金融資料。
需可連外，會使用 ToAlpha 額度。資料內容仍須檢查 source／as_of，不應只看 HTTP 200。

### 真實 Gemini → Agent → MCP 手動驗收

設定有效 Gemini API Key，啟動 `adk web`，分別建立新 session 輸入：

1. 「請使用工具查詢台積電 2330 最新月營收。」
2. 「請查詢目前台股市場概況。」
3. 「請查詢聯發科 2454 最近的三大法人買賣超。」

成功需在 ADK event trace 中看到真正 function_call 及對應 function_response，終端也需看到 `MCP call tool=...` 和 `MCP result ... status=received`。
問題一必須看到 monthly_revenue，問題二 market_overview，問題三 institutional_flows；另檢查來源、資料日期、單位與繁體中文。
只有模型文字回答，或只呼叫 resolve_stock/data_status，不算完成研究驗收。
金鑰不在 repository；Phase 2 已新增可選的自動 Live E2E，實際結果見 reports/phase2_acceptance.md。

## 常見錯誤

| 現象 | 處理 |
| --- | --- |
| adk 找不到 | activate 正確 Conda 環境，再安裝 requirements；用 `Get-Command adk` 確認 |
| Web 清單沒有 research_agent | 在 repository 根目錄執行；確認 init/agent.py 存在 |
| 未設定 GOOGLE_API_KEY | 編輯 research_agent/.env；檢查 shell 是否有舊值覆蓋，重啟 |
| Gemini API 401/403 | 檢查 AI Studio 金鑰與模型存取權；不要改成 Vertex AI |
| Gemini API 404 | 將 .env 的 GEMINI_MODEL 改為帳號可用模型；本次 gemini-3.8-flash 實測成功 |
| Gemini API 429 | 檢查 Gemini 配額，等候後重試 |
| MCP 探索或呼叫失敗 | 檢查網路、防火牆／Proxy、ToAlpha 可用性；執行 live test |
| ToAlpha 429 | 等候供應商額度恢復，不無限重試 |
| 缺工具或參數變更 | 查看 live tools/list schema，對照官方文件後更新 |
| 本輪未取得資料 | 查看 event trace：模型是否實際呼叫資料工具、是否錯誤或空資料 |
| 未來日期被拒絕 | 檢查供應商日期與本機時鐘；不以拒絕結果作數值結論 |

## 第一階段原訂後續方向

本階段驗收後，再規劃帶公告時間的資料品質檢查與研究評估集；ETF 研究擴充、策略、回測與組合功能須另外定義範圍，本次沒有實作。

## 第一階段驗證歷史（保留紀錄；Phase 2 結果以驗收報告為準）

環境：Windows、既有 Conda `esun-agent`、Python 3.11.17。沒有修改 Conda 全域設定。

| 檢查 | 實際結果 |
| --- | --- |
| `python -m pytest -q` | 加入 Gemini timeout 後 15 passed、1 skipped；live test 預設 skip |
| `RUN_TOALPHA_INTEGRATION=1` 的 real_toalpha test | 1 passed；tools/list、五個工具真正呼叫、ADK toolset allowlist 探索通過 |
| ADK Web | CLI 伺服器啟動成功；TestClient 驗證首頁 200、list-apps 包含 research_agent，AgentLoader 成功載入 |
| `.env` 忽略與 diff 檢查 | 通過 |
| Gemini → Agent → MCP 三題手動驗收 | 尚未執行，需要本機有效 Gemini API Key |

後續問題診斷已使用本機 `.env` 做一次真實市場概況查詢：gemini-2.5-flash 回傳 HTTP 404；gemini-3.8-flash 成功自主呼叫 data_status 與 market_overview。
三題完整手動驗收仍未全部完成。程式已修正 API 錯誤被資料不足保護訊息覆蓋的問題，並加入 404／429 等安全提示與回歸測試。
金鑰遮罩保留 Uvicorn access log 的五個參數及狀態碼型別，避免 `expected 5, got 0`；另有實際 AccessFormatter 回歸測試。
後續 UI 查詢約 13 秒完成月營收與法人資料工具回應，但未收到最終模型回答，使用者在約 242 秒時停止；新增 Gemini HTTP timeout 與錯誤提示，為等待增加請求層級限制。

即時營收回應期別為 2026-09，市場及法人資料日期為 2026-10-08；只代表供應商這次實際回應，沒有獨立重新核算金融數值。
ADK/MCP 套件有 experimental／deprecation warnings，測試未失敗。這些不等於 Gemini 端到端驗收已完成。
CLI 啟動確認之外，瀏覽器 localhost 存取仍需使用者本機驗證；執行環境跨命令的 loopback 請求逾時，因此用同程序 TestClient 驗證 HTTP 路由。


## Phase 2：資料基礎設施與 Agent 可靠性

新增模組獨立於 `research_agent`，不替換已運作的 Agent。資料下載無須 Gemini、MCP 或 Codex CLI；不含策略、因子、交易、回測引擎、配置最佳化及 D-Plan 生成。Phase 2 分支以尚未合併的第一階段分支為基底；先合併第一階段 PR，再合併第二階段 PR。

```text
config/
  universe.csv                  # 官方 PDF 的 150 檔，非 API 動態名單
  universe_manifest.json        # 官方 PDF SHA256、頁數與核對名單
src/data/
  universe.py                   # UniverseLoader：100 TWSE + 50 TPEx
  providers/
    base.py                     # BaseProvider 與單位、來源正規化
    twse.py                     # 官方全市場每日行情與三大法人
    tpex.py                     # 官方全市場每日行情與三大法人
    finmind.py                  # 明確啟用後的行情備援、月營收
    institutional.py            # InstitutionalFlowProvider
  http.py                       # 依序請求、限速、重試、原始 JSON 封存
  storage.py                    # SQLite catalog + immutable Parquet
  time_safety.py                # 含時區的 point-in-time 過濾
  quality.py                    # ERROR / WARNING / INFO 報告
  download.py                   # 批次下載、續傳、檢查點
  cli.py                        # python -m src.data.cli
research_agent/reliability.py   # Gemini/MCP timeout、重試、整輪 deadline
pytest.ini
tests/test_data.py              # 離線，MOCK 資料只在 pytest 暫存目錄
tests/test_live.py              # opt-in Integration / Live E2E
reports/phase2_acceptance.md     # 實際驗收、未完成事項
```

### 官方股票名單

來源為使用者提供的 `玉山挑戰賽_投資組合及交易標的_150檔清單_v1.pdf`，已抽取並逐頁核對三頁。CSV 必須符合 manifest 的代號、名稱、市場，不以當下 API 股票名單改寫官方名單。更新官方文件時須重新核對、更新 manifest；SHA256 是來源追溯值，不是主辦方數位簽章。

```powershell
python -m src.data.cli universe
```

`UniverseLoader` 提供 `load_universe()`、`get_all_tickers()`、`get_twse_tickers()`、`get_tpex_tickers()`、`is_valid_ticker(ticker)`、`get_stock_info(ticker)`。總數、市場數、四位數字、重複、名稱空白及官方名單差異皆拋出 `UniverseError`。官方 PDF 沒有提交到 Git；名單來源紀錄在 manifest。

### Provider、單位與來源

| 資料 | API 與參數 | 實際格式／單位 |
| --- | --- | --- |
| TWSE 每日行情 | `https://www.twse.com.tw/exchangeReport/MI_INDEX`，`date=YYYYMMDD&type=ALLBUT0999&response=json` | 在 tables 中依欄名選「證券代號／開盤價」表；成交股數為股，成交金額為元 |
| TPEx 每日行情 | `https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes`，`date=YYYY/MM/DD&id=&response=json` | tables[0]；成交股數為股，成交金額(元)為元；另保留官方均價 |
| TWSE 法人 | `https://www.twse.com.tw/fund/T86`，`date=YYYYMMDD&selectType=ALLBUT0999&response=json` | fields/data；買賣超股數；外資欄排除外資自營商 |
| TPEx 法人 | `https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade`，`date=YYYY/MM/DD&type=Daily&response=json` | 24 欄，七組買／賣／淨股數；外資不含外資自營商、投信、自營商合計分別取第 5／14／23 欄（1 起算） |
| FinMind 行情 | `https://api.finmindtrade.com/api/v4/data`，dataset=`TaiwanStockPrice` | Trading_Volume=股，Trading_money=元；Trading_turnover 是成交筆數，不能當成交金額 |
| FinMind 月營收 | 同上，dataset=`TaiwanStockMonthRevenue` | revenue=元；revenue_year/month 為期別；date/create_time 不能當實際公布時間 |

以實際 API 欄名、資料日期與列寬檢查 schema。TPEx 法人回應欄名有重複，分組位置依現行官方結構，尚未建立跨版本 header group 自動辨識；schema 變動須再核對官方頁面，不宜只依 HTTP 200 判斷。

這版批次行情用「每日期、每市場一筆請求」，不逐一重抓 150 檔。未使用 TPEx 月行情的「張／仟元」欄位，避免其四捨五入股數影響 VWAP。`vwap = turnover_TWD / volume_shares`，標記計算方法；`provider_average` 另外保存。兩者尚未核對主辦方的成交均價公式，不能直接當競賽成交價；未用 adjusted_close 替代價格。

TWSE/TPEx 原始資料取得優先。只有 `$env:FINMIND_ENABLED = "1"` 才能啟用 FinMind，token 使用 Authorization header，不保存到 raw JSON。下載 CLI 不讀取研究 Agent 的 `.env`，請在 PowerShell 設定 FinMind 環境變數。未啟用時，所有來源失敗便記錄失敗，不填股價。法人僅用官方資料；月營收目前僅實作 FinMind adapter。來源授權、商業／競賽再利用與 API 額度須按供應商當下條款確認，沒有授權豁免。

參考：[TWSE 歷史行情](https://www.twse.com.tw/zh/trading/historical/stock-day.html)、[TPEx 行情](https://www.tpex.org.tw/zh-tw/mainboard/trading/info/stock-pricing.html)、[FinMind 資料目錄](https://finmind.github.io/tutor/TaiwanMarket/DataList/)、[FinMind 月營收](https://finmind.github.io/tutor/TaiwanMarket/Fundamental/)、[ToAlpha 文件](https://toalpha.tw/mcp-docs)。ToAlpha 只供研究工具使用，不作批量歷史匯出來源。

### Windows PowerShell 操作

已有 `esun-agent` 環境可直接執行，不必重建：

```powershell
conda activate esun-agent
cd C:\Users\User\esun-agent-fund\esun-agent-fund  # 換成你自己的 repository 路徑
 git fetch origin
 git switch feature/phase2-market-data
python -m pip install -r requirements.txt
python -m src.data.cli universe

# 指定單股；只允許官方 CSV 股票
python -m src.data.cli download --ticker 2330 --start 2026-03-01 --end 2026-10-08

# 省略 ticker 即官方 150 檔；按相同區間重跑即可續傳
python -m src.data.cli download --start 2026-03-01 --end 2026-10-08 --output data/metadata/download-result.json

# 更新新日期；--refresh 才重新請求已封存的同日期資料
python -m src.data.cli download --start 2026-10-08 --end 2026-10-09 --refresh

# 法人：預設同樣支援全部 150 檔；亦可 --ticker 2454
python -m src.data.cli flows --ticker 2454 --start 2026-09-28 --end 2026-10-08

# 確認 FinMind 授權／額度後才啟用；token 若需要請自行設定，勿貼日誌
$env:FINMIND_ENABLED = "1"
python -m src.data.cli revenue --ticker 2330 --start 2026-01-01 --end 2026-10-08
Remove-Item Env:FINMIND_ENABLED

# 查詢本地 SQLite catalog 指向的 Parquet
python -m src.data.cli query --ticker 2330 --start 2026-09-01 --end 2026-09-30 --output data/metadata/2330-query.json
python -m src.data.cli query --dataset flows --ticker 2454 --start 2026-09-28 --end 2026-10-08
python -m src.data.cli query --ticker 2330 --as-of "2025-03-10T08:30:00+08:00"

# 資料品質；完整報告保存本機（大量內容不提交 Git）
python -m src.data.cli quality --start 2026-03-01 --end 2026-10-08 --min-days 120 --output data/metadata/quality-prices.json
python -m src.data.cli quality --dataset flows --start 2026-09-28 --end 2026-10-08 --min-days 5 --output data/metadata/quality-flows.json
python -m src.data.cli quality --dataset revenue --min-days 1 --output data/metadata/quality-revenue.json
```

下載 `--start/--end` 以 ISO 日期提供，不接受未來日期。預設間隔 2 秒、timeout 20 秒、最多重試 3 次（含首次共 4 次），可用 `--interval`、`--timeout`、`--max-retries` 調整，請遵守來源限速；沒有大量平行請求。401/403/schema 錯誤不無限重試。API 429/5xx/網路 timeout 使用 exponential backoff + jitter，會記錄類型與狀態碼，不記敏感 exception 內容。退出碼：0=操作無已偵測錯誤；1=設定／來源錯誤；2=已記錄下載失敗、品質錯誤或不完整品質報告；0 不代表所有金融資料已獨立驗收。

`Copy-Item` 僅適用 PowerShell。若 Anaconda Prompt 顯示 `(esun-agent) C:\...>`，通常是 cmd：請用 `copy research_agent\.env.example research_agent\.env`，且不要覆寫已設定金鑰的檔案。

### SQLite / Parquet 與增量更新

預設 `data/raw/` 保存原始 JSON envelope（URL、公開參數、fetched_at、immutable snapshot）；`data/processed/{prices,flows,revenue}/` 保存不可變 Parquet chunk；`data/metadata/catalog.sqlite` 保存官方名單、來源、timestamp、revision 指紋、更新檢查點、觀察到的市場交易日與失敗紀錄。三個目錄、`.env`、SQLite、Parquet 已列入 `.gitignore`，不提交大量金融資料。

相同 dataset/ticker/observation/content 指紋不重複儲存；修改數值會另建 revision，原紀錄保留。查詢預設取最新 revision。先做 PIT 過濾再選 revision，避免未來修訂覆蓋歷史版本。SQLite transaction 序列化 Parquet 寫入；chunk 原子發布後才 commit catalog。中斷可能留下未入 catalog 的孤立 chunk，查詢不會讀取；重跑可恢復。不要手動刪除 catalog 指向的 Parquet；暫不提供壓縮／清理工具或多程序大量併發下載。

完整市場日檢查點可跳過；單股下載不會冒充全市場完成。空 API 回應只是 `empty_unverified`，不是已驗證休市；下次欲確認供應商更新必須 `--refresh`。原始回應預設長期 cache，重跑不會重新下載相同資料；同一進程也只抓一次同樣的日市場。若股價停牌／無成交／上市較晚造成缺日，報告列出缺漏，不自動補前值或零。

### Point-in-time 與資料品質

* `observation_date`：行情／法人交易日、月營收所屬月份月底；只是觀察期別，不是可用日期。
* `published_at`：只有來源能證實實際公布時間才填入，現行 adapters 保留 null。
* `available_at`：此版以首次實際保存 snapshot 的 fetched_at 作「此系統已知時間」，basis=`first_observed_snapshot`；不是宣稱當年收盤後立即可用。
* `fetched_at`：含 `+08:00` 的實際抓取時間；raw cache 保留原抓取時間，不改寫成此次讀 cache 的時間。

`filter_available_data(rows, as_of_datetime, strict=True)` 要求有時區、資料觀察日不晚於截止日、可用時間不晚於截止時間且 availability_verified。這版不能以今天下載的歷史資料重建去年的已知資訊，因此嚴格歷史 query 多數返回空資料是預期行為。月營收 FinMind `date` 是期別標記，`create_time` 是資料建立時間，不是可驗證公告時間；`published_at=null`、`strict_historical_backtest_eligible=false`、YoY/MoM 無來源則 null，不猜數值。資料在本系統首次抓到以後可作當時已觀察資料，但不因此取得嚴格「原公告當時」回測資格。

品質檢查含官方 ticker、日期／時區、未來日期、重複、必要欄位、非有限值、負數、OHLC 範圍、股/元單位、無成交／缺價格、未知公告時間、已觀察市場日的個股缺漏、下載失敗與未驗證休市。ERROR 拒絕寫入下載結果；WARNING 不補值；INFO 記錄無成交與未知原公告時間。停牌、除權息／分割不一律判為價格錯誤；尚未接 corporate actions／官方完整休市與停牌表，所以不能宣稱每個缺日都已釐清。coverage 的分母只有「已觀察到資料的市場日」，不是完整官方行事曆，報告另列 empty_unverified／partial／failed。

### Gemini / MCP 的有限重試

`.env.example` 新增以下設定：

| 變數 | 預設 | 用途 |
| --- | --- | --- |
| AGENT_MAX_RETRIES | 3 | 每個尚未輸出內容的 Gemini 請求／唯讀 MCP 工具，最多重試三次 |
| AGENT_INITIAL_BACKOFF | 2 | 秒，指數退避起點 |
| AGENT_MAX_BACKOFF | 15 | 秒，退避上限；每次均加入 jitter |
| GEMINI_REQUEST_TIMEOUT | 60 | 秒，async request 與 SDK HTTP timeout |
| MCP_TOOL_TIMEOUT | 30 | 秒，單次 MCP 工具上限 |
| AGENT_TASK_DEADLINE | 240 | 秒，整輪研究含探索、API、工具、退避 |

Gemini SDK `HttpRetryOptions(attempts=1)`，應用層擁有重試；429、503 等暫時錯誤／網路 timeout 可重試，400/401/403/404 不重試。已輸出 streaming fragment／function_call 後不重播模型請求，避免重複工具呼叫。MCP 只暴露五個唯讀工具；ADK 2.11 自帶「建立 session／工具探索」的一次重連，探索不再疊加應用層 retry；實際 tool call 最多四次且受整輪 deadline 約束。上限不保證 Google／ToAlpha 服務成功，若耗盡則明確失敗，不以生成內容替代資料。這版擴充 ADK canonical_model 與 run hook，已固定 SDK 版本，升級時需重跑測試。

### Unit / Integration / Live E2E

```powershell
# 預設離線：不耗 Gemini 或 ToAlpha 額度；網路測試 SKIPPED
python -m pytest -q

# 真實 TWSE/TPEx 行情與法人 schema；無 Gemini 金鑰
$env:RUN_DATA_INTEGRATION = "1"
python -m pytest tests/test_live.py -m integration -q
Remove-Item Env:RUN_DATA_INTEGRATION

# 真實 Gemini -> Agent -> MCP；明確啟用才消耗 API 額度
# 先設定 research_agent/.env；Agent 載入 dotenv，金鑰缺少則 SKIPPED
$env:RUN_LIVE_E2E = "1"
python -m pytest tests/test_live.py -m live_e2e -q
Remove-Item Env:RUN_LIVE_E2E
```

Live E2E 以獨立 InMemory session 查三題，檢查 Gemini 真的產生 required function_call、MCP 有帶日期的財務 payload、最終中文、日期與來源；回應存在 `data/metadata/live-e2e/*.json`（gitignored）。自動檢查不等於全面查核每個數字、繁體用字與五日明細；須人工比較 response 與最終回答。錯誤／配額不足會 FAIL，不把錯誤提示當成功。三題整體仍須全部驗收，詳見驗收報告。

若 Windows 管控環境 pytest tmp_path 出現 PermissionError，可指定 repository 外的可寫暫存目錄：`python -m pytest -q --basetemp C:\your\writable\pytest-phase2`。這個目錄只能放測試資料，不放正式行情。

### 已知限制與下一階段

目前原公告時間、完整交易日／停牌原因、調整價／corporate actions 與競賽成交均價定義尚未驗收；150 檔未必都已有 120 日上市／上櫃歷史。這些需要官方來源證據，不能從缺漏推定。月營收目前為明確 opt-in FinMind；批量法人及營收長歷史尚需受供應商額度分批驗收。下一階段應先補資料可用時間證據、完整 calendar／corporate actions 與三題 E2E 全通過，再考慮有測試覆蓋的因子和 D-Plan validator；本次沒有實作那些功能。


## Phase 3–7 架構與實際狀態

```text
config/                     官方150股票、30ETF、成本/策略/Agent設定、凍結策略說明
research_agent/             原 Google ADK Research Agent，保留 adk web
src/data/                   TWSE/TPEx/FinMind、SQLite/Parquet、品質與時間檢查
src/competition/            Decimal帳務、整張訂單、公司行動、官方行事曆、對帳
src/strategy/               PIT因子、縮尾與標準化、三基準、walk-forward
src/portfolio/              成本/周轉/現金壓力檢查、合規、30ETF Active Share
src/agent/                  獨立 Investment Agent、唯讀證據工具、型別決策
src/dplan/                  v4.2 schema與語意驗證、官方策略Word範本填入
src/pipeline/               完整離線重播、資料上下文、真實Gemini dry-run與CLI
reports/                    實際驗收、限制及證據摘要
outputs/                    本地trace、草稿、報告、Word/PDF；不提交Git
```

金額與股數帳務使用 Decimal / int；VWAP 由官方成交金額（元）除成交股數計算，NAV 使用收盤價，不用 adjusted_close 替代成交價格。
初始現金10億元、佣金0.1425%、賣出稅0.3%。整張為1000股，成交量欄位統一為股。
交易規劃數量機械計算為 `floor(目標權重 × 前日NAV / 前日官方收盤 / 1000) × 1000 - 前日持股數`。
股價缺漏時阻擋，不補虛構價格。成本取至0.01元 ROUND_HALF_UP 是明確研究假設，尚待主辦方確認。

每輪先取得前日已結算持股、官方收盤與截止時間資料，讓 Gemini 透過唯讀工具自主產生目標與理由。
Optimizer 只給出有界檢查/調整建議，修改目標須回到 Agent 重評；不手改 D-Plan 訂單。
最後驗證來源→觀察→推論→決策→訂單、持股覆蓋、時間、整張、現金、資金用途及正式就緒閘門。
這是可審計的決定式啟發式檢查器，沒有宣稱求得全域最優解。

### 設定與時點模式

- `config/competition.json`：TEAM_11586、官方限制、費稅、未確認規則旗標。
- `config/alpha_strategy.yaml`：5日/20日動能、20日波動扣分、縮尾、z-score與缺值規則。
- `config/optimizer.yaml`：持股數、周轉門檻、不交易區間、成本、5%買賣壓力與修正次數。
- 同檔可設定 Active Share 內部安全門檻（預設25%）、產業集中警示（35%）及基準持股最大年齡（7日）；法定20%門檻不能由內部設定放寬。
- `config/data_policy.yaml`：嚴格可得、研究假設、合成測試模式。
- `config/agent.yaml`：模型及有限重試2秒起、上限15秒、jitter、工具上限與整輪240秒deadline。
- `config/strategy_declaration.json`：實際 Gemini 生成並完成官方Word範本視覺QA的凍結文字。不同策略版本須重新宣告，不可靜默替換。

`strict_verified` 預設只接受可驗證截止前已知的資料；今天下載的歷史 snapshot 不代表歷史當日已可知。
`research_assumptions` 明確假設前日價格在次日可得，仍排除公布時間未知營收，並揭露歷史修訂/公司行動缺口。
`synthetic_fixture` 僅接受標示的合成fixture，不把真實資料混入測試模式；測試資料不進正式 data。
同日收盤與量、未來法人、未知公布時間營收，不可進入08:30決策。市場/產業基準未取得時相對強弱保持缺值，不拿150股票均值冒充加權指數。

### Windows PowerShell 指令

以下指令於 repository 根目錄執行，使用既有 Python 3.11 Conda 環境；不需要 Codex CLI。

```powershell
conda activate esun-agent
python -m pip install -r requirements.txt
python -m src.pipeline.cli universe
python -m src.pipeline.cli availability
python -m src.pipeline.cli simulate --output outputs/offline-replay
python -m src.pipeline.cli offline-dry-run --output outputs/offline-decision-pipeline
python -m src.pipeline.cli replay-decision outputs/offline-decision-pipeline/2026-10-27 --output outputs/replay-check.json
python -m src.pipeline.cli features --as-of '2026-10-12T08:30:00+08:00' --output outputs/features.json
python -m src.pipeline.cli backtest --start 2026-09-03 --end 2026-10-08 --mode research_assumptions
python -m src.pipeline.cli compare --start 2026-09-03 --end 2026-10-08 --mode research_assumptions --output outputs/comparison
python -m src.pipeline.cli walk-forward --mode research_assumptions --output outputs/walk-forward.json
python -m src.pipeline.cli walk-forward --rolling --mode research_assumptions --output outputs/walk-forward-rolling.json
python -m src.pipeline.cli validate-dplan docs/official/D-Plan_TEAM_042_2026-10-27.json
python -m src.pipeline.cli dry-run --trade-date 2026-10-27 --as-of '2026-10-27T08:30:00+08:00' --initial
python -m pytest -q
```

`simulate` 是四日手算黃金帳務重播；`offline-dry-run` 是因子/排名/成本Optimizer/型別MOCK決策/D-Plan/執行/NAV/帳簿接續的完整四日合成管線。兩者都不是 Gemini 真實驗收。`backtest/compare` 預設嚴格模式因缺官方證據回報 BLOCKED；顯式研究模式才執行條件式回測。
walk-forward 實際執行每個訓練/驗證/測試區間，固定參數、不用最終測試調參；各區間從初始現金獨立開始，保留失敗與部分結果。訓練初始21交易日作暖機。
`compliance` 是 `validate-dplan` 別名，可用 `--settled-input` 提供含 state、previous_nav、previous_closes、cutoff 的本地JSON以檢查機械數量；提供檔案不等於官方來源已認證。
`decide` 與 `generate-dplan` 是完整 `dry-run` 別名，不允許跳過自主決策與驗證生成正式訂單。
既有 `python -m src.data.cli` 的下載、更新、品質與查詢功能保持相容。

顯式允許真實 Gemini 與 MCP（消耗 API 額度）：

```powershell
# 先確認 .env 有本人的有效Key；不用把Key貼到終端指令。
if (-not (Test-Path research_agent/.env)) { Copy-Item research_agent/.env.example research_agent/.env }
notepad research_agent/.env
# 若使用 cmd / Anaconda Prompt，複製指令改為：copy research_agent\.env.example research_agent\.env
$env:GEMINI_MODEL = 'gemini-3.1-flash-lite' # 本次實際可用；以帳號當下模型為準
python -m src.pipeline.cli dry-run --live --trade-date 2026-10-27 --as-of '2026-10-27T08:30:00+08:00' --initial --output outputs/competition-dry-run
# 已有部位時用 --state 本地已結算帳本.json 取代 --initial，絕不自動假設初始帳戶。
$env:RUN_LIVE_E2E = '1'
python -m pytest tests/test_live.py::test_live_research -q
Remove-Item Env:RUN_LIVE_E2E
python -m src.pipeline.cli strategy-document --live --output outputs/strategy-document
adk web
```

Investment Agent 是 CLI 的獨立自主決策流程，`adk web` 仍選 `research_agent` 做研究。
複製`.env`只在檔案不存在時執行，避免覆蓋有效Key。環境變數優先於`.env`、再優先於YAML預設。
策略文件需使用主辦方原始繳交DOCX放在 `docs/official`，其SHA256在manifest；未提供時清楚阻擋。
CLI填完Word會回報 VISUAL_QA_REQUIRED，不自行宣稱版面驗收或重新凍結。已有本次QA報告見 reports。

### 驗收限制與後续工作

本次離線測試、實際自主Agent決策、合成重播與條件式回測皆有不同證據，不能互相替代。
歷史資料來源遵守 TWSE/TPEx 官方API優先、FinMind授權備援；ToAlpha只做Research，不批次建立歷史庫。
未來需補全公司行動與歷史公布時點、官方30ETF逐日持股、Active Share口徑、官方帳本、費稅取整、缺價處理及首日提交說明，才能解除正式READY阻擋。
2026-10-26官方行事曆休市，競賽期間實際有24交易日、首個交易日10/27；尚未將此差異當成主辦方正式更正。
無任何真實券商下單、競賽平台上傳、信用交易或自動合併main。不要把改旗標當成完成資料驗證。
詳細操作、規則、資料假設、策略驗證與Agent設計見 docs 中的專題文件。

新run保存輸入、設定、捕捉決策與重播manifest。`replay-decision` 不重新呼叫模型，檢查實際來源/設定版本與檔案hash，再比較Optimizer、完整D-Plan bytes及驗證。可提供 `--expected-manifest-sha256`；本機hash不是外部來源認證。缺上下文的舊trace或來源版本改變不猜測重播，回報BLOCKED；REPRODUCED也不把原草稿提升成READY。分階段工程驗收見 `reports/phase3_acceptance.md` 至 `phase7_acceptance.md` 及 `end_to_end_readiness.md`。

`ActiveShareCalculator.evaluate()` 逐一輸出官方30檔ETF的持股時點、可得時間、來源、方法、Active Share及缺漏原因。缺失持股不補零；方法未確認不回報合規通過。Optimizer 有提供結構化基準資料時會檢查，資料不足時仍保留研究提案但正式就緒維持 BLOCKED。
`ActiveShareValidator.validate()` 使用競賽交易日集合、同日可得基準和前日已驗證計數；跳日、重跑、缺前日證據或拿舊持股判斷新交易日都不能更新正式計數。這項獨立檢查不替代官方結算對帳。
模擬器可接收 `benchmark_snapshots`、`benchmark_cutoff` 與 `active_share_method`，由當日NAV權重重新計算；正式檢查不接受單純AS數值字典。帳簿另存 `active_share_last_date`，交易日期推進不代表AS已驗證；缺證據的研究日不能接成連續違規。交易回滾後使用实际保留部位的AS計數，原輸入帳簿不被修改。
產業集中度只使用截止前可得、來源明確的分類；未知分類另外列出。產業35%是內部警示，並非主辦方法定限制。
# Phase 8／9 每日執行與驗收

本次延續 `feature/phase3-7-integrated` 的45caf06，新增每日Runner、OS防重複鎖、Run ID、階段恢復、輸入／輸出hash、截止檢查、本機通知、Windows排程腳本、24日明確合成驗收。**正式參賽仍為BLOCKED**，不是把已實作等同於正式驗收成功。

完整指令與故障處理見 [每日操作](docs/daily_operations.md)；正式缺口見 [規則缺口](docs/official_rules_gaps.md)；驗收見 [Phase 8](reports/phase8_acceptance.md)、[Phase 9](reports/phase9_acceptance.md)、[就緒狀態](reports/competition_readiness.md)。沒有新增外部券商、平台提交、LINE或Codex CLI依賴。

```powershell
conda activate esun-agent
python -m pip install -r requirements.txt
python -m src.pipeline.cli calendar-refresh --year 2026
python -m src.pipeline.cli daily-run --calendar data/metadata/twse-calendar-2026.json
python -m src.pipeline.cli offline-dry-run --sessions 24 --output outputs/offline-24
python -m pytest -q
```

每日結果：`outputs/daily/日期/RunID/status.json`、`notification.json`、`report.md`。COMPLETED只代表執行結束；exit2和submission_status=BLOCKED不能當作可提交。一般命令不消耗Gemini額度，只有明確`--live`才啟用。官方窗口預設未知；07:00是排程建議、08:30是內部研究期限，不是假造主辦方截止。前日帳簿自動找`data/metadata/settled/日期.json`，缺少即阻擋，不能拿昨天草稿當已成交持股。

排程先用 `./scripts/Install-DailyTask.ps1 -PythonExe (Get-Command python).Source -DryRun` 檢查；拿掉-DryRun才註冊。移除用 `./scripts/Remove-DailyTask.ps1`。預設非Live、目前登入使用者、台北系統時區，不儲存密碼、不改Conda或Windows時區。排程失敗、睡眠／關機／重啟與恢復限制見操作文件。

新增session-quality依已保存官方TWSE日曆區分休市、缺股與衝突；TPEx獨立日曆仍未驗證，不覆寫歷史狀態與公布時間。ETF持股有可替換的審閱檔案Adapter，缺30檔實際完整持股時逐檔回報，不補零。隔夜美股Adapter、公司行動完整覆蓋、正式結算真實性、官方Active Share／費稅／首日／截止口徑仍是缺口。

## 競賽前真實 API 工程演練

新增 `rehearsal-run --live`，串接真實本地資料、MCP市場證據、Gemini投資決策、既有Optimizer與D-Plan，獨立保存演練audit，正式提交永遠BLOCKED。不改daily-run期間／期限／規則，也不接受指定未來日期。詳見 [演練操作指南](docs/precompetition_rehearsal.md)。Research來源驗證改為比對工具回傳的來源／連結，接受source與完整機構名稱，不接受僅有空泛標籤。法人五日逐日明細不足仍未解決。
