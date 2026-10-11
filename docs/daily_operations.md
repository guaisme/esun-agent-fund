# Phase 8 每日營運

狀態：PARTIALLY COMPLETED；正式參賽 BLOCKED。延續45caf06，沒有重建Phase1–7或合併main。

`daily-run` 是監督程序：持有作業系統檔案鎖、建立唯一Run ID，子程序串接既有資料、Feature、Alpha、Investment Agent、Optimizer、Compliance、D-Plan。整體期限預設900秒；Live會再受08:30**內部研究期限**及已設定提交截止約束。08:30不是已確認的官方提交截止。SDK每請求有限重試沿用原政策，重試與資料下載也計入總期限。到期停止子程序、寫FAILED/BLOCKED，不使用替代投資組合。

預設不啟用模型、不更新網路資料。`--live`才允許模型與MCP；`--update-data`才更新行情。Live缺Key、日期不是今天、非競賽期間、休市、缺前日持股、最新資料不完整皆阻擋。首日初始十億元須明確`--initial`，只允許已驗證TWSE首個交易日；仍保留主辦方首日安排未確認的正式閘門。

本機時間換算台北。先載入有原始回應/hash的TWSE日曆，取前一交易日。`--state`可指定帳簿，否則找`data/metadata/settled/YYYY-MM-DD.json`。只能載入該前日帳簿；不能把草稿決策當成已成交狀態。`--settlement`可提供整理後的主辦方結算檔，使用既有Reconciler比對隊號、日期、現金、股數、NAV和原檔hash；目前不自動認證原檔真實性。差異阻擋，禁止覆蓋本機持股。

結算整理檔欄位：`team_id,trade_date,cash,positions,nav,original_file,original_sha256,retrieved_at`。金額用字串，股數用整數，original_file相對整理檔位置。不能只設定verified=true宣稱取得主辦方證據。

資料快照、品質報告、截止時間、150檔因子與排名保存於Run目錄。首次下載應先用Phase2 CLI分批建庫；`daily-run --update-data`不是繞過API額度的快速建庫工具。現版本自動更新行情；法人、營收長歷史仍須另行更新。無正式公司行動與隔夜美股adapter會明列缺口，不補數字。

Live在盤前透過官方MCP SDK呼叫`market_overview`，保存真實tool name/arguments/response。Gemini透過既有唯讀工具讀取捕捉的市場證據；這是Runner取得MCP後供模型使用，不冒稱模型自己呼叫MCP。未標日期的valuation不供模型當事實；最新指數必須對應前一交易日。MCP錯誤／逾時會FAILED，沒有常識備援。

## 狀態與保存

`outputs/daily/<日期>/<Run ID>/`：status.json、notification.json、report.md、request.json、各階段JSON、decision/下的trace/草稿/source ZIP/replay資料。`execution_status=COMPLETED`只代表流程結束；submission_status保留BLOCKED。沒有完整正式證據時目前不會產生READY，不能以設定旗標解鎖。

鎖由OS持有，程序退出或重開機後會釋放；不能憑鎖檔存在或TTL猜測占用。已有同日執行會要求`--resume RunID`或`--rerun`。重跑建立新Run ID，舊輸出不覆蓋。恢復檢查程式、設定、輸入與已完成階段檔案hash；已完成Run不可修改。模型決策進行中斷時必須明確rerun，不默默重送。跨日過期Live不能恢復成當日正式決策。

比較命令不修改文件：`compare-runs <舊Run目錄> <新Run目錄>`。本機hash是完整性紀錄，不是外部來源真實性證明。CLI exit 0=READY，1=FAILED，2=BLOCKED。本版正常執行也可能exit2。原始SDK錯誤文字不進入每日報告；記錄類型、階段與已保存的工作流紀錄，沒有印Key。已完成和失敗通知均為本機JSON/Markdown及終端機，不發送訊息給他人。

## Windows PowerShell

於repo根目錄執行：

```powershell
conda activate esun-agent
python -m pip install -r requirements.txt
python -m src.pipeline.cli calendar-refresh --year 2026
python -m src.pipeline.cli daily-run --calendar data/metadata/twse-calendar-2026.json
python -m src.pipeline.cli offline-dry-run --sessions 24 --output outputs/offline-24
python -m src.data.cli session-quality --start 2026-03-01 --end 2026-10-08 --calendar data/metadata/twse-calendar-2026.json --output outputs/session-quality.json
python -m src.pipeline.cli benchmark-status --as-of 2026-10-11T07:00:00+08:00
python -m pytest -q
```

日曆分類另輸出報告，不覆寫原歷史狀態，不把休市核對當作公布時間驗證。`partial`會列出當日缺股；上櫃日曆未知仍未驗證，缺股原因須上市櫃／停牌證據。

在真正當日窗口內，已備妥資料與上一交易日帳簿後才明確啟用（消耗額度）：

```powershell
python -m src.pipeline.cli daily-run --live --update-data --calendar data/metadata/twse-calendar-2026.json --state data/metadata/settled/上一交易日.json --benchmarks data/raw/benchmarks
# 初始首日另用 --initial，不與 --state 同時使用
# 失敗恢復：保留完全相同參數並加 --resume 原RunID
# 明確重新執行：保留原Run並加 --rerun
```

正式窗口未知保留BLOCKED，可以在內部研究期限前研究；不得晚於真正窗口生成可提交狀態。operations.yaml預設官方窗口為null，不填猜測時間。要修改用`config/local_operations.yaml`並以`--operations`指定，保留正式來源與審閱證據；此設定本身不代表規則認證。

## Windows Task Scheduler

不需Codex CLI／Docker。不修改Conda全域設定。先檢查動作，安裝預設只做非Live預檢：

```powershell
$pythonExe = (Get-Command python).Source
./scripts/Install-DailyTask.ps1 -PythonExe $pythonExe -DryRun
./scripts/Install-DailyTask.ps1 -PythonExe $pythonExe
Get-ScheduledTask -TaskName ESUN-Agent-Daily-0700
Get-ScheduledTaskInfo -TaskName ESUN-Agent-Daily-0700
Start-ScheduledTask -TaskName ESUN-Agent-Daily-0700
./scripts/Remove-DailyTask.ps1
```

本次沒有實際註冊或移除使用者排程。腳本已解析與DryRun；真正排程觸發屬本機NOT TESTED。要變更現存排程先檢查再移除，沒有Force覆蓋。安裝Live排程需使用者在指令中明確加-Live，預設不消耗額度。腳本直接用Conda環境python.exe，不靠背景shell的conda activate。

排程使用台北系統時區；若Windows不是Taipei Standard Time則拒絕安裝，不改全域時區。每日07:00，IgnoreNew、StartWhenAvailable、WakeToRun、20分鐘上限。這些選項語意依[Microsoft文件](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasksettingsset)；關機時無法執行，睡眠喚醒取決於硬體／電源政策。重開機／登入後補跑仍檢查日期及期限，過期不提交。排程要使用審閱後的本機operations設定可加`-OperationsFile (Resolve-Path config/local_operations.yaml).Path`；來源archive包含實際PowerShell腳本hash。

首版用目前使用者Interactive token，不儲存密碼，因此需已登入。沒有宣稱登出後仍可無人值守。排程stdout日誌在outputs/scheduler，個別Run有通知與報告。FAILED依原因檢查資料／服務後resume或rerun；BLOCKED不能靠重試消除規則缺口。

## Live測試

普通pytest不消耗額度。本次Live daily NOT TESTED：未明確啟用且正式資料缺口未補齊。要執行需當日、Key、正式日曆與前日帳簿：

```powershell
$env:RUN_LIVE_DAILY='1'
$env:DAILY_CALENDAR=(Resolve-Path data/metadata/twse-calendar-2026.json).Path
$env:DAILY_STATE=(Resolve-Path data/metadata/settled/上一交易日.json).Path
python -m pytest tests/test_live_daily.py -q
Remove-Item Env:RUN_LIVE_DAILY
Remove-Item Env:DAILY_CALENDAR
Remove-Item Env:DAILY_STATE
```

Key只放research_agent/.env，不貼命令或日誌。測試資料及金鑰均不提交Git。
