# Phase 4 驗收

狀態：**PARTIALLY COMPLETED**。

FeatureEngine、FactorCalculator、FeatureSnapshot、品質檢查、縮尾/標準化/缺值規則、三個基準與walk-forward已實作。訓練/驗證/測試區間及rolling競賽長度區間有實際執行紀錄，固定參數、保留失敗/部分結果。`tests/test_features.py`、`test_backtest_pipeline.py`、`test_acceptance_gates.py` 檢查時點、缺值、區間及實際執行角色。

資料模式明確分成strict_verified、research_assumptions、synthetic_fixture。未知營收公布時間及同日收盤/量不進入上午決策。來源不足的相對強弱保持缺值，不冒充市場指數。

尚未完成正式歷史驗收：完整公布及修訂時間、公司行動、法人長歷史、各股營收及ETF持股。固定當年股票名單有選樣偏誤。實際條件式績效、回測來源與限制保留本機報告；這份工程報告不聲稱獲利或所有區間成功。

操作：`python -m src.pipeline.cli backtest --start YYYY-MM-DD --end YYYY-MM-DD --mode research_assumptions`；`compare`、`walk-forward`。設定見 `config/alpha_strategy.yaml`，方法見 [strategy](../docs/strategy.md)。完整測試結果見 [validation_status](../docs/validation_status.md)。
