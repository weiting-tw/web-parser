import os
from dotenv import load_dotenv, find_dotenv
from fastapi import FastAPI, Header, HTTPException, Depends, Request, Response
from pydantic import BaseModel
import json
import asyncio

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
    temperature=1,
    base_url=AZURE_ENDPOINT,
    api_key=AZURE_KEY,
    enable_memory=True,
)
planner_llm = utils.get_llm_model(
    provider="azure_openai",
    model_name=os.getenv("PLANNER_LLM_MODEL", "o3-mini"),
    temperature=1,
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
    You are an expert web‑scraping assistant specialized in generating browser‑use scripts. 
    Your output must be valid JavaScript code using the browser‑use API (https://docs.browser‑use.com).
    The script should:
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
    You are an expert web‑scraping assistant specialized in generating browser‑use scripts. 
    Your output must be valid JavaScript code using the browser‑use API (https://docs.browser‑use.com).
    The script should:
    1. 從給定的起始列表頁一直翻到沒有『下一頁』為止，並在每頁收集所有文章項目的連結（若為相對路徑請自動拼成完整 URL），只 focus 在『翻頁＋收集連結』這件事。.
    2. output format: [
        { "url": "...", "title": "..." },
        { "url": "...", "title": "..." },
        …
        ]
"""

parser_post_message_context = """
    You are an expert web‑scraping assistant specialized in generating browser‑use scripts. 
    Your output must be valid JavaScript code using the browser‑use API (https://docs.browser‑use.com).
    The script should:
    1. 接受一個文章 url，開啟它後擷取 { url, title, content }。只做這件事，不要翻頁、不用處理多個 URL。.
    2. output format: 
        { "url": "...", "title": "...", "content": "..." }
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
            planner_interval=4
        )
        result = await agent.run()
        return Response(content=result.final_result(), media_type="application/json")
    finally:
        await ctx.close()

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
