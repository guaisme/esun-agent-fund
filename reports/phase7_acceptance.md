# Phase 7 驗收

狀態：**PARTIALLY COMPLETED**；正式輸出 **BLOCKED**。

主辦方v4.2 schema與正負例保留原始bytes。生成器使用捕捉的Agent決策，由前日NAV/收盤/股數機械推導訂單；驗證引用唯一連續、證據鏈、每個持股覆蓋、action、時間/時區、現金與資金用途。`tests/test_dplan.py`、`test_workflow.py` 覆蓋正負與失敗反例。

新run保存完整決策輸入、設定、typed decision、原始trace與source ZIP；replay檢查來源版本、檔案雜湊，再重算Optimizer與完整D-Plan，逐bytes/驗證比較。修改訂單、缺證據或版本不同不回報REPRODUCED。Replay不是新Gemini執行，也不是外部認證。

官方策略Word範本曾由實際Agent文字填入、Word唯讀PDF匯出及視覺QA；實際內容與trace保留本機。缺原始範本、QA或凍結證據時不自動宣稱完成。

正式submission-ready受官方持股/口徑、PIT/公司行動、已結算帳本及部分規則證據阻擋。草稿、無HARD_FAIL或schema通過不等於READY。沒有平台自動上傳、券商交易或main合併。技術驗收見 [validation_status](../docs/validation_status.md)。
