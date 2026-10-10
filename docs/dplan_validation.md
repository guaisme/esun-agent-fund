# D-Plan v4.2 驗證

使用原始schema Draft202012與FormatChecker，原始TEAM042正例與TEAM043負例均保持bytes；範例是合成，不冒用隊號。正式隊號TEAM_11586。

來源SOURCE→觀察OBS→推論INF→決策DEC→ORDER連續唯一ID；所有引用需存在，所有持股有交易或no_trade理由。來源日期是內容時間，不用fetched_at冒充公布時間。+08:00時間、來源截止、run先後、隊號/檔名/交易日分別檢查。

BUY/ADD/TRIM/SELL_ALL必須符合持股差與目標。整張公式與前日官方NAV/收盤核對，不允許手改訂單。驗證負現金、超賣、20–30檔、費稅後新增部位上限、現金<25%、C12曝險意圖與cash range。C14 funding_for只允許賣出資金支持買入，移除全部funding賣單須導致低於宣告cash lower。

只有schema過不等於正式VALID。未知帳本對帳、來源數值驗證或日曆會回報BLOCKED/UNVERIFIED；錯誤是INVALID/HARD_FAIL。無HARD_FAIL可保存draft；正式READY須全部官方閘門為真。沒有平台上傳。

策略DOCX只填原始三右欄，保留所有其他ZIP部分、版面、標籤與字型。Gemini生成名稱、主題與100–300字理念，驗證後原生Word唯讀PDF輸出，檢查全部頁面才凍結；原始範本不可覆蓋。CLI新文件仍要求使用者完成視覺QA，不能自動冒認。
