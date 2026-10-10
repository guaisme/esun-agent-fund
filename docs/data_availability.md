# 資料可得性與來源

目前實際資料是 TWSE MI_INDEX、TPEx dailyQuotes、TWSE T86、TPEx insti/dailyTrade，以及FinMind的2330月營收樣本。完整來源與授權限制延續Phase 2；ToAlpha不作大量歷史庫。

每列區分 observation_date、published_at/available_at、fetched_at。今日下載只能證明今日已見，不能反推過去公布時間。預設strict_verified排除未知；research_assumptions僅能顯式假設前日價在次日可得，不默認法人或營收公布日期。synthetic_fixture只允許標示的測試資料。

08:30資料截止排除同日收盤及量，snapshot可以保留所有被排除的原因。資料版本修訂、停牌缺日、最新行情過舊會留下warning/缺值，不能補價。未知市場或產業序列的相對強弱不計算。

現有150股共22632價格列、152個觀察交易日，2026-03-02至10-08；148股至少120列，7828為117、3718為24。法人只有8日1200列；2330營收10月資料published_at皆null。這不等於完整嚴格歷史資料。

官方TWSE年度行事曆已真實下載，2026-10-26補假，競賽首個交易日10/27、共24日；與152個價格觀察日無衝突。TPEx獨立日曆與主辦方首日提交規則尚待驗證。
