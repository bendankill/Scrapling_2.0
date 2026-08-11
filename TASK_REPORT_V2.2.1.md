# Scrapling V2.2.0 归档与 V2.2.1 类目层级完善任务报告

## 1. 完成状态

**自动化开发、测试、实际写盘和推送已完成；真实 HTTP 验证因 WAF 阻断暂未完成。**

V2.2.0 已按验收 SHA 归档，GitHub 默认主页已用 main 独立纯文档提交更新。V2.2.1 已从最终 V2.2.0 创建，完成列表页真实类目证据提取、线程安全旁路缓存、跨页复用、三格式导出和版本更新。

## 2. V2.2.0 最终归档 SHA

`5c444806b382b56c13c7afe5eaab31d94288afdc`

任务开始、本任务功能提交后和最终核验时，`origin/V2.2.0` 均保持该 SHA。V2.2.0 没有遗漏的项目代码、测试或报告，因此没有制造空提交。

## 3. GitHub 默认主页 main 更新前后 SHA

- 更新前：`75500e45d244b61854de553eedbaad21e9d3005d`
- 更新后：`383c1e8fdf38f75c257558ac42786faf2f567604`
- 提交：`docs: archive V2.2.0 and announce V2.2.1 development`

main 只修改 `README.md` 和 `CHANGELOG.md`，没有合并或修改 V2.2.0 生产代码。README 明确 V2.2.0 是当前稳定版本，V2.2.1 是开发分支，并提供两个分支链接。

## 4. V2.2.1 创建基线 SHA

- V2.2.0 唯一基线：`5c444806b382b56c13c7afe5eaab31d94288afdc`
- V2.2.1 创建时本地 SHA：`5c444806b382b56c13c7afe5eaab31d94288afdc`
- V2.2.1 初始远程 SHA：`5c444806b382b56c13c7afe5eaab31d94288afdc`

V2.2.1 未从 main 或其他历史分支创建。

## 5. V2.2.1 最终功能完整 SHA

`237b5338f7955f4d0bc5c777ebb303f7f0127789`

提交：`feat: extract verified category hierarchy for V2.2.1`

本报告在功能 SHA 产生后单独提交，以便准确记录功能提交。最终包含报告的发布 HEAD 由任务结束时的本地与远程 SHA 核验结果确定。

## 6. 本地与远程是否一致

功能提交推送后，本地 V2.2.1 与 `origin/V2.2.1` 均为 `237b5338f7955f4d0bc5c777ebb303f7f0127789`。报告提交后再次核对最终发布 HEAD。

## 7. 是否已推送

是。

- V2.2.1 初始分支已通过普通推送创建并设置跟踪。
- 功能提交已普通快进推送。
- main 文档提交已普通快进推送。
- 没有强制推送、合并生产代码、创建 PR 或标签。

## 8. config/categories.txt 是否保持不变

是。Codex 没有修改、恢复、暂存或提交该文件。

任务开始、分支切换、全部测试、真实验证和功能提交后的哈希均为：

`7732ac1747d3f708630105b8eb1cdb7513c2e74d`

真实 HTTP 验证使用 `output/` 临时配置，结束后已删除。

## 9. 用户备份文件是否保持不变

任务开始时工作区没有检测到 `config/categories - 副本.txt1`。Codex 没有创建、删除、恢复或提交该文件，也没有处理任何其他用户配置备份。

## 10. 当前问题根因

V2.2.0 只在最终导出时读取商品卡已有：

1. `extra.favorite_data.category_trail`
2. `extra["data-category-trail"]`

商品卡路径本身可能省略中间类目，例如缺少 `Audio Hi-Fi`。crawler 已经为每个 HTTP 200 页面创建唯一 Soup，但旧调用链没有从该 Soup 读取页面级面包屑、JSON-LD 或嵌入状态，因此更完整的页面证据无法进入导出边界。

## 11. 最终类目层级数据来源

页面级来源：

1. 可见面包屑 DOM，包括 schema.org microdata。
2. JSON-LD `BreadcrumbList`。
3. 明确 JSON/初始化状态中的 `categoryPath`、`categoryTrail`、`breadcrumbs` 等类目路径。

页面来源不存在或无法通过当前类目校验时，保持原有回退：

4. `extra.favorite_data.category_trail`
5. `extra["data-category-trail"]`

唯一一次线上 eMAG 请求返回 HTTP 511，所以本次无法确认当前线上 Boxe HTML 实际采用上述哪一种页面来源。页面结构支持通过标准化 fixture 和动态端口本地 HTTP 生产调用链验证，未冒充为线上证据。

## 12. 数据源优先级和一致性规则

- 所有候选先去除空段、网站根节点 `eMAG`、`Acasă`、`Home` 和重复层级。
- 当前类目已经出现在路径中时，必须位于末级，否则拒绝。
- 面包屑只提供父链时，仅在当前类目可由类目 URL、`h1`、`aria-current=page` 或明确类目标识确认时补入末级。
- 多来源不拼接，选择通过校验的单条路径。
- 页面候选先比较完整层级数，再比较来源可靠性；同等完整度时可见面包屑/JSON-LD 高于嵌入状态。
- 页面旁路证据优先于卡片路径；无有效页面证据才回退卡片。
- 一致性标准化忽略大小写、标点、连字符、罗马尼亚语重音和多余空格。
- 所有来源均不一致时不输出猜测层级。

## 13. Boxe 示例完整层级实际输出

生产 `Exporters.finalize()` 的动态本地 HTTP 与独立实际写盘均得到：

- 一级类：`TV, Audio-Video & Foto`
- 二级类：`Audio HI-FI & Profesionale`
- 三级类：`Audio Hi-Fi`
- 四级类：`Boxe`
- 五级类：不输出

这是本地合法 fixture 经 crawler 单次 Soup、商品解析、旁路注册和真实三格式写盘得到的结果。线上真实请求被 HTTP 511 阻断，不能声称线上 Boxe 已验证出该路径。

## 14. 一至五级处理规则

- 路径先完整清理和末级校验，再最多输出前五级。
- 一级只输出一级；不足五级时 JSON 不生成空的后续键。
- CSV/XLSX 按整批商品最高真实层级生成动态列，单条不足部分留空。
- 六级及以上先用完整末级验证，然后只输出前五级，不增加六级类。
- 空字符串、重复分隔符和重复类目不成为有效层级。
- 不写死 `Audio Hi-Fi`、Boxe 映射或任何人工类目表。

## 15. 是否新增 HTTP 请求

否。

只分析 crawler 已经获取的列表页响应，不请求商品详情页，不增加面包屑 API、类目树 API 或额外页面。动态本地 HTTP 集成测试明确断言单页请求数为 1。

## 16. 每页 Soup 解析次数

- HTTP 200 商品页：严格 1 次。
- HTTP 403/429/511：0 次，保持既有逻辑。
- 类目证据函数直接接收 crawler 已创建的 Soup，不重新解析 HTML。
- 60 张商品卡性能测试实际计数：fetch=1、Soup=1、层级提取=1。

## 17. 类目层级是否按页面提取一次

是，每个页面最多调用一次类目证据提取；不会按商品重复提取。

同一类目一旦取得有效旁路证据，后续分页直接复用缓存，不再扫描面包屑或 JSON 脚本。若前页没有任何有效证据，后续页面可以在自己的现有 Soup 上尝试一次，但仍不增加 DOM 解析或 HTTP 请求。

## 18. 线程安全和跨类目隔离实现

- `CategoryLevelRegistry` 使用 `RLock` 保护按规范化 URL 存储的不可变证据。
- query 参数、`www.` 和分页段不影响同类目命中。
- 不同主机、端口或类目路径使用不同键。
- crawler 使用“类目 URL → 独立 Lock”的双重检查，阻止同类目并发页面重复提取。
- 后到的较短路径不能覆盖先到的完整路径；同长度时只有更可靠来源可以替换。
- 两类目并发和同类目 20 线程专项测试均通过，没有串用。

## 19. 修改文件清单

V2.2.1 分支：

- `category_hierarchy.py`：页面证据提取、统一校验、候选选择、URL/名称标准化和线程安全注册表。
- `crawler.py`：在唯一 Soup 上按类目提取或复用旁路证据。
- `exporters.py`：线程安全注册/查询旁路证据，并传入统一产品导出边界。
- `output_schema.py`：接受可选已验证层级，缺失时回退原 extra。
- `main.py`、`VERSION`、crawler summary：版本更新为 2.2.1。
- `README.md`、`CHANGELOG.md`：V2.2.1 开发日志和验证结果。
- `tests/fixtures/v221_boxe_listing_minimal.html`：脱敏最小列表页结构。
- `tests/test_v221_category_hierarchy.py`：38 项专项测试。
- `tests/test_v220_chinese_exports.py`：run_summary 版本断言更新为 2.2.1。
- `TASK_REPORT_V2.2.1.md`：本报告。

main 分支只修改 README/CHANGELOG。

## 20. 新增测试名称和关键断言

新增 38 个 pytest 用例，分为：

- `TestPageEvidenceSources`：可见面包屑、JSON-LD、嵌入状态、父链补叶子、已有叶子不重复、更完整来源选择、不一致拒绝、隐藏面包屑拒绝、根节点和名称标准化。
- `TestCandidateFallbackAndBoundaries`：页面旁路优先、两级原有回退、不一致回退、一级至六级、空白、罗马尼亚语和去重。
- `TestRegistryConcurrencyAndIsolation`：URL 规范化、不同类目隔离、完整路径保护、更完整/更可靠替换和多线程注册。
- `TestCrawlerPerformanceAndReuse`：60 卡单 Soup 单提取、同类目多页复用、同类目并发只提取一次、不同类目并发隔离。
- `TestProductionWriteAndDataIntegrity`：动态端口本地 HTTP、请求数=1、JSON/CSV/XLSX 回读、四级动态列、数值类型、BOM、extra 和全部非类目字段不变。

没有删除旧测试、降低断言、使用 `pass` 或在测试中复制生产算法。

## 21. 专项测试结果

- V2.2.1 专项：`38 passed, 17 warnings, 0 failed in 1.25s`
- V2.2.0 中文导出 + V2.2.1 专项最终组合：`70 passed, 17 warnings, 0 failed in 1.51s`
- parser/crawler/exporter/WAF/分页相关回归：`161 passed, 234 warnings, 0 failed in 38.18s`

## 22. 两次完整测试结果

- 第 1 次：`274 passed, 245 warnings, 0 failed in 117.54s`
- 第 2 次：`274 passed, 245 warnings, 0 failed in 116.41s`

基线为 236，通过数增加 38。警告均来自 lxml/BeautifulSoup 的 `strip_cdata` 依赖弃用提示。

## 23. pip check、编译和 diff 检查结果

- `pip check`：`No broken requirements found.`
- `python -m py_compile`：修改的生产与测试 Python 文件全部退出码 0。
- `python main.py --version`：`eMAG Crawler V2.2.1`。
- 任务文件 `git diff --check` 和暂存区 `git diff --cached --check`：退出码 0。
- 全工作区只保留老大自己的 `config/categories.txt` 修改，该文件未进入提交。

## 24. JSON/CSV/XLSX 实际写盘数量

独立于 pytest 临时目录，使用生产 Exporters 写入 `output/v221_actual_write_20260811/` 后重新读取：

- products.json：2
- products.csv：2
- products.xlsx：2

同时确认 CSV BOM、最高四级动态列、XLSX 价格/评分/评价数量数值类型和 JSON extra 对象类型。

## 25. 修复前后字段数量对比

- ProductItem 普通字段：仍为原有 34 项，没有新增模型字段。
- 旁路信息只存在注册表和 PageResult 分析元数据，不写入 ProductItem 或 extra。
- Boxe 原商品卡回退路径为三级；页面证据完善后只增加授权的 `四级类=Boxe`。
- JSON 不增加空的五级类。
- 实际写盘输入 2 条，三种输出均为 2 条。

## 26. 非类目字段值一致性结果

测试删除“一级类至五级类”后，对修复前回退输出和旁路增强输出进行完整字典深度比较，结果完全相等。

已覆盖 PNK、product_id、offer_id、标题、URL、三个价格、折扣、库存、品牌、商品标签、链接打标、评分、评价数量、图片 URL、本地图片路径、时间、HTTP 状态、解析来源和完整 extra。内部 ProductItem 和 Exporters 英文字典在导出前后也完全相等。

## 27. 真实 HTTP 验证结果

- 北京时间：`2026-08-11T14:42:11+08:00`
- URL：`https://www.emag.ro/boxe/c?ref=bc`
- 条件：纯 HTTP、1 页、无图片、page/category/max-in-flight 均为 1。
- HTTP 状态：`511`
- 证据：`HTTP 511; captcha in body; WAF in body`
- 程序版本：`2.2.1`
- 状态：`waf_blocked`
- 退出码：`3`
- 商品：`0`
- 完成类目：`0/1`
- JSON/CSV/XLSX：`0/0/0`
- 重试：否，首次 511 后立即停止。
- 临时配置：已删除。

**真实HTTP验证因WAF阻断暂未完成。**

## 28. 已知限制

- HTTP 511 阻止了当前线上 Boxe HTML 调查，无法确认实时页面实际提供可见面包屑、JSON-LD 还是嵌入状态，也无法在线验证 PNK `DR8D26BBM`。
- 明确嵌入状态只解析 JSON 或带受控初始化标记的脚本，不执行任意 JavaScript；无法解析的脚本会安全忽略。
- 如果所有页面和卡片来源都无法通过末级一致性校验，本版本宁可不输出层级，也不会猜测。
- CSV 按标准保存数值文本；JSON 和 XLSX 保持原生数值类型。
- V2.2.1 仍是开发分支，待 ChatGPT 和老大最终验收后再决定稳定归档。

## 29. 最终分支 SHA

功能提交推送后：

- `V2.1.1`：`a94cc5c0a0dbb14011018e856c827f7b69eda088`
- `V2.1.2`：`ff6963a64aa09b65ab489d0903f9cf1806964b88`
- `V2.1.3`：`4306b1fb1176dde258f892cc803c52550fc241a8`
- `V2.1.4`：`6f28db70231a1a66e3c931944b46061392b83a12`
- `V2.2.0`：`5c444806b382b56c13c7afe5eaab31d94288afdc`
- `main`：`383c1e8fdf38f75c257558ac42786faf2f567604`
- `V2.2.1` 功能提交：`237b5338f7955f4d0bc5c777ebb303f7f0127789`

报告提交后会再次读取远程，确认除 main 文档提交和 V2.2.1 外，其他分支没有变化。

## 30. 需要 ChatGPT 重点复核的文件、函数和准确行号

- `category_hierarchy.py:34`：类目名称一致性标准化。
- `category_hierarchy.py:42`：类目 URL 规范化和分页复用键。
- `category_hierarchy.py:100`：根节点、重复层级和当前末级校验。
- `category_hierarchy.py:126`：不拼接的统一候选选择。
- `category_hierarchy.py:141`：页面可见/JSON-LD/嵌入来源总入口。
- `category_hierarchy.py:181`：页面旁路优先和原卡片路径回退。
- `category_hierarchy.py:210`：线程安全注册表和较差证据保护。
- `category_hierarchy.py:273`：可见面包屑提取。
- `category_hierarchy.py:314`：JSON-LD BreadcrumbList 提取。
- `category_hierarchy.py:363`：受控嵌入 JSON 提取。
- `crawler.py:210`：HTTP 200 单 Soup 生产调用链。
- `crawler.py:338`：按类目独立锁、双重检查和跨页复用。
- `exporters.py:65`：旁路层级注册/读取。
- `exporters.py:89`：三格式统一导出时注入旁路层级。
- `output_schema.py:92`：统一页面证据/卡片回退选择。
- `output_schema.py:153`：不修改内部字段的最终导出边界。
- `tests/test_v221_category_hierarchy.py:67`：数据源测试。
- `tests/test_v221_category_hierarchy.py:240`：并发和隔离测试。
- `tests/test_v221_category_hierarchy.py:298`：性能及跨页复用测试。
- `tests/test_v221_category_hierarchy.py:444`：动态 HTTP、实际写盘和数据不变。

## 安全核验

- 没有提交 config、用户备份、output、日志、图片、Cookie、Token、Authorization、真实响应、虚拟环境或缓存。
- 没有使用浏览器、Playwright、Chromium、Selenium 或详情页请求。
- 没有执行 hard reset、clean、强制推送或破坏用户文件的命令。
- main 文档 worktree 在确认路径和干净状态后通过 `git worktree remove` 安全移除。
