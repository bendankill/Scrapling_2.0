# 更新日志

## V2.0.1 (2026-08-03)

### 重大变更
- **删除浏览器方案**: 彻底移除 StealthyFetcher、DynamicFetcher、Playwright、Chromium
- **切换纯 HTTP**: 使用 Scrapling Fetcher/FetcherSession 纯 HTTP API
- **类目配置改为 TXT**: 每行一个 URL, 支持注释, 自动生成类目名
- **输出改为标准 JSON**: products.json 替代 products.jsonl, 使用 json.load 打开
- **新增验证码检测**: 自动检测 AWS WAF/CAPTCHA 并终止任务, 保存诊断信息
- **新增退出码规范**: 0=成功, 1=配置错误, 2=网络错误, 3=验证码, 130=中断

### 修复
- 修复 01_install.bat 中文乱码和"不是内部或外部命令"问题
- 修复品牌从标题猜测的问题 (现在只在页面有明确品牌字段时才填写)
- 修复原价选择器 `.pricing` 过宽导致误取当前价
- 修复库存判断顺序 (先判断缺货, 再判断供应商, 最后有库存)
- 修复 requested_pages 保存真实请求数而非上限
- 修复 page-workers 参数实际无效的问题 (现已实现真正的页面并发)
- 修复图片回填路径不一致 (统一使用 get_product_key)
- 修复多类目线程安全问题

### 删除的依赖
- playwright
- patchright
- browserforge
- scrapling[all] → scrapling[fetchers]

## V2.0.0 MVP (2026-08-03)

### 初始版本
- eMAG 商品列表页爬虫, 基于 Scrapling 0.4.12
- 使用 Scrapling StealthyFetcher 绕过 AWS WAF
- 多类目并发抓取, 罗马尼亚价格解析
- CSV/XLSX/JSONL 导出
