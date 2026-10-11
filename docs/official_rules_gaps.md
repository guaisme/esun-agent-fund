# 正式規則缺口

正式就緒BLOCKED。下表不是自訂替代規則。

| 項目 | 分類 | 現行處理與需要證據 |
|---|---|---|
| 10/26 TWSE休市／首日申報 | 主辦方答覆 | 日曆不交易；待確認是否仍須申報、首個建倉日 |
| 正式提交窗口 | 主辦方答覆 | operations預設null；07:00是排程，08:30是內部期限，均不冒充正式規定 |
| Active Share raw／前十正規化 | 主辦方答覆 | 正式method未確認不通過；保留逐ETF結果與連續日證據 |
| 30 ETF逐日實際持股 | 真實資料＋來源審閱 | 本次accepted=0；逐基金缺口可用benchmark-status查看，不填零 |
| 手續費與證交稅取整 | 主辦方答覆 | 既有研究假設保留；正式ROUNDING flag不修改 |
| 無成交價／停牌委託 | 主辦方答覆＋真實資料 | 缺價阻擋，不能舊價成交 |
| 官方結算對本地帳簿 | 真實資料＋程式 | 既有Reconciler比對原檔hash與現金/股數/NAV；不自動驗證整理檔真實性，不覆蓋帳簿 |
| 公司行動／除權息 | 真實資料＋主辦方答覆 | 有處理policy，完整事件覆蓋與零股細節不足，保留BLOCKED |
| 歷史公布／修訂時間 | 真實資料 | 首次觀察只能證明之後可知；不可回填成歷史published_at |
| empty_unverified | 程式＋官方日曆 | 新session-quality核對TWSE並區分休市／衝突；不覆寫原紀錄 |
| partial、7828／3718缺漏 | 真實資料＋程式 | 列缺股與日期，不猜上市／停牌原因 |
| TPEx獨立日曆 | 真實資料 | 官方[開休市頁](https://www.tpex.org.tw/zh-tw/announce/market/holiday.html)本次web讀取403，未當作驗證成功 |
| 排程與恢復 | 程式＋本機操作驗收 | 有腳本與測試；實際註冊、睡眠、重開機觸發NOT TESTED |
| 隔夜美股 | 真實來源Adapter | 首版明列缺少；MCP台股概況不冒充隔夜美股 |

ETF來源研究：證交所確認主動ETF須每日揭露實際投資組合，見[官方產品說明](https://wwwc.twse.com.tw/zh/products/securities/etf/products/active-list.html)。[官方簡介](https://accessibility.twse.com.tw/downloads/zh/ETF/activeETF.pdf)說明於投信網站揭露。逐投信網址、欄位、單位、歷史保存及使用條款需各別確認；沒有假設一個通用批量API。ToAlpha只作受限研究，不批量匯出ETF資料建庫。

已有可替換BenchmarkProvider與ReviewedFileBenchmarkProvider。每檔normalized export保留ticker、holdings（ticker/weight分數單位）、weight_unit=fraction、complete_equity_holdings、content_as_of、available_at、first_observed_at、source_url、original_file、original_sha256、reviewed_source_domain、reviewed_original_sha256。原檔須在同目錄範圍，缺檔／hash變動／重複／過期／未來／單位不明／合成來源均拒絕。首次抓取之前的歷史可得時間尚不支援認證，保守拒絕。review是本機來源審閱紀錄，不是官方Active Share方法確認或外部真實性簽章。
