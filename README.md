# eMAG 商品列表爬虫 V2.1.4

基于 [Scrapling](https://github.com/D4Vinci/Scrapling) 纯 HTTP Fetcher 的 eMAG（罗马尼亚电商）商品列表页爬虫。

## 版本记录

| 版本 | 日期 | 说明 |
|------|------|------|
| **V2.1.4** | 2026-08-07 | HTTP 200页面准确分类、隐藏/script验证码防误判、card-fashion商品解析、逐卡错误与异常诊断 ([分支](https://github.com/bendankill/Scrapling_2.0/tree/V2.1.4)) |
| V2.1.3 | 2026-08-05 | PNK图片命名、TXT运行配置、真实图片验证(Pillow)、跨批次缓存、类目数量日志 ([分支](https://github.com/bendankill/Scrapling_2.0/tree/V2.1.3)) |
| V2.1.2 | 2026-08-04 | 删除断点续抓、任务总耗时打印、有界图片Future+wait(FIRST_COMPLETED)调度+60s进度日志 |
| V2.1.1 | 2026-08-03 | 断点续抓(checkpoint)、准确统计(PageResult)、Ctrl+C安全中断、统一RunStatus、唯一键持久化 |
| V2.1.0 | 2026-08-03 | 首个生产可用稳定版，整合 V2.0.x 全部修复 |
| V2.0.3 | 2026-08-03 | 按页码顺序提交、真正 Session 复用、--all-pages=20、图片错误追踪 |
| V2.0.2 | 2026-08-03 | 依赖修复、403/429/511 统一 WAF 阻断、有界并发分页、跨类目去重隔离 |
| V2.0.1 | 2026-08-03 | 纯 HTTP 改造（删除浏览器）、TXT 类目配置、JSON 标准输出、WAF 退出码 |
| V2.0.0 | 2026-08-03 | 初始 MVP 版本 |

> 最新开发版本为 **V2.1.4**（`V2.1.4` 分支）。`main` 分支当前为 V2.1.0 稳定版。运行 `python main.py --version` 可确认本地版本，完整变更记录见 [CHANGELOG.md](CHANGELOG.md)。

## 重要：纯 HTTP 模式

当前版本使用**纯 HTTP 抓取**：
- 不启动浏览器（Playwright/Chromium）
- 不自动处理验证码
- 遇到验证码或 AWS WAF（HTTP 403/429/511）时，程序会**立即终止**并打印明确提示
- 用户需要在普通浏览器中手动完成验证后重新运行

## 功能范围

### 包含
- 使用 Scrapling 纯 HTTP FetcherSession 获取 eMAG 列表页
- 每线程真正 Session 复用（Cookie/TCP/TLS）
- 多类目并发抓取
- 有界并发 + 按页码顺序提交分页
- `--pages N` 严格最多 N 页，`--all-pages` 最多 20 页
- **Ctrl+C 安全中断**: 退出码 130
- **准确统计**: 每页 cards_found/products_parsed/parse_failed/duplicates/new_unique
- **图片下载进度**: 每60秒低频进度日志，有界Future调度，wait(FIRST_COMPLETED)事件驱动
- 罗马尼亚价格格式正确解析
- 商品主图下载（每商品最多 1 张），支持 JPEG/PNG/WebP/AVIF
- CSV (UTF-8 BOM) / XLSX (格式化) / JSON (标准数组) 导出
- 独立运行输出目录，不覆盖之前结果
- WAF 自动检测（403/429/511 + 正文特征）和诊断信息保存
- 图片错误写入 errors.csv

### 不包含
- 商品详情页抓取
- Web 后台或管理系统
- 数据库存储 / 代理池管理
- 浏览器自动化 / 自动绕过验证码

## 首次安装

### 前提条件
- Windows 10/11
- Python 3.11 或 3.12

### 安装步骤

双击 `01_install.bat`，脚本自动完成：
1. 检测 Python 版本
2. 创建虚拟环境 `.venv`
3. 安装依赖（纯 HTTP，不下载浏览器）
4. 移除不需要的 `patchright`
5. 执行 `pip check` 验证

或手动执行：
```batch
cd /d D:\test\20260730_PaChong\V2.0
01_install.bat
```

## 如何使用

### 方式一：BAT 菜单（推荐）

双击 `02_run.bat`：
1. 测试 1 页，不下载图片
2. 测试 2 页，不下载图片
3. 指定页数，下载主图
4. 指定页数，不下载图片
5. 最多 20 页（需二次确认）
6. 编辑 categories.txt

### 方式二：命令行

```batch
cd /d D:\test\20260730_PaChong\V2.0

.venv\Scripts\python.exe main.py --pages 1 --no-images
.venv\Scripts\python.exe main.py --pages 2 --no-images
.venv\Scripts\python.exe main.py --pages 3
.venv\Scripts\python.exe main.py --all-pages
.venv\Scripts\python.exe main.py --pages 10 --category-workers 2 --image-workers 8 --max-in-flight 16
```

## 添加多个类目

编辑 `config\categories.txt`，每行一个 URL：

```text
# 音箱
https://www.emag.ro/boxe/c?ref=bc
# 鼠标
https://www.emag.ro/mouse/c
# 键盘（已注释，不抓取）
# https://www.emag.ro/tastaturi/c
```

规则：
- 每行一个 URL
- `#` 开头的行为注释，忽略
- 空行忽略
- URL 必须以 `https://www.emag.ro/` 或 `https://emag.ro/` 开头
- 必须是 `/c` 类目路径，不能是 `/pd/` 商品详情页
- 类目名称自动从 URL 提取（如 `/boxe/c` → Boxe）

## 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--pages N` | 每个类目最大抓取 N 页（必须 > 0） | 1 |
| `--all-pages` | 每个类目最多抓取 20 页（与 --pages 互斥） | - |
| `--no-images` | 不下载商品主图 | 下载 |
| `--category-workers N` | 类目并发数 | 2 |
| `--page-workers N` | 页面并发数 | 3 |
| `--image-workers N` | 图片下载并发数 | 8 |
| `--max-in-flight N` | 全局最大并发请求 | 16 |
| `--config FILE` | 类目配置文件 | config/categories.txt |
| `--output DIR` | 输出目录 | output/时间戳/ |
| `--log-level` | 日志级别 (DEBUG/INFO/WARNING/ERROR) | INFO |
| `--version` | 显示版本号 | - |

## 退出码说明

| 退出码 | 含义 |
|--------|------|
| 0 | 成功且抓到商品 |
| 1 | 配置或参数错误 |
| 2 | 网络、解析或全部页面失败 |
| 3 | 检测到 WAF/验证码/403/429/511，需人工处理 |
| 130 | 用户中断 (Ctrl+C) |

## 输出文件

每次运行创建独立目录 `output\YYYYMMDD_HHMMSS\`：

| 文件 | 说明 |
|------|------|
| `products.xlsx` | Excel 商品数据（格式化、冻结首行、自动筛选） |
| `products.csv` | CSV 商品数据（UTF-8 BOM，Excel 友好） |
| `products.json` | 标准 JSON 数组（`json.load()` 可直接打开） |
| `run_summary.json` | 运行统计汇总 |
| `errors.csv` | 错误记录（页面错误 + 图片错误，无错误时仅有表头） |
| `logs/run.log` | 详细日志 |
| `images/` | 下载的商品主图 |
| `diagnostics/` | WAF 诊断文件（仅遇到阻断时） |

## 如何判断执行成功

1. 控制台末尾显示「退出码: 0」
2. 成功页数 > 0
3. 抓到商品数量 > 0

## 遇到验证码怎么办

程序检测到 WAF/验证码时：
1. 立即停止所有抓取
2. 打印中文提示（包含 HTTP 状态码、类目、页码、URL、阻断类型）
3. 保存 `diagnostics/captcha_diagnostic.json` 和脱敏响应 HTML
4. 返回退出码 3
5. 已抓取数据仍正常保存

处理步骤：
1. 用普通浏览器打开提示中的 URL
2. 完成人工验证
3. 重新运行爬虫

**如果反复出现验证码，说明当前网络环境下纯 HTTP 方式暂时无法继续，请不要反复高频重试。**

## 常见问题

### Python 未安装
下载 [Python 3.12](https://www.python.org/downloads/)，安装时勾选「Add Python to PATH」

### BAT 乱码
Windows CMD 编码问题可能导致部分中文显示异常。数据文件（CSV/XLSX/JSON）使用 UTF-8，不受影响。

### Excel 文件被占用
关闭已打开的输出文件后重新运行。

### 验证码反复出现
降低并发参数或更换网络环境后重试。纯 HTTP 方式无法自动绕过网站防护。

### 依赖安装失败
检查网络连接，确保可以访问 PyPI。`scrapling[fetchers]` 需要从 PyPI 下载。

## 项目结构

```
├── main.py              # 程序入口，CLI 参数解析
├── crawler.py           # 爬虫核心（纯 HTTP + Session 复用 + 有界并发）
├── parser.py            # 页面解析器（eMAG 商品卡片）
├── models.py            # 数据模型（ProductItem）
├── exporters.py         # 导出模块（CSV/XLSX/JSON）
├── image_downloader.py  # 图片下载（魔数检测 + 错误追踪）
├── utils.py             # 工具函数（价格解析/WAF检测/TXT配置）
├── requirements.txt     # 依赖清单
├── VERSION              # 版本号
├── README.md            # 本文件
├── CHANGELOG.md         # 完整更新日志
├── .gitignore
├── 01_install.bat       # 安装脚本
├── 02_run.bat           # 运行菜单
├── config/
│   └── categories.txt   # 类目配置
├── tests/
│   ├── test_parser.py
│   ├── test_prices.py
│   ├── test_page_limits.py
│   ├── test_config_captcha.py
│   ├── test_image_perf.py
│   └── test_integration.py
└── output/              # 输出目录
```
