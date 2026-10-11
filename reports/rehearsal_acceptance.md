# 競賽前演練工程驗收

實作COMPLETED（程式及離線工程檢查），最新入口真實Live NOT TESTED，正式提交BLOCKED。

新增來源驗證：比對工具實際來源名稱、機構別名或HTTPS連結，不接受只有source／來源／ToAlpha標籤。以使用者提供的三份真實既有JSON重驗，三項自動檢查通過，沒有新API請求或覆寫原FAILED。五日逐日法人明細仍PARTIALLY COMPLETED，不把來源修正冒充資料補齊。

新增rehearsal-run明確--live、研究初始現金／本地帳簿、真實SQLite／Parquet嚴格PIT、150檔最新價格／20檔排名預檢、MCP市場證據、Gemini決策、既有Optimizer／D-Plan／驗證、獨立Run ID／registry／OS鎖／900秒監督期限。正式daily-run與所有正式閘門未修改；重跑需明確--rerun。

完整回歸：264 passed、8 skipped、4 warnings，51.80秒。新增21項來源／演練離線MOCK測試；新增1項Live演練預設skip。最後精簡CLI與模型演練標記後，受影響測試25 passed、5 skipped、2 warnings，4.53秒。原有測試保留。

初次受沙箱temp／Windows asyncio限制的執行不是成功；切換隔離暫存目錄與允許本機socketpair後測試完成。MOCK完整串接首次缺market欄位而失敗，補完整fixture後發現一般audit JSON將Decimal存成字串，演練文件改用原typed決策及metadata由Generator生成，未手改股數或放寬Schema。原始正式驗證仍保留，另存工程驗證與數值型別正確的audit。

非Live CLI已實際執行：COMPLETED／BLOCKED、NOT_ENABLED。最新入口未呼叫真實Gemini／MCP；使用者原Research證據不是此入口Live成功證明。詳細資料及API證據留本地忽略目錄，不提交Git。

操作見docs/precompetition_rehearsal.md。正式ETF／Active Share口徑、公司行動、結算／歷史時間與窗口缺口仍BLOCKED。
