#!/usr/bin/env python3
"""
微信公众号关键词文章爬虫
  • 读取 keywords.json 配置文件，获取关键词列表及抓取数量
  • 通过搜狗微信搜索（weixin.sogou.com，type=2 文章搜索）抓取每个关键词的最新文章
  • 去重：同一篇文章（URL）不会在多个关键词下重复出现
  • 每天生成一篇汇总 Jekyll Markdown 文章，按关键词分节展示
"""

import aiohttp
import asyncio
import json
import logging
import random
import re
import sys
from bs4 import BeautifulSoup
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set
from urllib.parse import quote, urljoin
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

SOGOU_SEARCH_URL = (
    "https://weixin.sogou.com/weixin"
    "?type=2&query={query}&ie=utf8&s_from=input&_sug_=n&_sug_type_="
)

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Referer": "https://weixin.sogou.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
}

MAX_CONCURRENT = 2       # 搜狗反爬较严，并发不宜超过 2
MAX_RETRIES = 3
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
CONFIG_FILE = PROJECT_DIR / "keywords.json"
POSTS_DIR = PROJECT_DIR / "_posts"


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass
class Article:
    title: str
    url: str
    account: str = ""   # 来源公众号名（span.all-time-y2）
    digest: str = ""    # 文章摘要（p.txt-info）
    pub_time: str = ""  # 发布时间（span.s2，搜狗转换后为相对时间）


@dataclass
class KeywordResult:
    keyword: str
    articles: List[Article] = field(default_factory=list)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------
def load_config() -> dict:
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"配置文件不存在: {CONFIG_FILE}")
    with open(CONFIG_FILE, encoding="utf-8") as f:
        config = json.load(f)
    keywords = config.get("keywords", [])
    logger.info("已加载配置: %d 个关键词，每词抓取 %d 篇",
                len(keywords), config.get("articles_per_keyword", 5))
    return config


# ---------------------------------------------------------------------------
# 解析搜狗文章搜索结果页
# 经过实测验证的选择器：
#   文章列表：ul.news-list > li
#   标题链接：h3 a
#   公众号名：span.all-time-y2
#   摘要：    p.txt-info
#   时间：    span.s2（内含 <script> 动态渲染，取纯文本会为空，见下方处理）
# ---------------------------------------------------------------------------
def parse_search_result(html: str, keyword: str, n: int) -> List[Article]:
    soup = BeautifulSoup(html, "lxml")

    # 检测是否触发反爬验证码
    if soup.find("form", attrs={"id": "verify_form"}):
        raise RuntimeError("触发搜狗验证码，请稍后重试")

    items = soup.select("ul.news-list li")
    articles: List[Article] = []

    for item in items:
        if len(articles) >= n:
            break

        title_tag = item.select_one("h3 a")
        if not title_tag:
            continue

        title = title_tag.get_text(strip=True)
        href = title_tag.get("href", "")
        if not href or not title:
            continue

        # 搜狗文章链接是跳转链接，直接使用
        url = urljoin("https://weixin.sogou.com", href) if href.startswith("/") else href

        account_tag = item.select_one("span.all-time-y2")
        account = account_tag.get_text(strip=True) if account_tag else ""

        digest_tag = item.select_one("p.txt-info")
        # txt-info 里有 <em> 高亮标签，get_text 会自动合并
        digest = digest_tag.get_text(strip=True) if digest_tag else ""

        # span.s2 内是 <script>document.write(timeConvert('...'))</script>
        # 直接从 script 内容里提取时间戳
        time_tag = item.select_one("span.s2")
        pub_time = ""
        if time_tag:
            script = time_tag.find("script")
            if script:
                m = re.search(r"timeConvert\('(\d+)'\)", script.string or "")
                if m:
                    ts = int(m.group(1))
                    pub_time = datetime.fromtimestamp(ts, tz=TZ_SHANGHAI).strftime(
                        "%Y-%m-%d %H:%M"
                    )
            # 没有 script 时直接取文本（某些结果是静态时间）
            if not pub_time:
                pub_time = time_tag.get_text(strip=True)

        articles.append(Article(
            title=title,
            url=url,
            account=account,
            digest=digest,
            pub_time=pub_time,
        ))

    return articles


# ---------------------------------------------------------------------------
# Markdown 生成
# ---------------------------------------------------------------------------
def today_str() -> str:
    return datetime.now(TZ_SHANGHAI).strftime("%Y-%m-%d")


def generate_front_matter(date_str: str, keywords: List[str]) -> str:
    tags_str = ", ".join(f'"{kw}"' for kw in keywords)
    return f"""---
layout: single-with-ga
classes: wide
title: "{date_str} 微信热文精选"
date: {date_str} 08:00:00 +0800
categories: wechat-digest
tags: [{tags_str}]
---

"""


def generate_markdown_body(results: List[KeywordResult]) -> str:
    parts: List[str] = []

    # 概览表格
    parts.append("## 今日关键词概览")
    parts.append("")
    parts.append("| 关键词 | 文章数 | 状态 |")
    parts.append("|--------|--------|------|")
    for r in results:
        if r.error:
            status = f"❌ {r.error}"
        else:
            status = f"✅ {len(r.articles)} 篇"
        parts.append(f"| **{r.keyword}** | {len(r.articles)} | {status} |")
    parts.append("")
    parts.append("---")
    parts.append("")

    # 各关键词详细文章列表
    for r in results:
        parts.append(f"## 🔍 {r.keyword}")
        parts.append("")

        if r.error:
            parts.append(f"> ⚠️ 抓取失败：{r.error}")
            parts.append("")
            continue

        if not r.articles:
            parts.append("> 暂无相关文章")
            parts.append("")
            continue

        for i, art in enumerate(r.articles, 1):
            # 标题行：序号 + 可点击链接
            parts.append(f"### {i}. [{art.title}]({art.url})")
            parts.append("")

            # 元信息行
            meta = []
            if art.account:
                meta.append(f"📢 **{art.account}**")
            if art.pub_time:
                meta.append(f"🕐 {art.pub_time}")
            if meta:
                parts.append("  ".join(meta))
                parts.append("")

            # 摘要
            if art.digest:
                parts.append(f"> {art.digest}")
                parts.append("")

        parts.append("---")
        parts.append("")

    return "\n".join(parts)


def write_post(date_str: str, results: List[KeywordResult]) -> Path:
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = POSTS_DIR / f"{date_str}-wechat.md"
    keywords = [r.keyword for r in results]
    content = generate_front_matter(date_str, keywords) + generate_markdown_body(results)
    filename.write_text(content, encoding="utf-8")
    logger.info("已生成文章: %s", filename)
    return filename


# ---------------------------------------------------------------------------
# 异步爬虫
# ---------------------------------------------------------------------------
class KeywordCrawler:
    def __init__(self, config: dict):
        self.keywords: List[str] = config.get("keywords", [])
        self.n: int = config.get("articles_per_keyword", 5)
        delay = config.get("request_delay", [1.5, 3.5])
        self.delay_min: float = delay[0]
        self.delay_max: float = delay[1]
        self.deduplicate: bool = config.get("deduplicate", True)
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        self._seen_urls: Set[str] = set()  # 全局去重

    async def _fetch(self, session: aiohttp.ClientSession, url: str) -> str:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with self.semaphore:
                    await asyncio.sleep(random.uniform(self.delay_min, self.delay_max))
                    async with session.get(
                        url,
                        headers=HEADERS,
                        timeout=REQUEST_TIMEOUT,
                        allow_redirects=True,
                    ) as resp:
                        if resp.status == 429:
                            wait = 15 * attempt
                            logger.warning("触发限流（429），等待 %ds", wait)
                            await asyncio.sleep(wait)
                            continue
                        if resp.status != 200:
                            raise ValueError(f"HTTP {resp.status}")
                        return await resp.text()
            except Exception as exc:
                if attempt == MAX_RETRIES:
                    raise
                wait = 2 ** attempt + random.random()
                logger.warning("第 %d 次重试 %s（%s）", attempt, url[:60], exc)
                await asyncio.sleep(wait)
        raise RuntimeError(f"请求失败，已重试 {MAX_RETRIES} 次")

    async def _fetch_keyword(
        self,
        session: aiohttp.ClientSession,
        keyword: str,
    ) -> KeywordResult:
        result = KeywordResult(keyword=keyword)
        url = SOGOU_SEARCH_URL.format(query=quote(keyword))
        logger.info("抓取关键词: 「%s」-> %s", keyword, url)

        try:
            html = await self._fetch(session, url)
            articles = parse_search_result(html, keyword, self.n * 3)  # 多取一些，留给去重后筛选

            if self.deduplicate:
                deduped = []
                for art in articles:
                    if art.url not in self._seen_urls:
                        self._seen_urls.add(art.url)
                        deduped.append(art)
                        if len(deduped) >= self.n:
                            break
                articles = deduped
            else:
                articles = articles[:self.n]

            result.articles = articles
            logger.info("关键词 「%s」获取 %d 篇文章", keyword, len(articles))

        except RuntimeError as exc:
            result.error = str(exc)
            logger.error("关键词 「%s」失败: %s", keyword, exc)
        except Exception as exc:
            result.error = str(exc)
            logger.error("关键词 「%s」异常: %s", keyword, exc)

        return result

    async def run(self) -> List[KeywordResult]:
        """顺序抓取（避免对搜狗并发过高触发封锁）。"""
        connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT, force_close=False)
        async with aiohttp.ClientSession(connector=connector, trust_env=True) as session:
            results = []
            for kw in self.keywords:
                r = await self._fetch_keyword(session, kw)
                results.append(r)
            return results


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main() -> int:
    try:
        config = load_config()
    except FileNotFoundError as e:
        logger.error(str(e))
        return 1

    date_str = today_str()
    output_file = POSTS_DIR / f"{date_str}-wechat.md"

    if output_file.exists():
        logger.info("今日文章已存在，跳过: %s", output_file)
        return 0

    logger.info("开始抓取微信热文，日期: %s", date_str)
    crawler = KeywordCrawler(config)
    results = asyncio.run(crawler.run())

    total = sum(len(r.articles) for r in results)
    failed = sum(1 for r in results if r.error)
    logger.info("完成: %d 个关键词，共 %d 篇文章，%d 个失败", len(results), total, failed)

    write_post(date_str, results)
    return 0 if total > 0 else 1


if __name__ == "__main__":
    sys.exit(main())

