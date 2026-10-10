# ESUN AI Agent ETF Competition 2026

第一階段是使用 **Google ADK + Gemini AI Studio + ToAlpha Streamable HTTP MCP** 的台灣股票 Research Agent。
範圍只有市場、個股月營收及三大法人研究；不含交易、買賣決策、Portfolio Optimizer、回測或 D-Plan。

## Windows 11 / Conda 啟動

在 Anaconda PowerShell Prompt 執行。已有 Python 3.11 環境時直接 activate 該環境，不須重新建立或修改 Conda 全域設定。

```powershell
git clone https://github.com/guaisme/esun-agent-fund.git
cd esun-agent-fund
git switch codex/research-agent-phase1
conda create -n esun-agent-fund python=3.11 -y
conda activate esun-agent-fund
python --version
python -m pip install -r requirements.txt
Copy-Item research_agent/.env.example research_agent/.env
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
Gemini 每次 HTTP 請求 timeout 60 秒（SDK 單位為毫秒），attempts=1，不自動重試該請求。
這是每個請求的逾時，並非整輪研究總時限；網路傳輸持續有進度時總時間仍可能較長。
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
此階段不是 point-in-time 資料系統，無法證明歷史可知時間的資料不能用作無前視偏誤研究。

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
金鑰不在 repository，這項端到端驗收必須在本機執行，不能宣稱已完成。

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

## 下一階段

本階段驗收後，再規劃帶公告時間的資料品質檢查與研究評估集；ETF 研究擴充、策略、回測與組合功能須另外定義範圍，本次沒有實作。

## 本次實際驗證（2026-10-11，台北時間）

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
