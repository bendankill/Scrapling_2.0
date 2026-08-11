# 更新日志

## V2.2.1（开发中，2026-08-11）

### 新增
- **页面真实类目证据**: 从列表页可见面包屑、JSON-LD `BreadcrumbList` 和明确嵌入状态提取类目路径
- **旁路类目缓存**: 不修改 ProductItem 或 extra，按规范化 `category_url` 在线程安全注册表保存已验证层级
- **跨页复用与隔离**: 同一类目复用首次有效层级，不同类目使用独立缓存键和提取锁

### 数据规则
- 排除 `eMAG`、`Acasă`、`Home` 网站根节点，清理空段和重复层级
- 当前类目必须位于末级；可靠父级面包屑缺少当前类目时才允许补入
- 页面证据缺失或不一致时，回退原有 `favorite_data.category_trail` 和 `data-category-trail`
- 最多导出一级至五级，不改变其余34个商品字段或 extra 原值

### 性能与验证
- 每个 HTTP 200 页面仍只创建一次 Soup；60 张商品卡只提取一次页面类目层级
- 不增加商品详情页或其他 HTTP 请求
- 新增38项专项测试，完整测试连续两次均为274项通过、0失败
- 唯一一次真实 Boxe 请求返回 HTTP 511，按规则立即停止且未重试

## V2.2.0 (2026-08-10)

### 2026-08-11 数据完整性修复
- **链接打标**: 将 `campaign_name` 的输出字段名从“活动名称”修正为“链接打标”，三种产品格式共用同一映射
- **分层扩展映射**: `favorite_data` 内部与 `extra` 顶层使用各自的明确映射范围，顶层意外出现的 `category_trail` 保持原名
- **无损冲突保护**: 翻译目标键冲突或输入已有中文键时，冲突组整体保留原字段名，禁止覆盖、丢键或自创后缀
- **回归验证**: V2.2.0 专项测试增至32项，完整测试连续两次均为236项通过、0失败

### 新增
- **统一中文输出模式**: `products.json`、`products.csv`、`products.xlsx` 共用唯一的34项产品字段映射
- **扩展信息字段映射**: 只翻译16项含义明确的键，未知技术字段和值保持原样
- **真实类目层级**: 从商品已有 `favorite_data.category_trail` 或 `data-category-trail` 提取一级至五级
- **动态固定格式列**: CSV/XLSX按整批实际最高层级输出类目列，缺失层级单元格留空

### 保持兼容
- ProductItem、crawler、parser、图片下载、去重、统计和日志继续使用原英文内部字段
- 不翻译商品标题、类目值、库存、标签或其他罗马尼亚语内容
- 不访问商品详情页，不推断品牌，不新增评分、规格或描述字段

### 测试
- 新增26项中文字段、类目层级、JSON/CSV/XLSX及内部边界专项测试
- 验证JSON类型保持、UTF-8 BOM、XLSX数值类型、动态类目列和三格式数量一致

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
