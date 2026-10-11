# 競賽前真實 API 演練

`rehearsal-run` 是獨立工程入口，不改 `daily-run` 的競賽期間、提交窗口與合規閘門。日期使用當下台北日期，不接受未來／歷史trade-date或as-of；假日／競賽前可研究，正式提交永遠 **BLOCKED**。

## Windows PowerShell 操作

在Repository根目錄執行：

```powershell
conda activate esun-agent
python -m pip install -r requirements.txt
python -m pytest -q
python -m src.pipeline.cli calendar-refresh --year 2026
# 非Live預檢只寫報告，不呼叫MCP或Gemini。
python -m src.pipeline.cli rehearsal-run --calendar data/metadata/twse-calendar-2026.json --initial
# 明確啟用真實API；因前一預檢已有同日紀錄，須加--rerun。
python -m src.pipeline.cli rehearsal-run --live --rerun --calendar data/metadata/twse-calendar-2026.json --initial
```

金鑰沿用research_agent/.env，禁止貼入命令／Git。**--live會消耗Gemini額度**。`--initial`明確使用研究用十億元空倉，不是官方已結算帳簿。持股接續改用 `--state 本地帳簿.json`，不能與--initial並用；若有last_trade_date，須為前一驗證交易日。無日期帳簿仍標示未認證研究輸入；不能將草稿委託當已成交。

同日再次執行必須--rerun，新Run ID保留舊產物；沒有自動resume／默默重送模型。整體監督期限900秒，Gemini沿用既有240秒與有限重試／決策修正；逾時、模型無效、Optimizer無可行組合皆FAILED，不替換投資組合。exit2為BLOCKED、exit1為FAILED，沒有可提交exit0路徑。

## 真實資料要求

需有data/metadata/catalog.sqlite與Parquet真實資料、保存原始回應/hash的TWSE日曆。入口只讀既有資料，不大量下載。使用strict_verified，拒絕合成資料、當日收盤／未來資料；資料品質ERROR、TWSE日曆衝突、前一驗證交易日缺150檔正值收盤，或不足20檔PIT安全排名，皆在外部API前阻擋。TPEx日曆未認證保留警告。

若PreviousSessionCoverageIncomplete，查看缺股清單後用Phase2下載指令更新，不篡改日期。資料公布時間未知保留不確定性；真實首次觀測時間只證明目前已觀測，不冒稱過去已知。法人／營收被PIT排除的數量保存在品質報告，可能造成排名不足。

Runner實際呼叫ToAlpha `market_overview`，其指數日期須等於前一驗證交易日，排除無日期估值。保存工具回傳後供Gemini唯讀工具使用，不冒稱Gemini自己呼叫MCP。資料庫快照在外部呼叫前選取，MCP回傳後凍結證據，不再載入後來資料。ETF缺持股不補零；可選--benchmarks data/raw/benchmarks。

## 輸出與驗收

獨立目錄 `outputs/rehearsals/<日期>/<Run ID>/`、鎖與registry，不使用正式Daily目錄。

- status.json、notification.json、report.md：執行與提交狀態。
- frozen-data.json、context.json、settings.json：實際輸入與設定。
- data-quality.json、benchmarks.json、mcp-market.json：品質、ETF缺漏、真實工具紀錄。
- decision/：原工作流trace、Optimizer、原始正式驗證、來源archive。
- workflow.json：原始正式閘門／驗證，不修改失敗。
- rehearsal-dplan-audit.json：用捕捉的typed決策及原metadata生成；Decimal保留JSON數字，不手改委託。
- rehearsal-validation.json：工程Schema／語意檢查。

證據在模型開始前凍結。既有正式驗證仍可能記錄C6_CUTOFF（完成晚於凍結時間）等失敗，不刪除或改PASS。**另一份工程驗證**以實際模型完成時間作為收件時間上界，輸入仍是原凍結資料，不加入後來資訊，也不認證正式提交窗口。它仍可能BLOCKED（結算／日曆／來源待證明）；rehearsal_checks_passed僅指Schema與機械檢查沒有HARD_FAIL。

成功串接預期execution_status=COMPLETED、gemini=DECISION_PARSED、schema=true、rehearsal_checks_passed=true；submission_status永遠BLOCKED。錯誤不是成功，即使取得部分資料。金融內容只留Git忽略目錄。

這次開發未執行最新入口的真實Live驗收；MOCK測試不能證明真實Gemini成功。原Research法人工具仍缺五日逐日明細，修正來源辨識不等於補資料。正式首日、Active Share口徑、公司行動、結算與窗口仍待證據。

## Opt-in Live測試

```powershell
$env:RUN_LIVE_REHEARSAL='1'
$env:REHEARSAL_CALENDAR=(Resolve-Path data/metadata/twse-calendar-2026.json).Path
# 已有同日演練，確定要重跑時才設定：
$env:LIVE_REHEARSAL_RERUN='1'
try { python -m pytest tests/test_live_rehearsal.py -q -rA }
finally {
    Remove-Item Env:RUN_LIVE_REHEARSAL -ErrorAction SilentlyContinue
    Remove-Item Env:REHEARSAL_CALENDAR -ErrorAction SilentlyContinue
    Remove-Item Env:LIVE_REHEARSAL_RERUN -ErrorAction SilentlyContinue
}
```

測試使用研究初始現金及真實本地資料。普通pytest預設SKIPPED；API或驗證錯誤會失敗，不是skip。
