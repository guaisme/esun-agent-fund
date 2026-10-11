# Phase 6 驗收

狀態：**PARTIALLY COMPLETED**。

獨立 Investment Agent 使用Google ADK/Gemini、截止前唯讀資料工具及型別submit接口，原Research Agent保留。自主候選、配置、理由及市場判斷進入Optimizer；合規回饋以有限次修正返回模型。失敗不生成假配置。工具參數/結果、usage、版本與錯誤保存在本機trace。

有限429/503/timeout退避、jitter、單工具逾時、整輪deadline及工具/模型呼叫上限有離線測試。SDK內建attempts限制避免重試疊加。`tests/test_investment_agent.py`、`test_workflow.py`、原Research測試持續保留。

真實Research和Investment CLI曾實際呼叫；成功與失敗均保留原始版本證據。新增程式版本沒有以舊Live結果冒充重新驗收。API配額及穩定性無法由MOCK證明；最近五日法人逐日明細缺漏不能自行補值。

`tests/test_live_investment.py` 為opt-in，普通pytest不消耗額度。缺Key或未啟用跳過。完整技術測試與Live限制見 [validation_status](../docs/validation_status.md)，金融與模型細節見本機gitignored驗收報告。
