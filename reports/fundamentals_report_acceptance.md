# 決策可讀報告與基本面閘門驗收

- COMPLETED：保存演練離線匯出逐檔理由、引用觀察、權重、委託股數、估計現金及成本，不生成成交／績效紀錄。
- COMPLETED：區分目標剩餘現金與Optimizer成本／整張調整後現金，保留原cash_ratio相容欄位。
- COMPLETED：FinMind已觀測營收計算YoY／MoM；缺分母及零分母保留null，發布時間不推定。
- COMPLETED：官方150檔依序下載營收、原始快取／冪等重跑、連續3個失敗停止並保留未嘗試清單。
- COMPLETED：Live演練預設要求全部150檔最新月營收、連續同比／環比／加速度及5／20日法人因子；不足在外部API前停止。--allow-price-only為明確不完整演練。
- COMPLETED：完整模式納入營收／法人排名、個股引用觀察與模型工具，禁止從價格推斷獲利。
- COMPLETED：270 passed、8 skipped、4 warnings；全套離線回歸，原Research Agent測試仍通過。
- NOT TESTED：新增批次營收下載及完整資料Live Gemini演練，沒有將Mock宣稱成真實API。
- PARTIALLY COMPLETED：基本面範圍目前是月營收與法人；完整季報／EPS、資產負債、現金流仍未接入。
- BLOCKED：正式提交、Active Share基準、庫存對帳、正式時間定義及其他既有閘門。

測試修正過程：曾因Windows沙箱asyncio停滯改用核准執行；OneDrive暫存原子替換遭鎖定，改用本機Temp。曾發現報告讀取Optimizer dataclass的型別錯誤，修正後回歸通過；新增Mock測試補齊行情前置条件才驗證基本面阻擋。以上失敗不列為通過。
