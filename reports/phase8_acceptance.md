# Phase 8 驗收

整體 PARTIALLY COMPLETED；正式提交 BLOCKED。

已實作Daily Runner、台北時區、已保存原始回應hash的TWSE日曆、窗口／期限檢查、上一交易日帳簿載入、行情更新、品質檢查、因子／排名、明確Live啟用、MCP市場證據、既有Investment/Optimizer/Compliance/D-Plan串接、報告與本機通知。OS鎖與同資料庫registry防止改output路徑繞過同日防重複；新Run ID不覆蓋舊輸出。恢復檢查來源／設定／輸入／產物hash，模型中斷不自動重發。

有限重試沿用既有reliability/HTTP策略，監督子程序總期限到期停止。FAILED/BLOCKED沒有假金融資料或非法組合備援。日誌沒有序列化環境變數或API Key。Ledger比對使用既有Reconciler，不自動認證主辦方來源真實性。

已實際執行：離線測試、當日非Live預檢（因非競賽期間COMPLETED/BLOCKED）、同日重跑拒絕、官方TWSE2026日曆下載與原檔hash、既有行情session-quality、三份PowerShell腳本解析及Install-DailyTask -DryRun。

NOT TESTED：真正註冊排程、睡眠喚醒、關機／重開機觸發、Live daily完整驗收。這次沒有安裝持續排程或啟用Live，不能用MOCK成果宣稱這些通過。Interactive登入權杖需要已登入；不宣稱登出後仍能執行。

BLOCKED：正式窗口／首日／Active Share方法與持股、費稅／缺價規則、完整公司行動／歷史時間、主辦方帳簿來源驗證。缺隔夜美股adapter、TPEx獨立日曆、法人／營收自動更新完整度；報告明列，不猜測。即使可產生草稿仍不能READY。

實際完整測試結果見validation_status；操作見docs/daily_operations.md。金融資料與私人trace留本機outputs/data，不提交Git。
