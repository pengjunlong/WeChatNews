# WeChatNews

自动抓取微信公众号热文，按关键词分类整理，每天生成 Jekyll 博客文章并部署到 GitHub Pages。

基于 [Minimal Mistakes](https://github.com/mmistakes/minimal-mistakes) 主题，纯 Markdown 渲染。

## 功能特性

- **关键词驱动**：在 `keywords.json` 中配置感兴趣的关键词，无需关心具体公众号
- **自动抓取**：GitHub Actions 定时任务，每天北京时间 8:00 自动运行
- **来源标注**：每篇文章注明来源公众号名称、发布时间及摘要
- **跨词去重**：同一篇文章不会在多个关键词下重复出现
- **增量更新**：当天文章已存在则跳过，避免重复抓取
- **数据来源**：搜狗微信搜索（`weixin.sogou.com`）

## 项目结构

```
WeChatNews/
├── _config.yml               # Jekyll 站点配置
├── keywords.json             # 关键词配置文件（主要维护对象）
├── _posts/                   # 每日自动生成的 Markdown 文章
├── _layouts/
│   └── single-with-ga.html   # 文章布局（含 Google Analytics）
├── _includes/
│   └── analytics.html        # GA 跟踪代码
├── _data/
│   └── subsites.yml          # 子站点元数据
├── scripts/
│   └── wechat_crawler.py     # 爬虫主脚本
├── .github/workflows/
│   └── deploy.yml            # CI/CD 定时任务
├── Gemfile
└── index.html                # 首页
```

## 配置关键词

编辑 `keywords.json`：

```json
{
  "keywords": ["人工智能", "大模型", "经济政策"],
  "articles_per_keyword": 5,
  "request_delay": [1.5, 3.5],
  "deduplicate": true
}
```

| 字段 | 说明 |
|------|------|
| `keywords` | 关键词列表，支持任意中文词 |
| `articles_per_keyword` | 每个关键词抓取的文章数 |
| `request_delay` | 请求间隔（秒），随机取区间内的值，避免触发限流 |
| `deduplicate` | 是否跨关键词去重（默认 `true`） |

## 本地开发

### 前置依赖

- Python >= 3.10
- Ruby >= 3.0 + Bundler（本地预览 Jekyll 时需要）

### 手动运行爬虫

```bash
pip install aiohttp beautifulsoup4 lxml
python scripts/wechat_crawler.py
```

生成的文章保存到 `_posts/YYYY-MM-DD-wechat.md`。

### 本地预览 Jekyll 站点

```bash
bundle install
bundle exec jekyll serve
```

访问 `http://localhost:4000/WeChatNews/` 查看效果。

## 许可

MIT
