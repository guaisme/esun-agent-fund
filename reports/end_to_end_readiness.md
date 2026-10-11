# 整合驗收與就緒

整體 **PARTIALLY COMPLETED**；正式競賽就緒 **BLOCKED**。

逐階段報告：[Phase 3](phase3_acceptance.md)、[Phase 4](phase4_acceptance.md)、[Phase 5](phase5_acceptance.md)、[Phase 6](phase6_acceptance.md)、[Phase 7](phase7_acceptance.md)。本報告只公開工程狀態；原始金融/模型證據保留本機。

完整離線MOCK多日流程涵蓋因子、排名、型別決策、成本/配置檢查、D-Plan、語意驗證、成交、NAV、帳簿保存及次日接續。另有手算黃金帳務重播；二者不宣稱真實模型成功。新run還可用 `replay-decision` 重現捕捉決策的確定式處理。每輪輸入及來源ZIP保存實際版本，不執行不可信舊程式碼。

最新實際離線結果、跳過原因與環境限制見 [validation_status](../docs/validation_status.md)。Live必須明確啟用，新版本未重跑Live時標記NOT TESTED。舊資料缺上下文不推測補齊。

正式閘門包括：競賽期間/日曆、嚴格資料可得、觀察證據、公司行動、已結算持股、完整Active Share、費稅規則、凍結策略與實際模型紀錄。不可只改布林旗標當作取得證據。

外部缺口：完整逐日官方ETF持股及AS方法、可驗證歷史公布/修訂與公司行動、主辦方已結算帳本、費稅取整/缺價規則、首日提交差異及TPEx日曆。沒有這些資料時保留BLOCKED。

所有Windows根目錄操作指令見README及 [operations](../docs/operations.md)。不需要Codex CLI，不改Conda全域設定。Git只發布程式碼、官方設定、MOCK測試與工程文件，不發布私人金融資料。Draft PR未合併main。
