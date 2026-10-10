# 技術驗收與發布範圍

2026-10-11離線pytest：163 passed、6 skipped、4 warnings。原15項仍通過，原測試檔內容保持。合成fixture明確標示MOCK，不當真實API結果。

實際Research與Investment API亦已執行；詳細金融回傳、投資配置、績效、模型trace、生成文件與草稿保留本機 gitignored reports及outputs。此PR只含程式碼、官方設定、測試、操作文件及技術驗收，不發布這些衍生金融資料。

正式競賽就緒為BLOCKED：官方持股與Active Share口徑、公司行動/PIT完整性、官方已結算帳本、費稅取整/缺價規則、首日提交與TPEx日曆證據未完全驗證。程式不能用修改旗標或假資料繞過。

GitHub目的地已透過官方connector核對：repository ID1413519474，owner與authenticated user ID一致，使用者有admin/push權限。自動核准審查仍拒絕發布詳細金融資料，已縮限為工程交付；原始證據未刪除、未改經其他管道發布。
