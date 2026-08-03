# 更新日志

## V2.0.2 (2026-08-03)

### 重大修复
- **依赖冲突修复**: 移除 `lxml==5.3.0` 固定版本，由 Scrapling 0.4.12 自动管理 (需要 lxml>=6.1.1)
- **WAF 检测增强**: HTTP 403/429/511 无条件视为 WAF 阻断，不再依赖正文关键词
- **分页重构**: 有界并发调度，不再一次性提交全部页面任务；每完成一个补充一个
- **跨类目去重隔离**: 每个类目独立的页面哈希集合，不同类目不会互相误判重复
- **每线程 Session 复用**: 使用 `threading.local()` 实现线程本地 FetcherSession，同一线程多页面复用
- **图片下载修复**: 同 URL 对应多个商品时全部回填本地路径；HTTP 重定向跟随
- **TXT URL 校验增强**: 使用 `urllib.parse` 解析 URL，只接受 `http/https`，域名为 emag.ro
- **BAT 脚本修复**: 使用 `!ERRORLEVEL!` 延迟展开避免括号块问题；添加 patchright 卸载步骤
- **errors.csv 确保存在**: 无错误时也创建含表头的 errors.csv
- **图片统计修复**: 每类目独立统计 image_success/image_failed

### 依赖说明
- `scrapling[fetchers]==0.4.12` 传递安装 `playwright`（Python 包，仅用于错误类型定义）
- `playwright` Python 包已安装但**不下载不启动任何浏览器**
- `patchright` 在安装后被自动移除
- `browserforge` 用于生成浏览器兼容的请求头（纯数据库，不含浏览器代码）

## V2.0.1 (2026-08-03)
- 纯 HTTP 模式，删除 StealthyFetcher
- categories.txt 替代 categories.json
- products.json 替代 products.jsonl
- Captcha 检测和退出码规范

## V2.0.0 MVP (2026-08-03)
- 初始版本，基于 Scrapling 0.4.12
