# 更新日志

## V2.1.4 (2026-08-07)

### 修复
- **HTTP 200页面分类**: 正确区分商品页、WAF、明确空类目、不可用类目和未知页面
- **Tricouri Sport解析**: 兼容 `.card-fashion.js-product-data` 商品卡，并从收藏按钮JSON回填商品ID和Offer ID
- **隐藏验证码误判**: 可见性检查覆盖元素自身及全部祖先节点的 `hidden`、`display:none`、`visibility:hidden`、`aria-hidden=true`
- **script验证码误判**: 可见正文排除 `script`、`style`、`noscript`、`template`、`head` 和隐藏内容
- **全部卡片解析失败**: 每张卡片的原始错误先写入 `errors.csv`，随后追加页面级 `ALL_PARSE_FAILED`

### 新增
- **未知HTTP 200诊断**: 异常时保存HTML和包含安全响应元数据、页面证据及解析计数的JSON
- **真实结构fixture**: 增加脱敏后的 `Tricouri Sport` 最小真实DOM测试样本

### 清理
- 删除废弃的 `_page_has_valid_product_soup()`
- 删除重复的 `crawler._check_body_waf()`，统一使用一套WAF与可见性规则

### 测试
- 新增19项专项测试，总测试数增至204项
- 完整测试连续两次均为204项通过、0失败
- 三次纯HTTP低频真实验证均成功解析60/60个 `Tricouri Sport` 商品

## V2.1.3 (2026-08-05)

### 新增
- **图片PNK命名**: 下载图片使用商品PNK码命名 (`{PNK}.{ext}`)
- **同URL多PNK**: 相同图片URL对应多个商品时，每个PNK生成独立文件(硬链接优先)
- **TXT运行配置**: `config/categories.txt` 顶部支持6项运行参数，CLI参数可覆盖
- **类目数量日志**: 任务开始和结束时打印实际抓取类目数量
- **真实图片验证**: 使用Pillow `Image.verify()` 严格验证JPEG/PNG/WebP/AVIF
- **有效小图片**: 移除固定1024字节限制，格式有效的图片均可保存
- **跨批次缓存**: 相同PNK+URL的图片跨批次复用，不重复生成文件
- **统一URL去重**: `_normalize_cat_url()` 识别同路径不同查询参数的重复URL

### 修复
- **并发默认值统一**: 新建 `config.py`，CLI默认值从统一配置读取
- **线程安全PNK命名**: `_reserved_names` + `_name_lock` 防止并发冲突
- **原子文件写入**: `mkstemp` + `os.replace` 防止半成品文件
- **Pillow严格验证**: 删除 `except Exception: return True`，WebP/AVIF全体严格验证
- **缓存扩展名修复**: 缓存存储 `(path, ext)` 元组，修复 `_tmp` 文件名问题
- **空断言修复**: 超时测试改为真实断言 `failed==1, error_type==TIMEOUT`

### 测试
- 157项测试 (从152项增长)
- 跨批次缓存、严格验证、TXT配置、PNK文件、类目数量专项测试

## V2.1.2 (2026-08-04)

### 新增
- **任务总耗时**: 所有退出场景打印 `HH:MM:SS.mmm` 格式耗时
- **有界图片Future**: `wait(FIRST_COMPLETED)` 替代50ms轮询
- **60秒进度日志**: 低频图片下载进度，不逐张打印

### 删除
- **断点续抓**: 删除 `checkpoint.py`、`--resume`、`resume.bat`、`CheckpointManager`

## V2.1.1 (2026-08-03)

### 新增
- **断点续抓**: CheckpointManager、页面快照、`--resume` 恢复
- **PageResult统计**: cards_found/products_parsed/parse_failed/duplicates/new_unique
- **Ctrl+C安全中断**: signal handler，退出码130，checkpoint保存

## V2.1.0 (2026-08-03)

### 版本标记
- 累计修订 V2.0.0 → V2.0.1 → V2.0.2 → V2.0.3 → **V2.1.0**
- 整合 V2.0.x 全部修复，首个生产可用稳定版标记

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
