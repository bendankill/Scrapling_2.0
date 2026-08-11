# Scrapling V2.2.1 类目层级准确性 S1 修复报告

## 1. 完成状态

自动化修复、生产调用链验证、并发验证、三格式写盘、文档更新、功能提交和推送均已完成。V2.2.1 仍标记为“开发中/待 ChatGPT 验收”，不是稳定版。

真实 HTTP 验证未完成：当前环境中的 Scrapling 会话在获得 eMAG HTTP 响应前报出 `No active session available`（HTTP 0）。本报告不把该结果记为 eMAG 页面或 WAF 验证通过。

## 2. 当前分支

`V2.2.1`

## 3. 修复前完整 SHA

`de9b1107216ec6f361876008baed8e8506597da7`

## 4. 修复后功能完整 SHA

`ee8667ce5e4ec82f9b1297b1fae5be12b27d725c`

提交：`fix: harden V2.2.1 category hierarchy accuracy`

本报告在功能提交之后单独提交，因此最终发布 HEAD 以报告提交完成后的本地/远程核验值为准。

## 5. 本地与远程是否一致

功能提交推送后，本地 `HEAD` 与 `origin/V2.2.1` 均为 `ee8667ce5e4ec82f9b1297b1fae5be12b27d725c`。报告提交后会再次核对最终 HEAD。

## 6. 是否已推送

是。使用普通推送，没有强制推送、合并 main、创建 PR 或标签。

## 7. config/categories.txt 是否未修改

Codex 未编辑、恢复、暂存或提交 `config/categories.txt`。它继续作为老大的工作区修改保留，当前 SHA-1 为 `b11babeab1ff76982395c87173d4147fdc4e0085`，不属于本次提交。

未发现并且未创建 `config/categories - 副本.txt1`；所有 `output/`、临时配置和运行产物均未提交。

## 8. 三个 S1 问题根因

1. **S1-1 无关 breadcrumb**：旧逻辑宽泛扫描任意属性中含 `breadcrumb` 的节点，并把 URL/H1 当作可向任意父链追加当前类目的授权；候选又先按长度排序，因此更长帮助导航可能获胜。
2. **S1-2 短页面路径覆盖**：旧 `select_product_category_levels()` 一收到页面旁路层级就立即返回，没有与商品 `favorite_data.category_trail` 和 `data-category-trail` 共同比较。
3. **S1-3 缓存锁死**：crawler 发现任何缓存就直接返回；虽然 Registry 能替换较短路径，后续页面证据却永远没有机会进入 Registry。

## 9. 无关 breadcrumb 复现修复前后

输入：URL `/boxe/c`、H1 `Boxe`、可见导航 `eMAG / Contul meu / Ajutor comenzi`。

- 修复前：`Contul meu / Ajutor comenzi / Boxe`
- 修复后：拒绝页面路径，返回 `None`；URL/H1 不再授权追加。

## 10. 多 breadcrumb 冲突修复前后

同时存在 `eMAG / TV / Audio / Boxe` 和更长的 `eMAG / Contul meu / Ajutor / Comenzi / Livrare`：

- 修复前：错误选择更长帮助路径并追加 `Boxe`
- 修复后：选择具有连续 `.../c` 类目链接的 `TV / Audio / Boxe`
- 两条互相冲突且都具有独立高可信类目证据时：放弃页面旁路证据，不按长度或来源序号猜测。

## 11. 短页面路径覆盖商品完整路径修复前后

页面 `Audio / Boxe`，商品 `TV, Audio-Video & Foto / Audio HI-FI & Profesionale / Audio Hi-Fi / Boxe`：

- 修复前：输出两级 `Audio / Boxe`
- 修复后：保留商品四级完整路径

页面与商品路径现在进入同一选择流程；相互一致时选择更完整链，冲突时短页面链不能删除完整商品链。

## 12. 缓存升级修复前后

第一页两级临时候选 `Audio / Boxe`，第二页四级类目链接证据：

- 修复前：缓存一直为两级
- 修复后：升级为 `TV, Audio-Video & Foto / Audio HI-FI & Profesionale / Audio Hi-Fi / Boxe`
- 后续较短、低可信或冲突路径均不能覆盖最终四级证据。

## 13. 最终证据数据结构

`CategoryPathEvidence` 保存：

- `levels`
- `source`
- `reliability`
- `current_category_explicit`
- `current_category_appended`
- `category_links_verified`
- `structured_breadcrumb`
- `is_tentative`
- `validation_reason`

## 14. 父级追加当前类目的严格条件

只有父节点全部具有明确 eMAG 类目 URL（路径以 `/c` 结束）或明确类目 ID，才允许把可靠当前类目补到父链末级。URL slug 或 H1 只能确认当前页面名称，不能验证任意父链。

footer、隐藏祖先、script、template、noscript、head、仅 `data-testid` 名称和带非类目父链接的路径均被拒绝。

## 15. 候选路径一致性规则

- 两条路径末级必须标准化一致。
- 较短路径必须按原顺序成为较长路径的子序列。
- 一致路径可选择更完整链，不拼接两条独立路径。
- 当前类目位于中间而非末级时拒绝。
- 大小写、重音、标点、连字符和多余空格按既有标准化规则比较。

## 16. 可靠性与完整度排序规则

- 先判断是否为真实类目证据，再判断一致性。
- 一致路径才允许用完整度补层级。
- 冲突时最终/类目链接/结构化证据优先于临时候选。
- 两条独立高可信路径冲突时返回无页面证据。
- 低可信长路径不能仅凭长度覆盖高可信冲突路径。
- 页面短路径不覆盖更完整商品原路径。

## 17. 缓存临时候选和最终证据规则

- 嵌入 JSON、无类目链接的显式叶子等标记为临时候选。
- 经父级类目链接验证的可见 breadcrumb 或 JSON-LD 标记为最终证据。
- 临时候选每类目最多进行 3 次页面升级扫描。
- 最终证据直接跨页复用，避免重复高成本扫描。
- Registry 在 `RLock` 内原子比较；允许一致的更完整升级和临时到最终升级，禁止降级与冲突覆盖。
- 不同规范化 category URL 使用独立缓存键和 crawler 锁。

## 18. 是否新增 HTTP 请求

否。只复用 crawler 已获得的列表页 Soup；不请求详情页、breadcrumb API 或类目树 API。

## 19. 每页 Soup 解析次数

HTTP 200 页面严格为 1 次；403/429/511 保持 0 次。没有重新解析同一 HTML。

## 20. 每页类目提取次数

每页最多 1 次，60 张商品卡不会触发 60 次提取。最终缓存后的分页为 0 次；临时候选允许后续页面每页 1 次、每类目总计最多 3 次升级检查。

## 21. 修改文件清单

- `category_hierarchy.py`
- `crawler.py`
- `exporters.py`
- `output_schema.py`
- `tests/fixtures/v221_boxe_listing_minimal.html`
- `tests/test_v221_category_hierarchy.py`
- `tests/test_v221_category_accuracy_s1.py`（新增）
- `README.md`
- `CHANGELOG.md`
- `TASK_REPORT_V2.2.1.md`

## 22. 新增测试名称和关键断言

新增 30 项测试，集中在 `tests/test_v221_category_accuracy_s1.py`：

- `TestS1UnrelatedBreadcrumbs`：10 项，覆盖 URL/H1 错误授权、帮助导航、更长帮助链、data-testid、footer、隐藏/template、类目链接追加和 JSON-LD 正反例。
- `TestS1EvidenceConflictRules`：7 项，覆盖可靠性优先、一致扩展、两条高可信冲突、多个嵌入路径、末级校验与子序列判断。
- `TestS1PageProductComparison`：5 项，覆盖页面两级与商品四级、页面补中间层级、冲突回退和三来源回退。
- `TestS1CrawlerCacheUpgrade`：7 项，使用真实 crawler 方法覆盖升级、禁止降级、冲突保护、同类目/不同类目并发和 3 次扫描上限。
- `TestS1ProductionWrite`：1 项，真实调用 `Exporters.finalize()` 并回读三格式，校验四级、BOM、数值类型、extra、ProductItem 不变和顶层白名单。

旧测试未删除，断言未放宽；两项旧“普通父文字可仅靠 H1 追加”的断言按 S1 新规则改为“拒绝”或补充真实类目链接证据。

## 23. 专项测试结果

- V2.2.1 类目 + S1：`68 passed, 41 warnings, 0 failed`
- V2.2.0 中文导出 + V2.2.1 类目/S1 相关回归：`100 passed, 41 warnings, 0 failed`
- 警告仅为 lxml/BeautifulSoup `strip_cdata` 弃用提示。

## 24. 两次完整测试结果

- 第一次：`304 passed, 269 warnings, 0 failed in 116.57s`
- 第二次：`304 passed, 269 warnings, 0 failed in 115.94s`
- 修复前基线：`274 passed`
- 净新增：30 项测试

## 25. 并发测试三连结果

连续三次均为：`5 passed, 63 deselected, 7 warnings, 0 failed`，耗时分别为 0.72s、0.69s、0.70s。

## 26. pip check、编译和 diff 检查

- `pip check`：`No broken requirements found.`
- 对全部 Git 跟踪 Python 文件执行 `python -m py_compile`：退出码 0
- `git diff --check`：退出码 0
- 暂存文件敏感信息扫描：未发现 Cookie、Authorization、Bearer、Token 或 API key

## 27. JSON/CSV/XLSX 实际写盘数量

独立生产 `Exporters.finalize()` 写盘回读：

- products.json：1
- products.csv：1
- products.xlsx：1
- CSV UTF-8 BOM：存在
- JSON extra：对象
- XLSX 价格/评分/评价数量：数值类型

动态端口本地 HTTP 生产集成测试也为三格式各 1 条、单页请求 1 次。

## 28. Boxe 示例实际输出

本地合法 fixture、真实 crawler/Exporter 调用链和独立写盘均输出：

- 一级类：`TV, Audio-Video & Foto`
- 二级类：`Audio HI-FI & Profesionale`
- 三级类：`Audio Hi-Fi`
- 四级类：`Boxe`
- 五级类：不输出

没有硬编码 `Audio Hi-Fi` 或 Boxe 人工映射；值来自测试页面的类目链接/结构化证据。

## 29. 非类目字段一致性结果

ProductItem 和内部英文商品字典在导出前后深度相等；原始 extra 未写入旁路字段。PNK、ID、标题、URL、价格、库存、品牌、标签、链接打标、评分、评价数、图片和采集元数据均未改变。除一级至五级类外没有新产品顶层字段。

## 30. 真实 HTTP 验证结果

- 时间：2026-08-11 15:30（Asia/Shanghai）
- URL：`https://www.emag.ro/boxe/c?ref=bc`
- 条件：纯 HTTP、1 页、无图片、page/category/max-in-flight 均为 1、`retries=0`
- 结果：HTTP 0；Scrapling 报错 `No active session available`
- 状态：`network_error`
- 逻辑退出码：2
- 商品：0
- JSON/CSV/XLSX：0/0/0
- 临时配置：已删除且未提交

该错误发生在取得 eMAG 响应之前，因此不能判断线上页面为商品页、WAF 或其他 HTTP 页面，也不能在线核验 PNK `DR8D26BBM`。真实 HTTP 验证暂未完成。

## 31. 已知限制

- 当前环境的 Scrapling 会话错误阻止线上验证；需要在可正常建立 Scrapling HTTP 会话的环境中重新进行一次低频验证。
- 无链接的显式 breadcrumb 叶子只作为临时候选；若后续页没有更强证据，最多检查 3 次后保留当前最佳结果。
- 两条独立高可信路径冲突时宁可回退商品路径或不输出页面层级，不自动裁决。
- V2.2.1 仍待 ChatGPT 与老大验收。

## 32. 分支最终 SHA

功能提交推送后：

- V2.1.1：`a94cc5c0a0dbb14011018e856c827f7b69eda088`
- V2.1.2：`ff6963a64aa09b65ab489d0903f9cf1806964b88`
- V2.1.3：`4306b1fb1176dde258f892cc803c52550fc241a8`
- V2.1.4：`6f28db70231a1a66e3c931944b46061392b83a12`
- V2.2.0：`5c444806b382b56c13c7afe5eaab31d94288afdc`（未变化）
- main：`383c1e8fdf38f75c257558ac42786faf2f567604`（未变化）
- V2.2.1 功能提交：`ee8667ce5e4ec82f9b1297b1fae5be12b27d725c`

报告提交后再次核对 V2.2.1 最终本地/远程 HEAD，并在最终回复给出。

## 33. 需要 ChatGPT 重点复核的文件、函数和准确行号

- `category_hierarchy.py:29`：`CategoryPathEvidence`
- `category_hierarchy.py:143`：`category_paths_are_consistent()`
- `category_hierarchy.py:177`：`select_best_category_evidence()`
- `category_hierarchy.py:207`：`extract_page_category_evidence()`
- `category_hierarchy.py:281`：`select_product_category_levels()`
- `category_hierarchy.py:369`：`CategoryLevelRegistry`
- `category_hierarchy.py:470`：`_visible_breadcrumb_paths()`
- `category_hierarchy.py:521`：`_json_ld_breadcrumb_paths()`
- `crawler.py:340`：`_get_or_extract_category_evidence()`
- `exporters.py:89`：`_to_output()`
- `output_schema.py:156`：`product_to_output_dict()`
- `tests/test_v221_category_accuracy_s1.py:71`：无关 breadcrumb 反例
- `tests/test_v221_category_accuracy_s1.py:164`：冲突选择反例
- `tests/test_v221_category_accuracy_s1.py:221`：页面/商品路径比较
- `tests/test_v221_category_accuracy_s1.py:259`：真实 crawler 缓存升级与并发
- `tests/test_v221_category_accuracy_s1.py:362`：三格式生产写盘
