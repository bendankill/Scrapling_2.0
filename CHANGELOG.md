# 更新日志

## V2.0.0 MVP (2026-08-03)

### 新增功能
- eMAG 商品列表页爬虫，基于 Scrapling 0.4.12
- 使用 Scrapling StealthyFetcher 绕过 AWS WAF CAPTCHA 保护
- 支持多类目并发抓取
- 从商品卡片 HTML + 内嵌 JSON 提取完整商品信息
- 罗马尼亚价格格式解析（千位分隔符 + 逗号小数点）
- 自动翻页，支持页数上限和全部页面模式
- 商品主图下载，支持并发控制和缓存检查
- 结果导出为 CSV (UTF-8 BOM)、XLSX 和 JSONL
- 独立运行输出目录，防止覆盖
- BAT 启动脚本，适合编程小白使用

### 技术栈
- Scrapling 0.4.12 (StealthyFetcher + FetcherSession)
- BeautifulSoup4 / lxml (HTML 解析)
- openpyxl (XLSX 导出)
- httpx (图片下载)

### 限制
- 由于 eMAG 使用 AWS WAF CAPTCHA，必须使用 StealthyFetcher（基于 Playwright/Chromium）
- 每次抓取页面耗时约 3-10 秒（包含 WAF 验证和页面渲染）
- 只抓取列表页，不访问商品详情页
