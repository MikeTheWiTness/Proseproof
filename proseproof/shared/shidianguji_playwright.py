"""识典古籍文本提取 - Playwright headless 浏览器方案（Issue #3）

软依赖设计：Playwright 不可用时静默不可用，调用方通过 is_playwright_available() 判断。

稳定要点：
- wait_until="domcontentloaded"（不能用 networkidle——统计心跳导致超时）
- 等 article.chapter-reader p 出现
"""
import re
import logging

_log = logging.getLogger(__name__)


# ============================================================
# API
# ============================================================

def is_playwright_available():
    """检测 Playwright + Chromium 是否就绪。

    Returns:
        bool: True 表示可用，False 表示不可用（不抛异常）
    """
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


def extract_chapter(book_id, chapter_id, timeout=30000):
    """用 Playwright 渲染识典古籍详情页，提取正文。

    Args:
        book_id: 书籍 ID（如 "SK0724"）
        chapter_id: 章节 ID（如 "1l9yzpxkqkr3b"）
        timeout: 页面加载超时（毫秒），默认 30 秒

    Returns:
        dict: {"title": str, "text": str} 或 None（失败时）
    """
    if not is_playwright_available():
        return None

    url = f"https://www.shidianguji.com/book/{book_id}/chapter/{chapter_id}"

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/120 Safari/537.36"
            )
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)

            # 等待正文段落出现
            try:
                page.wait_for_selector("article.chapter-reader p", timeout=15000)
            except Exception:
                pass  # 可能页面结构不同

            title = ""
            if page.locator("article.chapter-reader h4").count():
                title = page.locator("article.chapter-reader h4").first.inner_text()

            paras = page.locator("article.chapter-reader p").all_inner_texts()
            browser.close()

            if not paras:
                return None

            text = "\n".join(paras)
            return {"title": title.strip(), "text": text}

    except Exception as e:
        _log.warning(f"extract_chapter 失败: {e}")
        return None


def search_and_extract(keywords, timeout=60000):
    """搜索识典古籍并提取第一条结果的全文。

    优先使用纯 HTTP 解析搜索结果页（复用现有 _parse_shidianguji_search 逻辑），
    获取 /book/xxx/chapter/xxx 链接后调用 extract_chapter。

    Args:
        keywords: 搜索关键词（建议取前 20 字）
        timeout: 整体超时（毫秒）

    Returns:
        str: 提取到的正文文本，或 None
    """
    if not keywords or not keywords.strip():
        return None

    # Step 1: 搜索定位章节 URL
    detail_url = _search_detail_url(keywords, timeout)
    if not detail_url:
        return None

    # Step 2: 解析 book_id + chapter_id
    parts = [p for p in detail_url.split("/") if p]
    try:
        book_idx = parts.index("book")
        chapter_idx = parts.index("chapter")
        book_id = parts[book_idx + 1]
        chapter_id = parts[chapter_idx + 1]
    except (ValueError, IndexError):
        _log.warning(f"无法解析章节 URL: {detail_url}")
        return None

    # Step 3: 提取正文
    result = extract_chapter(book_id, chapter_id, timeout)
    if result:
        return result["text"]
    return None


# ============================================================
# 内部实现
# ============================================================

def _search_detail_url(keywords, timeout=30000):
    """搜索识典古籍，返回第一条结果的详情页路径。

    Args:
        keywords: 搜索关键词
        timeout: 超时时间

    Returns:
        str: "/book/xxx/chapter/xxx" 路径，或 None
    """
    import urllib.parse
    import requests

    search_url = f"https://www.shidianguji.com/search/{urllib.parse.quote(keywords)}"

    # 策略 A：纯 HTTP（最快）
    try:
        resp = requests.get(search_url, timeout=20, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/120 Safari/537.36"),
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        resp.encoding = "utf-8"
        html = resp.text
    except Exception:
        html = ""

    if html:
        urls = re.findall(r'(/book/[^"]+chapter[^"]+)', html)
        if urls:
            return urls[0]

    # 策略 B：Playwright 渲染搜索页（HTTP 拿不到链接时）
    if not is_playwright_available():
        return None

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/120 Safari/537.36"
            )
            page.goto(search_url, wait_until="domcontentloaded", timeout=timeout)
            try:
                page.wait_for_selector("a[href*='/book/']", timeout=10000)
            except Exception:
                pass
            html = page.content()
            browser.close()
    except Exception:
        return None

    urls = re.findall(r'(/book/[^"]+chapter[^"]+)', html)
    return urls[0] if urls else None
