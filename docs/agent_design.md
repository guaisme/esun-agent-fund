# Investment Agent

`esun_investment_agent`與Research Agent分開。Google ADK ReliableResearchAgent封裝官方Gemini SDK，AI Studio路徑；模型由GEMINI_MODEL設定。

工具提供已結算帳本、PIT snapshot、因子、排名、法人、營收、regime證據、ETF狀態、成本、optimizer與驗證、可得性。工具閉包深拷貝上下文，沒有任意SQL/檔案/未來行情權限。

Gemini自主決定regime、候選、目標權重、各股理由、現金區間與研究需求。`submit_investment_decision`驗證完整JSON與實際obs refs，無效會提供錯誤讓模型在工具/LLM次數上限內修正。程式不修補缺失理由、不補股票或權重。官方機械訂單數量由Decimal程式產生，LLM不輸出委託股數。

AI Studio上的ADK output_schema配合工具會使用fallback set_model_response，曾因動態字典schema反覆失敗；現採官方Function Tool提交型別JSON。ETF文件無工具，使用SDK response_json_schema並有限次驗證回饋，沒有截斷模型文字。

429/503/timeout有限重試，SDK attempts=1避免疊加；已有stream輸出不重播。整體dry-run deadline跨決策修正；工具與LLM上限阻止無限回圈。實際quota失敗保留FAILED，不退回示範持股。prompt/config/git/hash與原始model/tool/usage紀錄保留，不偽造tokens。
