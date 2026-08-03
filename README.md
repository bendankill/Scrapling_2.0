# eMAG 商品列表爬虫 V2.0 MVP

基于 [Scrapling](https://github.com/D4Vinci/Scrapling) 的 eMAG（罗马尼亚电商）商品列表页爬虫。

## 功能范围

### 本次包含
- 抓取 eMAG 商品列表页的完整商品信息
- 支持同时配置多个类目，可独立开关
- 自动翻页，精确控制页数上限
- 罗马尼亚价格格式正确解析
- 商品主图下载（每商品最多1张）
- 结果导出为 CSV / XLSX / JSONL
- 独立运行输出目录，不会覆盖之前的结果

### 本次不包含
- 商品详情页抓取
- Web 后台或管理系统
- 数据库存储
- 代理池管理
- 分布式爬虫
- 定时任务

## 首次安装

### 前提条件
- Windows 10/11
- Python 3.11 或 3.12
- Git（用于克隆项目，已有项目目录可跳过）

### 安装步骤

双击运行 `01_install.bat`，脚本会自动：
1. 检查 Python 是否安装
2. 创建虚拟环境 `.venv`
3. 安装所有依赖
4. 显示安装结果

或者手动执行：
```batch
cd /d D:\test\20260730_PaChong\V2.0
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

## 如何使用

### 方式一：双击 BAT 菜单（推荐）

双击 `02_run.bat`，选择对应选项：
1. 测试1页（不下载图片）
2. 测试2页（不下载图片）
3. 指定页数（下载图片）
4. 指定页数（不下载图片）
5. 全部页面（需二次确认）
6. 编辑类目配置

### 方式二：命令行

```batch
# 进入项目目录
cd /d D:\test\20260730_PaChong\V2.0

# 测试1页，不下载图片
.venv\Scripts\python.exe main.py --pages 1 --no-images

# 测试2页，不下载图片
.venv\Scripts\python.exe main.py --pages 2 --no-images

# 抓取3页，下载主图
.venv\Scripts\python.exe main.py --pages 3

# 抓取全部页面
.venv\Scripts\python.exe main.py --all-pages

# 速度优先配置（10页，4类目并发）
.venv\Scripts\python.exe main.py --pages 10 --category-workers 4 --page-workers 4 --image-workers 8 --max-in-flight 16
```

## 添加多个类目

编辑 `config\categories.json`：

```json
{
  "categories": [
    {
      "name": "Mouse",
      "url": "https://www.emag.ro/mouse/c",
      "enabled": true
    },
    {
      "name": "Tastaturi",
      "url": "https://www.emag.ro/tastaturi/c",
      "enabled": true
    },
    {
      "name": "Casti",
      "url": "https://www.emag.ro/casti/c",
      "enabled": false
    }
  ]
}
```

- `name`: 类目名称（用于显示和统计）
- `url`: 类目列表页 URL（必须 https:// 开头）
- `enabled`: true=启用，false=临时关闭

## 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--pages N` | 每个类目最大抓取 N 页 | 1 |
| `--all-pages` | 抓取全部页面（与 --pages 互斥） | - |
| `--no-images` | 不下载商品主图 | 下载 |
| `--category-workers N` | 类目并发数 | 2 |
| `--page-workers N` | 页面并发数 | 4 |
| `--image-workers N` | 图片下载并发数 | 8 |
| `--max-in-flight N` | 全局最大并发请求 | 16 |
| `--config FILE` | 类目配置文件 | config/categories.json |
| `--output DIR` | 输出目录 | output/时间戳/ |
| `--log-level` | 日志级别 | INFO |
| `--version` | 显示版本 | - |
| `--help` | 显示帮助 | - |

## 输出文件

每次运行创建一个独立目录，例如 `output\20260803_153000\`：

| 文件 | 说明 |
|------|------|
| `products.xlsx` | Excel 商品数据（带格式化） |
| `products.csv` | CSV 商品数据（UTF-8 BOM，Excel 友好） |
| `products.jsonl` | 完整原始商品数据 |
| `run_summary.json` | 运行统计汇总 |
| `errors.csv` | 错误记录 |
| `logs/run.log` | 详细日志 |
| `images/` | 下载的商品主图 |

## 如何判断执行成功

1. 控制台末尾显示 "抓取完成!" 
2. 退出码为 0
3. 成功页数 > 0
4. 抓到商品数量 > 0
5. `run_summary.json` 中的 `totals.success_pages` > 0

## 常见错误处理

### Python 未安装
下载安装 [Python 3.12](https://www.python.org/downloads/)，安装时勾选 "Add Python to PATH"

### Scrapling 安装失败
确保已安装 Visual C++ Redistributable，然后重试 `01_install.bat`

### 403 / 429 错误
eMAG 限制了请求频率，程序已内置重试和延迟。如果持续出现，请：
- 降低并发参数
- 等待几分钟后重试
- 检查网络连接

### 网络超时
检查网络连接是否正常，eMAG.ro 是否可访问

### Excel 文件被占用
关闭 Excel 中打开的旧输出文件，然后重新运行

### 类目 URL 无效
检查 `config/categories.json` 中的 URL 是否以 `https://www.emag.ro/` 开头

### 页面结构变化
如果 eMAG 改版导致抓不到商品，请检查程序日志并更新 `parser.py`

### 没有抓到商品
- 检查类目 URL 是否有效
- 检查日志中的错误信息
- 尝试在浏览器中打开目标 URL 确认页面正常

## 项目结构

```
├─ main.py              # 程序入口
├─ crawler.py           # 爬虫核心逻辑
├─ parser.py            # 页面解析器
├─ models.py            # 数据模型
├─ exporters.py         # 导出模块
├─ image_downloader.py  # 图片下载
├─ utils.py             # 工具函数
├─ requirements.txt     # 依赖清单
├─ VERSION              # 版本号
├─ README.md            # 本文件
├─ CHANGELOG.md         # 更新日志
├─ .gitignore
├─ 01_install.bat       # 安装脚本
├─ 02_run.bat           # 运行菜单
├─ config/
│  └─ categories.json   # 类目配置
├─ tests/
│  ├─ test_parser.py
│  ├─ test_prices.py
│  └─ test_page_limits.py
└─ output/              # 输出目录
```
