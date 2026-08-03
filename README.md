# eMAG 商品列表爬虫 V2.0.1

基于 [Scrapling](https://github.com/D4Vinci/Scrapling) 纯 HTTP Fetcher 的 eMAG 商品列表页爬虫。

## 重要：纯 HTTP 模式

当前版本已改为**纯 HTTP 抓取**：
- 不再使用浏览器（Playwright/Chromium）
- 不再自动处理验证码
- 遇到验证码或 AWS WAF 时，程序会**立即终止**并打印明确提示
- 用户需要在普通浏览器中手动完成验证后重新运行

## 功能范围

### 本次包含
- 使用 Scrapling 纯 HTTP FetcherSession 获取 eMAG 列表页
- 支持多类目并发抓取
- 自动翻页，精确控制页数上限
- 罗马尼亚价格格式正确解析
- 商品主图下载（每商品最多1张）
- 结果导出为 CSV / XLSX / 标准 JSON（数组格式）
- 独立运行输出目录，不会覆盖之前的结果
- 验证码/WAF 自动检测和诊断信息保存

### 本次不包含
- 商品详情页抓取
- Web 后台或管理系统
- 数据库存储
- 代理池管理
- 浏览器自动化
- 自动绕过验证码

## 首次安装

### 前提条件
- Windows 10/11
- Python 3.11 或 3.12

### 安装步骤

双击运行 `01_install.bat`，脚本会自动：
1. 检查 Python 版本
2. 创建虚拟环境 `.venv`
3. 安装所有依赖（纯 HTTP，不含浏览器）
4. 验证安装

或者手动执行：
```batch
cd /d D:\test\20260730_PaChong\V2.0
01_install.bat
```

## 如何使用

### 方式一：双击 BAT 菜单（推荐）

双击 `02_run.bat`，选择对应选项：
1. 测试1页，不下载图片
2. 测试2页，不下载图片
3. 指定页数，下载主图
4. 指定页数，不下载图片
5. 全部页面（需二次确认）
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
# 鼠标
https://www.emag.ro/mouse/c
# 键盘
https://www.emag.ro/tastaturi/c
# 耳机（已注释掉，不抓取）
# https://www.emag.ro/casti/c
```

规则：
- 每行一个 URL
- `#` 开头的行为注释，会被忽略
- 空行会被忽略
- URL 必须以 `https://www.emag.ro/` 或 `https://emag.ro/` 开头
- 必须是 `/c` 类目路径，不能是 `/pd/` 商品详情页
- 类目名称会自动从 URL 中提取

## 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--pages N` | 每个类目最大抓取 N 页（必须 > 0） | 1 |
| `--all-pages` | 抓取全部页面（与 --pages 互斥） | - |
| `--no-images` | 不下载商品主图 | 下载 |
| `--category-workers N` | 类目并发数 | 2 |
| `--page-workers N` | 页面并发数 | 4 |
| `--image-workers N` | 图片下载并发数 | 8 |
| `--max-in-flight N` | 全局最大并发请求 | 16 |
| `--config FILE` | 类目配置文件 | config/categories.txt |
| `--output DIR` | 输出目录 | output/时间戳/ |
| `--log-level` | 日志级别 | INFO |
| `--version` | 显示版本 | - |

## 退出码说明

| 退出码 | 含义 |
|--------|------|
| 0 | 成功且抓到商品 |
| 1 | 配置或参数错误 |
| 2 | 网络、解析或全部页面失败 |
| 3 | 检测到验证码/WAF，需要人工处理 |
| 130 | 用户中断 (Ctrl+C) |

## 输出文件

每次运行创建一个独立目录，例如 `output\20260803_180000\`：

| 文件 | 说明 |
|------|------|
| `products.xlsx` | Excel 商品数据（格式化） |
| `products.csv` | CSV 商品数据（UTF-8 BOM） |
| `products.json` | 标准 JSON 数组（可用 json.load 打开） |
| `run_summary.json` | 运行统计汇总 |
| `errors.csv` | 错误记录 |
| `logs/run.log` | 详细日志 |
| `images/` | 下载的商品主图 |
| `diagnostics/` | 验证码诊断文件（仅遇到验证码时） |

## 如何判断执行成功

1. 控制台末尾显示退出码 0
2. 成功页数 > 0
3. 抓到商品数量 > 0

## 遇到验证码怎么办

程序检测到验证码时会：
1. 立即停止所有抓取
2. 打印醒目的中文提示
3. 保存 `diagnostics/captcha_diagnostic.json` 诊断文件
4. 返回退出码 3
5. 已抓取的数据仍会正常保存

请：
1. 用普通浏览器打开提示中的 URL
2. 完成人工验证
3. 重新运行爬虫脚本

**如果反复出现验证码，说明当前网络环境下纯 HTTP 方式暂时无法继续，请不要反复高频重试。**

## 常见问题

### Python 未安装
下载 [Python 3.12](https://www.python.org/downloads/)，安装时勾选 "Add Python to PATH"

### BAT 乱码
由于 Windows CMD 编码问题，部分中文可能显示异常。数据文件（CSV/XLSX/JSON）使用 UTF-8，不受影响。

### Excel 文件被占用
关闭 Excel 中已打开的输出文件后重新运行。

### 验证码反复出现
当前纯 HTTP 方式无法绕过，请降低并发参数或更换网络环境后重试。

## 项目结构

```
├── main.py              # 程序入口
├── crawler.py           # 爬虫核心（纯 HTTP）
├── parser.py            # 页面解析器
├── models.py            # 数据模型
├── exporters.py         # 导出模块
├── image_downloader.py  # 图片下载
├── utils.py             # 工具函数
├── requirements.txt     # 依赖清单
├── VERSION              # 版本号
├── README.md            # 本文件
├── CHANGELOG.md         # 更新日志
├── .gitignore
├── 01_install.bat       # 安装脚本
├── 02_run.bat           # 运行菜单
├── config/
│  └── categories.txt    # 类目配置
├── tests/
│  ├── test_parser.py
│  ├── test_prices.py
│  ├── test_page_limits.py
│  └── test_config_captcha.py
└── output/              # 输出目录
```
