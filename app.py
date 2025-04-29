import os
from dotenv import load_dotenv, find_dotenv
from fastapi import FastAPI, Header, HTTPException, Depends, Request
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

async def monitor_disconnect(request: Request, task: asyncio.Task):
    try:
        while True:
            if await request.is_disconnected():
                task.cancel()
                break
            await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        # 本身被取消就結束
        pass

@app.post("/scrape")
async def scrape(
    request: Request,
    payload: ScrapeRequest,
    token: str = Depends(verify_token)
):
    """
    接受 JSON:
    {
      "task": "...",
    }
    Header: X-API-Token: <你設定的 token>
    回傳 JSON 陣列
    """
    # 每次呼叫都可以新建 Context，避免跨請求干擾
    ctx = BrowserContext(browser=browser, config=context_cfg)

    agent = Agent(
        task=payload.task,
        message_context=
        """
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
        """,
        llm=llm,
        browser_context=ctx,
        planner_llm=planner_llm,
        use_vision_for_planner=False,
        planner_interval=4
    )

    try:
        result = await agent.run()
        return json.loads(result.final_result())

    finally:
        # 無論如何都要關掉 browser context
        await ctx.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
