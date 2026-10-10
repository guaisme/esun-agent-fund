# Windows 操作與故障處理

完整PowerShell指令見README；所有命令於repo根目錄、Python3.11 Conda執行。已有環境直接activate，不改Conda全域設定。不需要Codex CLI。

`universe`檢查150/100/50。`availability`報告資料期別；`features`建立PIT因子；`backtest/compare/walk-forward`保留所有結果；`simulate`黃金帳務；`offline-dry-run`完整四日MOCK管線；`dry-run --live`才真正呼叫Gemini。沒有--live會SKIPPED而不是暗中花額度。

`validate-dplan`及其別名`compliance`可驗證JSON，未知官方已結算帳本不能轉為VALID。`decide/generate-dplan`是完整dry-run別名。帳簿透過--state載入，--initial必須明確指定初始狀態。官方帳本由SettlementReconciler核對原檔SHA256、隊號、日期、股數、現金與NAV；目前無主辦方API，不能自行編造endpoint或交易紀錄。

HTTP404先查模型存取；429是速率/配額限制、不要無限重送；503或timeout有有限退避，持續不可用回FAILED。MCP完成工具後仍可能等待Gemini整理。API Key不得貼在日誌/.env不得Git提交。cmd沒有Copy-Item，改用copy；PowerShell才有Copy-Item。

ToAlpha data_status包含未來法說會等服務metadata，現透明移除未來項而保留當前資料更新狀態；它本身不算金融研究證據，仍須呼叫主題工具。金融數據未來日期仍拒絕。

outputs保存source ZIP與hash、原始trace、草稿、portfolio、QA報告，不加入Git。data只存真實Provider資料，tests/temp只存標示fixture。正式READY目前BLOCKED，不以修改旗標繞過資料完整性。
