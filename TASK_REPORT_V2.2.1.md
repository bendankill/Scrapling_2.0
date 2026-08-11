# Scrapling V2.2.1 类目准确性与真实 HTTP 验证修复报告

## 1. 修复前完整 SHA

`32de8a6fe55d07e90e96540e92c0f438b5438329`

## 2. 功能提交完整 SHA

`69eac267895d45917677767b0f14caae16b3e32b`

提交：`fix: enforce V2.2.1 category evidence trust`

## 3. 报告提交后的最终完整 SHA

Git 提交不能在自身内容中可靠记录自己的 SHA（SHA 由包含本行在内的完整提交对象计算）。本报告作为功能提交后的独立文档提交；报告提交完成后的最终本地/远程 SHA 在最终回复和远程 `origin/V2.2.1` 中给出并核对。

## 4. 当前分支

`V2.2.1`，继续标记为“开发中/待 ChatGPT 验收”，未创建新分支。

## 5. 是否已推送

功能提交已使用普通快进推送到 `origin/V2.2.1`。报告提交后会再次普通推送。

## 6. 本地与远程 SHA 是否一致

功能提交推送后本地与远程均为 `69eac267895d45917677767b0f14caae16b3e32b`。报告提交后再次核对最终 SHA。

## 7. main、V2.2.0 和历史分支

任务期间未切换、合并或修改这些分支：

- main：`383c1e8fdf38f75c257558ac42786faf2f567604`
- V2.2.0：`5c444806b382b56c13c7afe5eaab31d94288afdc`
- V2.1.4：`6f28db70231a1a66e3c931944b46061392b83a12`
- V2.1.3：`4306b1fb1176dde258f892cc803c52550fc241a8`
- V2.1.2：`ff6963a64aa09b65ab489d0903f9cf1806964b88`
- V2.1.1：`a94cc5c0a0dbb14011018e856c827f7b69eda088`

## 8. config/categories.txt

Codex 未修改、删除、恢复、暂存或提交该文件。它继续保留老大的工作区修改，任务开始和结束时记录的 SHA-1 均为：

`b11babeab1ff76982395c87173d4147fdc4e0085`

仓库 `output/`、日志、图片、Cookie、认证信息、虚拟环境和缓存均未提交。真实验证使用仓库外临时目录，验证结果读取后已安全删除。

## 9. 三项问题根因

1. **错误 breadcrumb**：旧 `_is_category_url()` 只检查路径是否以 `/c` 结束，没有校验站点；`_has_category_id()` 错误信任通用 `data-id`；末级链接没有与当前 `page_url` 对比；无链接纯文字可见链仍可成为临时全局证据。
2. **缓存可信度倒置**：旧 `_prefer_consistent()` 先比较路径长度，较长临时证据可以压住较短最终证据。
3. **旧真实验证零请求**：Scrapling 0.4.12 将 `retries` 解释为总请求尝试次数。旧验证设置 `retries=0`，请求循环没有执行，随后抛出 `No active session available`；这不是 eMAG 网络错误，也不是 Session 环境故障。

## 10. breadcrumb 信任规则修复前后

修复前，`Contul meu / Ajutor / Boxe` 仅因末级文字匹配就会输出为三级临时候选。

修复后，可见 breadcrumb 必须具备可独立验证的父级类目链接或明确类目 ID；纯文字帮助链直接拒绝并回退商品自身路径。footer、隐藏、template、非类目链接等既有排除规则继续生效。

## 11. 类目 URL 同源验证规则

- 相对 URL 通过 `urljoin(page_url, href)` 按当前页面解析。
- 绝对 URL 必须与当前页面的协议、主机和有效端口一致。
- `www.emag.ro` 与 `emag.ro` 作为明确站点别名归一化。
- 本地动态 HTTP 测试使用实际随机端口，不写死测试地址。
- 查询参数不参与类目身份比较。
- `/boxe/p2/c` 与 `/boxe/c` 通过既有 URL 规范化识别为同一类目。
- `https://evil.example/audio/c` 即使以 `/c` 结束也会拒绝。

## 12. data-id 与明确类目 ID

- `data-id`：不再信任。
- `data-category-id`、`category-id`：在无链接节点上可以作为明确类目证据。
- 节点一旦有非空链接，该链接必须通过同源类目 URL 校验；`/account`、`/help` 等不能被类目 ID 掩盖。

## 13. 缓存证据优先级

从高到低：

1. 最终且父级类目链接已验证；
2. 最终且结构化证据可信；
3. 其他最终证据；
4. 临时证据；
5. 无法独立验证的辅助证据。

先比较证据等级；只有等级相同且路径一致时才比较完整度，最后才比较来源可靠性分值。

## 14. 临时/最终替换矩阵

| 现有缓存 | 后续证据 | 结果 |
|---|---|---|
| 临时短 | 最终长 | 升级为最终长 |
| 临时长 | 最终短 | 升级为最终短 |
| 最终短 | 临时长 | 保留最终短 |
| 最终长 | 临时短 | 保留最终长 |
| 同级一致短 | 同级一致长 | 采用同级长 |
| 同级冲突 | 同级冲突 | 不覆盖 |

Registry 比较与替换在同一 `RLock` 内完成。Crawler 最终证据直接复用；临时候选仍保留每类目最多 3 次升级扫描，第三次出现最终证据也能替换成功。

## 15. 两条高可信冲突证据

页面候选选择返回无页面旁路证据，导出回退商品 `favorite_data.category_trail` 或 `data-category-trail`。Registry 已有最终证据时，冲突最终证据不得按长度或到达顺序覆盖。

## 16. 是否新增 HTTP 请求

生产抓取没有新增请求，不访问商品详情页、breadcrumb API 或类目树 API。类目校验只复用当前列表页 Soup。

自动化测试使用本地动态 HTTP 服务；规定的线上验证只请求 eMAG 一次。

## 17. 每页 Soup 解析次数

HTTP 200 页面严格 1 次；403/429/511 保持 0 次。没有重新解析同一 HTML。

## 18. 每页类目提取次数

每页最多 1 次，不按商品重复提取。最终缓存后的后续页为 0 次；临时缓存每类目最多 3 次升级扫描。

## 19. 新增测试和关键断言

新增 `tests/test_v221_category_accuracy_s1_followup.py`，共 35 项：

- `TestStrictBreadcrumbOriginAndIdentity`：15项，覆盖纯文字帮助链、普通data-id、明确类目ID、冲突链接、外域、相对/绝对同源URL、eMAG别名、错误末级、分页、两级商品回退和全无效边界。
- `test_registry_trust_replacement_matrix`：6项参数化矩阵，逐一验证临时/最终、长/短、一致/冲突顺序。
- `TestCacheTrustCrawlerAndConcurrency`：6项，覆盖高可信冲突、乱序线程、Crawler临时长到最终短、最终缓存复用、第三次升级和跨类目隔离。
- `TestSessionRetryAndLifecycle`：7项，覆盖0和负数拒绝、`retries=1`真实本地请求、同线程复用、跨线程隔离、finalize关闭和Cookie连续性。
- `TestThreeFormatTrustBoundary`：1项，真实写出并回读三级/四级/五级JSON、CSV、XLSX。

旧测试没有删除；因信任规则变更，只把旧纯文字测试补上合法链接或改为新规则的拒绝断言。

## 20. 修复前测试基线

`304 passed`

## 21. 修复后专项和相关回归

- 本轮新增专项：`35 passed, 27 warnings, 0 failed`
- V2.2.1类目与上一轮S1：`68 passed, 41 warnings, 0 failed`
- parser/crawler/exporter/Session/WAF/分页/图片相关回归：`258 passed, 2 skipped, 217 warnings, 0 failed`
- 2项跳过来自既有环境条件，不是失败。

## 22. 两次完整测试

- 第一次：`339 passed, 302 warnings, 0 failed in 132.19s`
- 第二次：`339 passed, 302 warnings, 0 failed in 132.09s`
- 304基线 + 35新增 = 339，测试总数没有减少。
- 警告为 lxml/BeautifulSoup 的既有 `strip_cdata` 弃用提示。

## 23. 三次并发与缓存乱序测试

连续三次均为：`11 passed, 54 deselected, 8 warnings, 0 failed`，耗时分别为 0.73s、0.68s、0.69s。

## 24. pip check、编译和 diff 检查

- `pip check`：`No broken requirements found.`
- 所有 Git 跟踪 Python 文件及新增测试执行 `python -m py_compile`：退出码0。
- `git diff --check` 与暂存区 `git diff --cached --check`：退出码0。
- 环境检查未发现 `ALL_PROXY`、`HTTP_PROXY` 或 `HTTPS_PROXY`，本地动态 HTTP 测试未受代理干扰。
- 敏感信息扫描未发现 Cookie、Authorization、Bearer Token 或 API Key 实值。

## 25. JSON、CSV、XLSX 实际写盘

生产 `Exporters.finalize()` 的三级/四级/五级写盘测试：

- products.json：3条
- products.csv：3条
- products.xlsx：3条
- JSON按单条实际层级省略空键；CSV/XLSX按整批最高五级生成动态列。
- CSV含UTF-8 BOM；JSON扩展信息为对象；XLSX价格、评分、评价数量保持数值类型。

真实HTTP验证写盘：JSON/CSV/XLSX各60条。

## 26. 非类目字段一致性

测试对导出前后 ProductItem 和内部英文商品字典进行深度比较，结果完全一致。PNK、产品ID、Offer ID、标题、URL、价格、库存、品牌、标签、链接打标、评分、评价数、图片路径和原始extra均未修改；除一级至五级类外没有新增产品顶层字段。

## 27. retries=0 旧验证失败的真实原因

Scrapling 0.4.12 的 `retries` 是“总尝试次数”，不是“首次请求之外的重试次数”。旧验证设置0后，请求循环执行0次，随后才抛出 `RuntimeError: No active session available`。因此旧报告中的“当前Scrapling会话环境错误”归因已从README/CHANGELOG/本报告更正。

生产默认仍为3。本次新增 `_validated_session_retries()`，任何布尔值、非整数、0或负数都会在创建 Session 前抛出包含实际值的清晰 `ValueError`。

## 28. 新真实验证 retries 配置

`retries=1`，含义为总请求尝试次数1，不进行第二次尝试。

同时使用：1类目、1页、无图片、`page_workers=1`、`category_workers=1`、`max_in_flight=1`、纯HTTP、无浏览器、无详情页。

## 29. 真实 HTTP 结果

- 时间：2026-08-11 16:17:46（Asia/Shanghai）
- URL：`https://www.emag.ro/boxe/c?ref=bc`
- 实际请求次数：1
- HTTP：200
- 最终URL：与请求URL一致
- Content-Type：`text/html; charset=UTF-8`
- 响应长度：934779字节
- 程序状态：`completed`
- 退出码：0
- 候选卡片：60
- 成功解析：60
- 解析失败：0
- 商品记录：60
- 完成类目：1/1
- JSON/CSV/XLSX：60/60/60
- PNK `DR8D26BBM`：当前页不存在
- 当前页面可验证输出：`TV, Audio-Video & Foto / Audio HI-FI & Profesionale / Boxe`

实际响应没有提供足够可信证据补出 `Audio Hi-Fi`，因此没有按旧示例猜测中间层级。

## 30. WAF结果

本次没有遇到403、429或511，未进入WAF状态。现有WAF状态、退出码3和零Soup解析测试继续通过。

## 31. 网络错误记录

本次真实请求没有网络错误。生产 `FetchResult` 现会保留底层异常类型与详情；日志详情包括实际retries、manager/client是否进入上下文、线程ID和是否已调用请求方法，不再只记录笼统HTTP 0。

## 32. 已知限制

- 当前线上Boxe第一页的可信数据只证明三级路径，无法证明旧示例中的四级 `Audio Hi-Fi / Boxe`；本版本遵循“宁缺勿猜”。
- 无父级链接/类目ID的可见纯文字breadcrumb被直接拒绝；合法层级仍可从JSON-LD、嵌入状态或商品自身路径回退。
- 两条高可信页面路径冲突时不自动裁决，回退商品自身路径。
- V2.2.1仍待ChatGPT与老大验收。

## 33. 需要 ChatGPT 重点复核的位置

- `category_hierarchy.py:164`：`_evidence_tier()`
- `category_hierarchy.py:183`：`_prefer_consistent()`
- `category_hierarchy.py:225`：`extract_page_category_evidence()`
- `category_hierarchy.py:260`：`_descriptor_to_evidence()`
- `category_hierarchy.py:439`：`_registry_replacement()`
- `category_hierarchy.py:470`：`_is_category_url()`
- `category_hierarchy.py:485`：`_leaf_category_url_matches()`
- `category_hierarchy.py:500`：`_has_category_id()`
- `category_hierarchy.py:535`：`_visible_breadcrumb_paths()`
- `category_hierarchy.py:603`：`_json_ld_breadcrumb_paths()`
- `crawler.py:139`：`_validated_session_retries()`
- `crawler.py:147`：`_get_client()`
- `crawler.py:174`：`_fetch_page()`及底层异常记录
- `crawler.py:366`：`_get_or_extract_category_evidence()`
- `tests/test_v221_category_accuracy_s1_followup.py:90`：同源、ID、末级与回退反例
- `tests/test_v221_category_accuracy_s1_followup.py:235`：缓存替换矩阵
- `tests/test_v221_category_accuracy_s1_followup.py:242`：Crawler并发和升级
- `tests/test_v221_category_accuracy_s1_followup.py:355`：Session、retries和Cookie
- `tests/test_v221_category_accuracy_s1_followup.py:420`：三格式写盘与数据不变
