import os
from dotenv import load_dotenv, find_dotenv
from fastapi import FastAPI, Header, HTTPException, Depends, Request, Response
from pydantic import BaseModel
import json
import asyncio
from browser_use import Controller, ActionResult, Browser

# 載入 env
dotenv_path = find_dotenv()  # or os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path, override=True)

# 你原本的 Agent、BrowserContext etc. import
from browser_use import Agent
from src.utils import utils
from browser_use.browser.browser import Browser, BrowserConfig, BrowserContext, BrowserContextConfig

# 取 env
API_TOKEN = os.getenv("API_TOKEN")
AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_KEY      = os.getenv("AZURE_OPENAI_API_KEY")

# 建立 FastAPI
app = FastAPI(title="Browser‑use Scraping API")
controller = Controller()

# Body schema
class ScrapeRequest(BaseModel):
    task: str
    #message_context: str

# 取 token 驗證
def verify_token(x_token: str = Header(..., alias="X-API-Token")):
    if x_token != API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid API Token")
    return x_token

# 初始化 LLM & BrowserContext
llm = utils.get_llm_model(
    provider="azure_openai",
    model_name=os.getenv("LLM_MODEL", "gpt-4.1"),
    temperature=os.getenv("LLM_TEMPERATURE", 0.0),
    base_url=AZURE_ENDPOINT,
    api_key=AZURE_KEY,
    enable_memory=True,
)
planner_llm = utils.get_llm_model(
    provider="azure_openai",
    model_name=os.getenv("PLANNER_LLM_MODEL", "gpt-4.1-mini"),
    temperature=os.getenv("PLANNER_LLM_TEMPERATURE", 0.0),
    base_url=AZURE_ENDPOINT,
    api_key=AZURE_KEY,
    enable_memory=True,
)

browser = Browser(
    BrowserConfig(
        headless=True,
        disable_security=True,
    )
)
context_cfg = BrowserContextConfig(
    cookies_file="./cookies.json",
    wait_for_network_idle_page_load_time=3.0,
    browser_window_size={'width': 1920, 'height': 5000},
    locale='zh-TW',
    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/85.0.4183.102 Safari/537.36',
    highlight_elements=True,
    viewport_expansion=500,
)

parser_default_message_context = """
    You are an expert web‑scraping assistant
    1. 從 task 給予的起始頁面開始，自動翻頁直到沒有「下一頁」為止。
    2. 在每個列表頁面抓出所有文章項目，並對它們做進入，如果像的 url 是相對路徑時，需把當前的 baseUrl 放入到 url 內以及擷取完整的 url、title、content，再回到列表。
    3. 以 JSON 陣列形式輸出，每筆記錄包含 { url, title, content }。
    4. 格式如下 [
        { "url": "...", "title": "...", "content": "..." },
        { "url": "...", "title": "...", "content": "..." },
        …
        ]
"""

parser_url_message_context = """
    You are an expert web‑scraping assistant
    1. 從給定的起始列表頁，收集頁面中的所有文章項目連結（若為相對路徑請自動拼成完整 URL），只專注在『翻頁＋收集連結』的這件事。.
    2. output format: [
        { "url": "...", "title": "..." },
        { "url": "...", "title": "..." },
        …
        ]
    3. 其餘資料不輸出
"""

parser_post_message_context = """
    You are an expert web‑scraping assistant specialized
    1. 完整擷取網址文章原文，務必不要省略任何段落或字句，不要進行摘要或改寫，完全以原文呈現。
    2. output format: 
        { "url": "...", "title": "...", "content": "...", "content_is_omit": true/false }
    3. content_is_omit: true 代表內容有省略，false 代表內容完整
    4. 其餘資料不輸出
"""

parser_pages_message_context = """
    You are an expert web‑scraping assistant
    1. 接收使用者提供的「起始頁面 URL」。
    2. 解析出所有與分頁（pagination）相關的連結
        a. 例如：下一頁、上一頁、第一頁、最後一頁、分頁數字等。
        b. 將解析出來的連結記錄下來，供後續使用。
    3. 透過已知的分頁去取得其餘的所有的分頁（pagination）相關連結
        a. 使用遞迴且最少頁面跳轉的方式進行分頁解析
    4. 若連結為相對路徑，自動補全成絕對 URL。
        a. 紀錄已拜訪的 URL，避免無限迴圈。
    5. 依據最大分頁數量限制，限制最多解析的分頁數量，並且補齊不完整的分頁連結。
    6. 將結果包含補齊的分頁連結輸出 JSON 陣列的格式如下：
        [
            { "url": "..." },
            { "url": "..." },
            …
        ]
    7. 其餘資料不輸出
"""

@app.get("/", summary="Liveness probe")
async def health() -> dict:
    """
    簡單回應 service 狀態
    """
    return {"status": "ok"}

async def run_agent(
    task: str,
    message_context: str,
    llm,
    planner_llm,
    browser,
    context_cfg
):
    # 每次呼叫都可以新建 Context，避免跨請求干擾
    ctx = BrowserContext(browser=browser, config=context_cfg)
    try:
        agent = Agent(
            task=task,
            message_context=message_context,
            llm=llm,
            browser_context=ctx,
            planner_llm=planner_llm,
            use_vision_for_planner=False,
            planner_interval=4,
            tool_calling_method="function_calling",
        )
        result = await agent.run()
        return Response(content=result.final_result(), media_type="application/json")
    finally:
        await ctx.close()
        await browser.close()

def make_endpoint(path: str, message_context: str):
    @app.post(path)
    async def endpoint(
        payload: ScrapeRequest,
        token: str = Depends(verify_token),
    ):
        return await run_agent(
            task=payload.task,
            message_context=message_context,
            llm=llm,
            planner_llm=planner_llm,
            browser=browser,
            context_cfg=context_cfg
        )
    return endpoint

# 依序建立三支路由
make_endpoint("/scrape", parser_default_message_context)
make_endpoint("/post", parser_post_message_context)
make_endpoint("/url", parser_url_message_context)
make_endpoint("/pages", parser_pages_message_context)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
