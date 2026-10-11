# Phase 3 驗收

狀態：**PARTIALLY COMPLETED**。本報告是工程驗收；實際金融證據保留本機。

已驗證：Decimal費稅、整張/超賣/重複訂單、官方成交金額除成交股數的執行價格、收盤NAV、整日回滾、價格漂移寬限、警告及DQ、公司行動policy、保存帳簿及多日接續。具體離線案例見 `tests/test_simulator.py` 與明確標示合成的 `tests/fixtures/competition_case.json`。

Active Share由同日可得基準及實際NAV重新計算，帳簿獨立保存AS證據日期。回滾後使用保留部位計數；缺日證據不能接成連續違規。裸AS數值僅能在明確fixture使用，不能認證正式合規。

尚未驗證：費稅取整、缺成交價規則、完整公司行動與官方成交均價定義。已有來源metadata不等於上述官方確認。部分帳務摘要分布在DailyNAV/SimulationResult而不是單一PortfolioState；不可用原始檔案存在代替逐欄位完整性驗收。

執行：`python -m pytest tests/test_simulator.py tests/test_active_share_reports.py -q`；`python -m src.pipeline.cli simulate`。整套最新實際結果見 [validation_status](../docs/validation_status.md)。
