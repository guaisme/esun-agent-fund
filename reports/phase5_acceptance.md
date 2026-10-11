# Phase 5 驗收

狀態：**PARTIALLY COMPLETED**。

Optimizer使用實際成本、周轉、不交易區間、價格壓力及有限修正，未宣稱全域最優解。目標變更須Agent重新確認，沒有手動更改金融決策。測試見 `tests/test_optimizer.py`、`test_active_share_reports.py`。

官方股票及30ETF名單有canonical驗證。AS逐基金輸出來源、內容/可得時間、方法、缺漏、最小值與狀態。缺股/重複/過期/未來持股不填零；裸數值不能正式通過。AS連續日計數綁定獨立證據日期，跳日及重跑不通過。可設定內部安全門檻，不能重設法定20%。

產業集中度只接受來源及時點驗證的分類，未知權重另列；集中度是內部警示而非法定約束。

未取得完整官方逐日ETF持股或主辦方確認計算口徑，真實AS驗收 **BLOCKED**。MOCK反例通過不代表已取得官方合規證書。最新整套實際測試見 [validation_status](../docs/validation_status.md)。
