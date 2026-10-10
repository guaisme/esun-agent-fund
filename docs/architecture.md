# Phase 3–7 架構

資料供應商 → SQLite/Parquet → PIT snapshot → FeatureEngine → AlphaModel → Investment Agent 唯讀工具 → 型別自主決策 → PortfolioOptimizer → Agent回饋 → DPlanGenerator → DPlanValidator → 本地草稿。

執行行情與決策資料分離。PortfolioSimulator 接收當日成交股數/金額與收盤，模型工具無法查詢它們。依前日官方收盤及NAV推導整張訂單，費稅逐筆計算，收盤估值；任一非法交易整日回滾並保留新日估值，不部分偷渡交易。

Research Agent 保持獨立，仍能由ADK Web查MCP。Investment Agent只有唯讀決策工具與型別submit工具，沒有下單或平台上傳權限。Source archive、實際模型/usage、trace、策略與因子版本均保存在本地outputs。

不使用PostgreSQL、Redis、Docker、券商API或多Agent架構。
