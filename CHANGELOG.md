# 更新日志

## V2.1.0 (2026-08-03)

### 版本标记
- 累计修订 V2.0.0 → V2.0.1 → V2.0.2 → V2.0.3 → **V2.1.0**
- 此版本整合了 V2.0.x 全部修复，作为首个生产可用稳定版标记

### 当前能力摘要
- **纯 HTTP 抓取**: Scrapling FetcherSession（无浏览器）
- **WAF 检测**: HTTP 403/429/511 统一阻断 + 正文验证码特征检测
- **分页**: 有界并发 + 按页码顺序提交，`--pages N` 严格限制，`--all-pages` 最多 20 页
- **Session 复用**: `threading.local()` + `__enter__()` 真正复用底层 HTTP 客户端
- **并发**: 类目并发 + 页面并发 + 图片并发，全局 Semaphore 统一控制
- **输出**: CSV (UTF-8 BOM) + XLSX (格式化) + JSON (标准数组) + errors.csv + run_summary.json
- **图片**: 主图下载、魔数检测 (JPEG/PNG/WebP/AVIF)、同 URL 多商品回填、错误追踪
- **配置**: `categories.txt` 每行一个 URL，`urllib.parse` 校验
- **退出码**: 0=成功, 1=参数错误, 2=网络错误, 3=WAF阻断, 130=中断

### 依赖
- `scrapling[fetchers]==0.4.12` (传递安装 playwright Python 包, 不启动浏览器)
- `beautifulsoup4`, `lxml`, `openpyxl`, `httpx`, `pytest`
- `patchright` 安装后自动卸载

## V2.0.3 (2026-08-03)
- 按页码顺序缓冲提交（并发乱序数据不丢失）
- `--all-pages` 固定 20 页
- 真正 Session 复用（`__enter__()` 一次，后续复用）
- 图片错误写入 `errors.csv`
- 动态端口测试服务器

## V2.0.2 (2026-08-03)
- 依赖冲突修复 (lxml>=6.1.1)
- 403/429/511 无条件 WAF 阻断
- 有界并发分页
- 跨类目去重隔离
- `threading.local()` Session 管理

## V2.0.1 (2026-08-03)
- 纯 HTTP 模式 (删除 StealthyFetcher/Playwright/Chromium)
- `categories.txt` 替代 `categories.json`
- `products.json` 替代 `products.jsonl`
- WAF 检测和退出码规范

## V2.0.0 MVP (2026-08-03)
- 初始版本，基于 Scrapling 0.4.12
