# Windows 操作與故障處理

完整PowerShell指令見README；所有命令於repo根目錄、Python3.11 Conda執行。已有環境直接activate，不改Conda全域設定。不需要Codex CLI。

`universe`檢查150/100/50。`availability`報告資料期別；`features`建立PIT因子；`backtest/compare/walk-forward`保留所有結果；`simulate`黃金帳務；`offline-dry-run`完整四日MOCK管線；`dry-run --live`才真正呼叫Gemini。沒有--live會SKIPPED而不是暗中花額度。

`validate-dplan`及其別名`compliance`可驗證JSON，未知官方已結算帳本不能轉為VALID。`decide/generate-dplan`是完整dry-run別名。帳簿透過--state載入，--initial必須明確指定初始狀態。官方帳本由SettlementReconciler核對原檔SHA256、隊號、日期、股數、現金與NAV；目前無主辦方API，不能自行編造endpoint或交易紀錄。

HTTP404先查模型存取；429是速率/配額限制、不要無限重送；503或timeout有有限退避，持續不可用回FAILED。MCP完成工具後仍可能等待Gemini整理。API Key不得貼在日誌/.env不得Git提交。cmd沒有Copy-Item，改用copy；PowerShell才有Copy-Item。

ToAlpha data_status包含未來法說會等服務metadata，現透明移除未來項而保留當前資料更新狀態；它本身不算金融研究證據，仍須呼叫主題工具。金融數據未來日期仍拒絕。

outputs保存source ZIP與hash、原始trace、草稿、portfolio、QA報告，不加入Git。data只存真實Provider資料，tests/temp只存標示fixture。正式READY目前BLOCKED，不以修改旗標繞過資料完整性。

新增run會保存 `decision-inputs.json` 與 `decision-replay.json`。離線重播命令：`python -m src.pipeline.cli replay-decision outputs/offline-decision-pipeline/2026-10-27 --output outputs/replay-check.json`。可加 `--expected-manifest-sha256` 使用獨立保存的manifest hash；未提供時只檢查本機完整性，不能認證外部來源。

重播不呼叫Gemini/MCP、不花API額度、不修改D-Plan或帳簿。只有來源內容、保存檔案、配置、D-Plan bytes及驗證都一致才REPRODUCED；正式就緒保留原來結果。新D-Plan用UTF-8及LF換行，避免Windows換行造成跨平台雜湊差異。舊run沒有必要輸入會BLOCKED，程式/設定不同須先在隔離位置還原保存版本；不自動執行archive裡的程式碼。

`replay-decision` exit code：0=REPRODUCED、1=FAILED、2=BLOCKED，供PowerShell檢查 `$LASTEXITCODE`。原始manifest hash應另行保存；同時改寫所有本地檔案與hash無法由本地hash證明外部真實性。
