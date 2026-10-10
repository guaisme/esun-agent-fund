# Phase 2 實際驗收紀錄

日期：2026-10-11（Asia/Taipei）。整體 **PARTIALLY COMPLETED**，不是全部金融資料與 E2E 已驗收完成。

## 程式與測試

| 項目 | 狀態 | 實際證據 |
| --- | --- | --- |
| 官方名單、Provider、增量儲存、PIT、CLI、資料品質、重試／deadline | COMPLETED（已實作並測試） | 新增 src/data、config 與 reliability 模組；詳細檔案見 README |
| 離線 pytest | COMPLETED | 最新 `56 passed, 5 skipped`，沒有真實 Gemini／ToAlpha 呼叫 |
| 原有第一階段 15 項 | COMPLETED | 原 test_research_agent.py 內容未修改，15 項全部通過 |
| 官方 TWSE／TPEx Integration | COMPLETED | `1 passed, 3 deselected`；2330、5274 的 2026-09-30 行情與法人真正取得、正規化檢查通過 |
| ToAlpha Integration | COMPLETED | `1 passed, 15 deselected`；tools/list、五個工具與 ADK toolset 探索實際通過 |
| FinMind 真實月營收 | COMPLETED（單股樣本） | 2330 十筆，觀察期別 2025-12 至 2026-09；published_at 全為 null，不冒充公布日 |
| TWSE／FinMind 真實交叉核對 | COMPLETED（單日單股） | 2330、2026-09-30：OHLC、volume、turnover 與股／元單位八項相同 |
| 最新區間重複下載 | COMPLETED | 2026-09-28～10-08 重跑：written=0、跳過16個完整市場日、failures=[] |
| PIT 真實本地查詢 | COMPLETED | `--as-of 2025-03-10T08:30:00+08:00` 回傳 []，未洩漏今日才下載的歷史 snapshot |
| Windows ADK Web | PARTIALLY COMPLETED | 原 TestClient／AgentLoader 測試仍通過；新版使用者瀏覽器互動流程需本機重啟驗證 |

離線測試涵蓋官方名單變異、數量、市場、重複、不合法 ticker、欄位映射、單位、原始 JSON cache、SQLite／Parquet、重複與修訂、備援、timeout、429、503、MCP timeout、整輪 deadline、已輸出 stream 不重播、PIT、資料品質、缺漏與休市未知狀態。MOCK 只在測試暫存目錄，正式 data 內都是實際供應商資料。

測試環境：Windows 11、既有 Conda esun-agent、Python 3.11.17。直接依賴版本：google-adk[mcp] 2.11.0、google-genai 2.29.0、mcp 2.3.0、python-dotenv 1.2.4、pytest 9.1.1、requests 2.34.2、pyarrow 26.0.0。格式工具只安裝到工作區內的獨立 target，未修改 Conda 全域設定或使用者原套件。

## 三題 Live E2E

實際使用使用者本機金鑰，模型為 gemini-3.8-flash；API Key 沒有複製到 repository、日誌、報告或 GitHub。

| 問題 | 狀態 | 實際結果與限制 |
| --- | --- | --- |
| 2330 最新月營收 | COMPLETED（本次研究鏈路） | 真正呼叫 data_status、resolve_stock、monthly_revenue，最後有繁體中文；2026-09、營收／YoY／MoM／單位與 MCP payload 對照一致。原公布時間未知，回答已說明 |
| 台灣市場概況 | PARTIALLY COMPLETED | 初次 503→503→429→429，四次嘗試後安全失敗。另一次真實呼叫 market_overview 並產生繁體中文報告；原驗收 parser 只找最上層 as_of，誤判巢狀日期，已修正並對這份實際捕獲資料重檢通過。最後真實重跑又遇429，尚未取得新版測試的穩定 live PASS |
| 2454 最近五個交易日法人 | PARTIALLY COMPLETED | 真正呼叫 institutional_flows、生成繁體中文，最新單日與 d5 累計值對照一致；ToAlpha 工具未提供五天逐日完整表，Agent 明確說明限制，不能把近5日累計宣稱為5天明細 |

首次 live pytest 的實際結果為 `2 passed, 1 failed`，不是三題全通過。市場 parser 修正前重跑失敗為測試程式錯誤，修正後最後重跑失敗為 Gemini 429。實際捕獲回應的重新驗證是 **離線檢查真實紀錄**，不是新的 live API 測試；不以此冒充重新執行 E2E 成功。

E2E 自動測試只驗證呼叫、帶日期的財務 payload、中文最終回答、日期與來源引用；並不證明每個推論正確。已人工對照上述回應的主要數值與單位，但未向原始公開資訊觀測站逐筆重核 ToAlpha 全部財務數值。市場 valuation 缺獨立 as_of，不能視為已驗證該欄位時點。data_status 含未來活動日期，現行保守 guard 拒絕整份結果，各次報告均說明此限制；尚需區分活動排程與財務資料日期。

## 官方 150 檔與真實行情品質

官方 PDF 三頁抽取並目視核對：**150 檔、TWSE 100、TPEx 50、無重複**；CSV 的代號／名稱／市場與 source manifest 一致，manifest 含原 PDF SHA256。未自行生成股票名單。

實际依序下載區間 2026-03-01～2026-10-08，取得交易日 **2026-03-02～2026-10-08**：

* 150／150 檔都有資料，**22,632 筆**真實價格，來源 TWSE／TPEx。
* **148 檔達到至少120筆交易日；7828為117筆、3718為24筆**。這兩檔歷史不足仍為 PARTIALLY COMPLETED，掛牌／代號變更原因待官方證實；不拼接前身、不造股價。
* 以已觀察市場日計算，應有22,800筆，實際coverage **99.2632%**。這不是已核對完整交易日／停牌清單的完整度；缺漏168筆仍列出。
* 品質 **ERROR 0、WARNING 8、INFO 22,634**。3491 的 2026-07-15 與3665的2026-06-10 OHLC各四項為null，保留無成交／停牌未知狀態，沒有補數字。
* 行情檢查點：140個 empty_unverified、128個 partial、**0個 failed**；空 API 不是已驗證休市，partial反映個股缺漏。
* 最近法人區間 **2026-09-28～2026-10-08**，150檔、8個交易日、**1,200筆**，ERROR 0、WARNING 0。完整120日法人歷史 **NOT TESTED**。
* 月營收只取得2330 **10筆**，ERROR 0、WARNING 10（公布時間未知）。全150檔長期營收批次與可驗證公布時間 **NOT TESTED**。

摘要：[data_quality_summary.json](data_quality_summary.json)。完整股價／法人／營收、原始 JSON、每檔每日期缺漏與完整報告在開發 checkout 的 data 內，全部 gitignored，沒有提交大量金融歷史資料。Git checkout 不會帶這些資料到使用者既有本機資料夾，請按 README 重新下載。

## 可靠性與尚未驗收事項

* **COMPLETED**：Gemini SDK attempts=1，應用層最多3次 retry，exponential backoff/jitter、每請求60秒、唯讀工具30秒、整輪240秒，均可設定；503／429實際有限重試與安全失敗有日誌證據。已有 stream 不重播，MCP探索不疊加ADK重連。
* **PARTIALLY COMPLETED**：503／429仍受供應商服務與配額影響，沒有三題連續穩定通過的證據；重試只改善故障處理，不保證服務成功。
* **BLOCKED（外部驗收）**：最終市場 live 重跑遇429，需服務／額度恢復後再驗；這不是程式碼已驗收全通過。
* **NOT TESTED／待來源確認**：歷史原公布時點、完整休市／停牌清單、corporate actions、主辦方成交均價公式、TPEx 法人 schema 分組位置跨版本穩定性、供應商競賽再利用授權與大量配額。TWSE/TPEx歷史端點已真正驗證，不代表所有年月／變更情境都已覆蓋。
* 不將今日下載的歷史資料假裝在當年可得。available_at為首次snapshot，嚴格歷史查詢排除未知可用時點。

## 使用者本機與下一階段

完整 PowerShell 指令見 README：`conda activate esun-agent`、`git fetch origin`、`git switch feature/phase2-market-data`、`python -m pip install -r requirements.txt`、`python -m pytest -q`；保留原 .env，重啟 `adk web`。

再執行 `python -m src.data.cli universe`、`download --start 2026-03-01 --end 2026-10-08`、`quality --min-days 120`，然後明確啟用 RUN_LIVE_E2E 做三題驗收。P0剩餘項目應先補市場Live PASS、五日明細與短歷史原因；P1再補公布時間證據、完整calendar／corporate actions。這些資料驗收完成前，不建議把本版稱為已可正式無前視回測或競賽下單的系統。


## 新增與修改檔案

修改：`.gitignore`、`requirements.txt`、`research_agent/agent.py`、`research_agent/.env.example`、`README.md`。

新增：

```text
config/universe.csv
config/universe_manifest.json
src/__init__.py
src/data/__init__.py
src/data/universe.py
src/data/time_safety.py
src/data/http.py
src/data/storage.py
src/data/quality.py
src/data/download.py
src/data/cli.py
src/data/providers/__init__.py
src/data/providers/base.py
src/data/providers/twse.py
src/data/providers/tpex.py
src/data/providers/finmind.py
src/data/providers/institutional.py
research_agent/reliability.py
pytest.ini
tests/test_data.py
tests/test_live.py
reports/data_quality_summary.json
reports/phase2_acceptance.md
```

第一階段 `research_agent/__init__.py` 與 `tests/test_research_agent.py` 保留；官方其他競賽文件只供規則與未來資料欄位參考，沒有把其中策略範例當開發／交易指令。
