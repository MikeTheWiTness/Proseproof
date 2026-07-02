"""
联网搜索与网页抓取工具模块

提供两个 LangChain BaseTool：
- WebFetchTool: 抓取指定 URL 的正文文本
- WebSearchTool: 搜索互联网获取结果列表，支持多后端切换
"""

import re
import json
import requests
from html import unescape as _html_unescape
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


# ============================================================
# HTML 文本提取（共用）
# ============================================================

def _extract_text(html: str) -> str:
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<head[^>]*>.*?</head>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', html)
    text = _html_unescape(text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    return text.strip()


def _truncate(text: str, limit: int = 6000) -> str:
    if len(text) > limit:
        return text[:limit] + "\n...[截断]"
    return text


# ============================================================
# WebFetchTool — 抓取指定网页
# ============================================================

class WebFetchParams(BaseModel):
    url: str = Field(
        description="要抓取的网页URL。示例: https://www.shidianguji.com/search/关键词 或 https://sou-yun.cn/QueryPoem.aspx?q=诗句"
    )


class WebFetchTool(BaseTool):
    name: str = "web_fetch"
    description: str = (
        "抓取指定网页的文本内容并提取正文。"
        "支持：搜韵网(https://sou-yun.cn/)古诗检索、识典古籍(https://www.shidianguji.com/)原文搜索、"
        "以及任意网页的正文抓取。返回纯文本，超过6000字自动截断。"
    )
    args_schema: type[BaseModel] = WebFetchParams

    def _run(self, url: str) -> str:
        try:
            if "sou-yun.cn" in url:
                return self._fetch_souyun(url)
            if "shidianguji.com" in url:
                return self._fetch_shidianguji(url)
            return self._fetch_generic(url)
        except Exception as e:
            return f"[网页抓取失败: {e}]"

    def _fetch_generic(self, url: str) -> str:
        resp = requests.get(url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.encoding = resp.apparent_encoding or "utf-8"
        resp.raise_for_status()
        text = _extract_text(resp.text)
        if not text.strip():
            return "[网页内容为空，可能需登录或该页面无文本内容]"
        if "0 条搜索结果" in text or "找到 0 条" in text or "未找到" in text:
            return "[搜索结果为空：该网站未收录此内容]"
        return _truncate(text)

    def _fetch_souyun(self, url: str) -> str:
        import urllib.parse as _up
        parsed = _up.urlparse(url)
        query = _up.parse_qs(parsed.query).get("q", [""])[0]
        if not query:
            return "[搜韵网搜索需要提供 q 参数，如 https://sou-yun.cn/QueryPoem.aspx?q=诗句]"

        s = requests.Session()
        # 尝试直接 GET（部分场景下 QueryString 即生效）
        direct_resp = s.get(
            f"https://sou-yun.cn/QueryPoem.aspx?q={_up.quote(query)}",
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
        )
        direct_text = _extract_text(direct_resp.text)
        # 如果直接 GET 拿到了有效内容（搜索框+KTV），说明可用
        if len(direct_text.strip()) > 200 and ("Keywords" in direct_resp.text or query in direct_text):
            return _truncate(direct_text)

        # 回退：尝试获取 ViewState 再 POST（Legacy 兼容）
        resp = s.get("https://sou-yun.cn/QueryPoem.aspx", timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        viewstate = re.search(r'id="__VIEWSTATE" value="([^"]+)"', resp.text)
        if not viewstate:
            return "[搜韵网暂时不可用，请使用模型自身知识判断]"

        data = {
            "__VIEWSTATE": viewstate.group(1),
            "KeywordTextBox": query,
            "_ContentKeys": query,
            "QueryButton": "检索",
            "ShowMatchedClauseOnly": "on",
        }
        resp2 = s.post("https://sou-yun.cn/QueryPoem.aspx", data=data, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        text = _extract_text(resp2.text)
        if query not in text.replace(" ", ""):
            return "[搜韵网未收录此内容，请使用模型自身知识判断]"
        return _truncate(text)

    def _fetch_shidianguji(self, url: str) -> str:
        import urllib.parse as _up
        parsed = _up.urlparse(url)
        path = parsed.path

        query = ""
        if "search" in path:
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 2 and parts[-1] != "search":
                query = _up.unquote(parts[-1])
        if not query:
            query = _up.parse_qs(parsed.query).get("q", [""])[0]
        if not query:
            query = _up.parse_qs(parsed.query).get("keyword", [""])[0]

        if not query:
            return "[识典古籍搜索需要提供关键词，如 https://www.shidianguji.com/search/关键词 或 ?q=关键词]"

        search_url = f"https://www.shidianguji.com/search/{_up.quote(query)}"
        try:
            resp = requests.get(search_url, timeout=20, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "zh-CN,zh;q=0.9",
            })
            resp.encoding = "utf-8"
        except Exception as e:
            return f"[识典古籍搜索失败: {e}]"

        results = _parse_shidianguji_search(resp.text)
        if not results:
            return "[搜索结果为空：识典古籍未收录此内容]"

        first = results[0]
        detail_url = first["url"]
        if detail_url.startswith("/"):
            detail_url = "https://www.shidianguji.com" + detail_url

        try:
            resp2 = requests.get(detail_url, timeout=20, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "zh-CN,zh;q=0.9",
            })
            resp2.encoding = "utf-8"
        except Exception as e:
            return f"[识典古籍详情页获取失败: {e}]"

        text = _parse_shidianguji_detail(resp2.text)
        if not text.strip():
            text = _extract_text(resp2.text)
        if not text.strip():
            return "[识典古籍详情页内容为空]"

        title = first.get("title", "")
        header = f"【识典古籍】{title}\n\n" if title else "【识典古籍】\n\n"
        return _truncate(header + text)

    async def _arun(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError


def _parse_shidianguji_search(html: str):
    results = []
    # 新版 SPA 结构：<a href="/book/ID/chapter/..." class="search-paragraph">...嵌套div...</a>
    # 旧版结构：<a href="/book/...">标题</a>
    # 抓取整个 <a> 标签，优先用其可见文本作 title（旧版书名），取不到再回退 URL book_id
    anchor_matches = re.findall(r'<a\s+[^>]*href="(/book/[^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL)
    seen = set()
    for href, inner in anchor_matches:
        if href not in seen:
            seen.add(href)
            # 从 <a> 标签内部提取可读文本（去除嵌套标签后）
            title_text = re.sub(r'<[^>]+>', '', inner).strip()
            # 从 URL 中提取书名作回退：/book/BOOK_ID/chapter/...
            parts = [p for p in href.split("/") if p]
            book_id = parts[1] if len(parts) >= 2 else href
            results.append({"title": title_text or book_id, "url": href})
    return results[:10]


def _parse_shidianguji_detail(html: str) -> str:
    content_selectors = [
        r'<div[^>]*class="[^"]*content[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]*class="[^"]*text[^"]*"[^>]*>(.*?)</div>',
        r'<article[^>]*>(.*?)</article>',
        r'<div[^>]*id="content"[^>]*>(.*?)</div>',
    ]
    for selector in content_selectors:
        m = re.search(selector, html, re.DOTALL)
        if m:
            text = _extract_text(m.group(1))
            if len(text.strip()) > 20:
                return text.strip()
    return _extract_text(html).strip()


# ============================================================
# WebSearchTool — 搜索引擎
# ============================================================

_SEARCH_CONFIG = {
    "ddgs": {
        "label": "DuckDuckGo",
        "note": "国际通用，部分网络环境可能超时",
    },
    "baidu": {
        "label": "百度",
        "note": "中文搜索最优，无需配置",
    },
}


class WebSearchParams(BaseModel):
    query: str = Field(description="搜索关键词，建议使用空格分隔的术语，中英文均可")
    backend: str = Field(
        default="ddgs",
        description="搜索后端: 'ddgs'（DuckDuckGo，默认）, 'baidu'（百度）",
    )


class WebSearchTool(BaseTool):
    name: str = "web_search"
    description: str = (
        "搜索互联网获取最新信息。返回每条结果的标题、摘要和URL。"
        "适用于：查找最新说法、验证专业知识、检索不在模型训练数据内的信息。"
        "获取搜索结果的URL后可进一步调用 web_fetch 抓取详情页全文。"
        "后端: ddgs（国际） / baidu（中文最佳）。超时或无结果会返回明确提示，此时应使用模型自身知识。"
    )
    args_schema: type[BaseModel] = WebSearchParams

    def _run(self, query: str, backend: str = "ddgs") -> str:
        backend = backend.lower()
        if backend == "baidu":
            return self._search_baidu(query)
        return self._search_ddgs(query)

    # ---- DuckDuckGo 后端 ----

    def _search_ddgs(self, query: str) -> str:
        try:
            from ddgs import DDGS
        except ImportError:
            return json.dumps({
                "error": "ddgs 未安装，请执行: pip install ddgs",
                "hint": "或切换 backend='baidu'",
            }, ensure_ascii=False)

        try:
            results = list(DDGS().text(query, max_results=5))
        except Exception as e:
            return json.dumps({
                "error": f"DuckDuckGo 搜索失败: {e}",
                "hint": "当前网络可能无法访问，尝试 backend='baidu' 或使用模型自身知识",
            }, ensure_ascii=False)

        if not results:
            return "[DuckDuckGo 搜索无结果]"

        items = []
        for r in results:
            items.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            })
        return json.dumps(items, ensure_ascii=False)

    # ---- 百度后端 ----

    def _search_baidu(self, query: str) -> str:
        try:
            resp = requests.get(
                "https://www.baidu.com/s",
                params={"wd": query, "rn": "5"},
                timeout=15,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
            )
            resp.encoding = "utf-8"
            if resp.status_code != 200:
                return json.dumps({
                    "error": f"百度搜索返回 HTTP {resp.status_code}",
                    "hint": "可尝试 backend='ddgs' 或使用模型自身知识",
                }, ensure_ascii=False)

            items = self._parse_baidu_results(resp.text)
            if not items:
                return "[百度搜索无结果，可能触发了反爬验证]"

            return json.dumps(items, ensure_ascii=False)

        except requests.Timeout:
            return json.dumps({
                "error": "百度搜索超时",
                "hint": "请稍后重试或使用模型自身知识",
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({
                "error": f"百度搜索失败: {e}",
            }, ensure_ascii=False)

    def _parse_baidu_results(self, html: str) -> list[dict]:
        """从百度搜索结果 HTML 中提取标题、摘要和 URL"""
        items = []
        # 匹配结果容器: <div class="result c-container" ...> ... </div>
        # 百度每条结果通常包含: <h3><a>标题</a></h3> + <span class="content-right_...">摘要</span>
        blocks = re.findall(
            r'<div[^>]*class="[^"]*result[^"]*c-container[^"]*"[^>]*>.*?</div>\s*</div>\s*</div>',
            html, re.DOTALL
        )
        if not blocks:
            # 降级: 匹配更宽泛的结果块
            blocks = re.findall(
                r'<div[^>]*class="[^"]*c-container[^"]*".*?</div>\s*</div>',
                html, re.DOTALL
            )

        for block in blocks[:5]:
            # 提取标题和链接
            title_match = re.search(r'<a[^>]*>(.*?)</a>', block, re.DOTALL)
            url_match = re.search(r'href\s*=\s*"((?:https?:)?//(?:www\.)?baidu\.com/link\?[^"]+)"', block)
            title = _extract_text(title_match.group(1)) if title_match else ""

            # 提取摘要 (去除 HTML 标签)
            snippet = _extract_text(block)
            # 摘要中去掉标题部分
            if title and snippet.startswith(title):
                snippet = snippet[len(title):].strip()
            snippet = snippet[:300]

            item = {"title": title, "snippet": snippet}
            if url_match:
                item["url"] = url_match.group(1)
            if title:
                items.append(item)

        return items

    async def _arun(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError
