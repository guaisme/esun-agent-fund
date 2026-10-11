# 技術驗收與發布範圍

2026-10-11最新離線pytest：209 passed、6 skipped、4 warnings，13.19秒。原15項仍通過，原測試檔內容保持。合成fixture明確標示MOCK，不當真實API結果。

新增15項saved-decision replay測試，涵蓋完整上下文/設定/typed decision保存、Decimal觀察數字、相同Optimizer/D-Plan bytes/驗證、檔案修改、重新hash的訂單/配置修改、來源版本不同、獨立manifest hash、目錄逸出、缺舊run上下文、cutoff不符及CLI exit code。第一輪2項因Windows CRLF/LF差異失敗、14項通過；固定新D-Plan為UTF-8/LF後該批16項通過，最終整套206項通過。沒有掩蓋初次失敗或把重播當新模型呼叫。

再加3項錯誤格式manifest/來源雜湊物件反例，完整結果209 passed。四日MOCK CLI已實際執行，逐日重播均REPRODUCED；只有原始確定式處理被重現，正式就緒仍BLOCKED。本輪沒有重新跑Live或取得新的官方ETF持股。

新增22項涵蓋逐30ETF報告、缺漏/重複/過期/未來時點阻擋、Active Share連續交易日與重跑、舊日持股不能累計新日違規、產業來源/可得時間、Optimizer證據檢查與設定驗證、Git版本讀取逾時仍保留實際內容雜湊。產業警示不是官方限制；此輪未新增真實ETF資料驗收。

另6項驗證正式合規不接受裸AS數值字典、交易回滾後由實際保留部位重新計算、法定20%不可重設、AS證據日期獨立於帳簿日期、缺一日證據不能累計連續違規、舊帳簿仍可讀。`active_share_last_date` 新欄位預設空；沒有日期的舊AS計數不自動認證為官方歷史。

受限沙箱曾卡在Windows asyncio初始化的本機socketpair，尚未進入測試業務邏輯；堆疊診斷後在允許本機loopback的環境完成整套離線測試。已取消的診斷執行不算通過。6項跳過仍為顯式啟用或需外部服務的測試。

實際Research與Investment API亦已執行；詳細金融回傳、投資配置、績效、模型trace、生成文件與草稿保留本機 gitignored reports及outputs。此PR只含程式碼、官方設定、測試、操作文件及技術驗收，不發布這些衍生金融資料。

正式競賽就緒為BLOCKED：官方持股與Active Share口徑、公司行動/PIT完整性、官方已結算帳本、費稅取整/缺價規則、首日提交與TPEx日曆證據未完全驗證。程式不能用修改旗標或假資料繞過。

GitHub目的地已透過官方connector核對：repository ID1413519474，owner與authenticated user ID一致，使用者有admin/push權限。自動核准審查仍拒絕發布詳細金融資料，已縮限為工程交付；原始證據未刪除、未改經其他管道發布。
# Phase 8／9 更新（2026-10-11）

完整離線回歸實際結果：**243 passed、7 skipped、4 warnings，82.25秒**。既有209項及原Research15項仍通過。新增34項離線Daily/鎖/恢復/期限/時區/資料缺漏/通知/Benchmark/MCP證據與24日測試；新增Live daily為第7項skip，沒有消耗API額度或冒稱Live成功。

24日CLI合成風險情境（risk-off/risk-on/risk-off）已實際執行，150檔因子與排名、20檔持股、首日建立／後續加碼／減碼、D-Plan／驗證／NAV／帳簿接續通過，正式readiness仍BLOCKED。資料與模型都是MOCK，詳細產物只留本機。初次候選超限與另一中性情境被Optimizer阻擋的失敗紀錄未刪除，沒有放寬Optimizer或正式規則。

實際執行官方TWSE2026日曆下載、原回應hash、session-quality、非競賽期間每日預檢和同日重啟拒絕、PowerShell三腳本解析及安裝-DryRun。沒有實際安裝持續排程、睡眠／重啟測試或最新版本Live daily。TPEx官方頁本次403，保留獨立日曆未驗證。30ETF完整持股接受數0，逐檔缺漏欄位/截止時間回報。

排程／營運原始輸出與Live金融紀錄不加入Git；只交付source、MOCK測試與工程文件。實際派送使用以feature/phase3-7-integrated為基底的新分支與Draft PR，沒有合併main。

最後將排程腳本納入來源指紋並支援 OperationsFile 後，另執行受影響測試：54 passed、1 deselected、2 warnings（14.91秒）；未把此批當成第二次完整243項回歸。PowerShell解析與安裝DryRun亦重驗通過。

## 競賽前演練與Research來源修正

完整回歸264 passed、8 skipped、4 warnings（51.80秒）；最後CLI摘要／演練上下文更新後，受影響測試25 passed、5 skipped、2 warnings（4.53秒）。既有三份Research JSON重驗自動檢查通過，沒有新API呼叫或修改原FAILED。最新rehearsal-run Live仍NOT TESTED，正式提交永遠BLOCKED。詳細見reports/rehearsal_acceptance.md。
