# Scrapling V2.2.1 第二轮类目准确性与 Session 健壮性修复报告

## 1. 当前分支

`V2.2.1`。本分支继续标记为“开发中/待ChatGPT验收”，没有创建新分支、PR或标签，也没有合并main。

## 2. 修复前完整 SHA

`55bd1251f0cda6a074e1836d8adce93cf44a4f59`

## 3. 功能提交完整 SHA

`95b81ec1de152f2b314404b91c0f54fe0eeec5ad`

提交信息：`fix: harden V2.2.1 category evidence and sessions`

## 4. 推送与最终 SHA 说明

功能和报告使用两个普通提交。Git提交无法在自身文件内容里可靠记录自身SHA，因此报告/证据提交后的最终远程HEAD以Codex最终回复和远程Git记录为准；没有在本文伪造自引用SHA。

## 5. 历史分支保护

本轮开始时并在最终推送前后核对：

- main：`383c1e8fdf38f75c257558ac42786faf2f567604`
- V2.2.0：`5c444806b382b56c13c7afe5eaab31d94288afdc`
- V2.1.4：`6f28db70231a1a66e3c931944b46061392b83a12`
- V2.1.3：`4306b1fb1176dde258f892cc803c52550fc241a8`
- V2.1.2：`ff6963a64aa09b65ab489d0903f9cf1806964b88`
- V2.1.1：`a94cc5c0a0dbb14011018e856c827f7b69eda088`

本轮未切换或修改上述分支。GitHub默认分支仍为main；main首页已存在V2.2.0稳定版和V2.2.1开发分支记录，因此遵守“不修改main”的任务限制。

## 6. config/categories.txt 保护

- 开始前SHA-256：`c31ff9df4f2c3c96181ac24b611d6fe7780704cf359aa2df09ef1b13248ba8a4`
- 完成开发、测试和真实验证后SHA-256：`c31ff9df4f2c3c96181ac24b611d6fe7780704cf359aa2df09ef1b13248ba8a4`
- Codex未读取其类目业务内容，未修改、删除、恢复、暂存或提交该文件。

## 7. 本轮六个问题的复现与根因

1. **标准Schema.org父级链接漏取**：`span[itemprop=name]`只能向自身或子级找链接，没有在同一`itemListElement`内识别父级`a[itemprop=item]`；修复前标准示例返回`None`。
2. **节点元数据错位**：名称先独立去重，再用删除数量切片URL/ID；中间重复节点使名称与另一节点的URL/ID绑定。
3. **同页临时冲突顺序依赖**：遍历嵌入JSON时逐步覆盖best；交换`categoryPath`和`breadcrumb`键顺序，修复前分别返回`TV/Audio/Boxe`与`Marketplace/Recommended/Boxe`。
4. **跨页最终证据锁死**：Registry没有冲突状态，保留第一条冲突最终路径；Crawler发现最终缓存后停止检查后续页面。
5. **finalize后复用关闭Session**：全局Session列表清空，但工作线程thread-local仍持有旧manager/client；修复前关闭后`_get_client()`返回同一对象。
6. **请求诊断阶段错误**：异常分支固定写`request_call_started=True`；即使客户端创建前失败也错误声称已经调用请求。

## 8. 修复前后行为对比

| 问题 | 修复前 | 修复后 |
|---|---|---|
| 标准Schema | 合法父级链接无法提取，返回None | 以完整itemListElement解析，得到正确路径 |
| 中间重复 | 名称/URL/ID可能错位 | 完整节点整体排序、清理、去重 |
| 同页临时冲突 | 取决于JSON键顺序 | 两种顺序均为temporary_conflicted，公共路径停用 |
| 跨页最终冲突 | 第一条最终路径继续输出 | 进入粘性final_conflicted，所有商品回退自身路径 |
| finalize后复用 | 返回已关闭客户端 | generation不匹配时创建新活动Session |
| 请求前失败诊断 | request_call_started=True | False，并标记configuration/session/before_get阶段 |

## 9. 标准Schema.org父级链接解析

解析器先定位完整`itemListElement`或等价列表项，再在该项内提取name、URL、category_id和position。支持：节点本身为链接、名称内部链接、名称位于父级`a[itemprop=item]`、同项兄弟名称/URL、`meta[itemprop=position]`、JSON-LD及原有可见结构。

## 10. 父级链接查找边界

从name节点向父级查找时遇到当前逻辑item即停止；随后只搜索该item的`itemprop=item`或链接后代。不会越过item使用外层`/help`或其他`/c`链接。上一轮同源、末级、明确类目ID及非类目链接冲突规则全部保留。

## 11. 名称、URL、ID整体模型

`CategoryBreadcrumbNode`不可拆分地保存：`name`、`url`、`category_id`、`position`和`source`。`_PathDescriptor`保存节点元组，levels只由节点名称投影，不再维护独立平行数组。

## 12. 中间重复与元数据对齐

position排序、空名称清理、开头Home/Acasă/eMAG移除和标准化去重均对完整节点执行。`Home/TV/Audio/Audio/Boxe`清理后保留`TV/Audio/Boxe`，对应URL与ID仍分别来自同一原节点。完整路径先验证当前类目末级和链接，导出时才截取前五级。

## 13. 同页候选先收集后裁决

可见面包屑、JSON-LD和全部嵌入类目字段先转为`CategoryPathEvidence`候选；`decide_category_evidence()`在收集结束后一次裁决，不在遍历过程中覆盖best。

## 14. 字段与DOM顺序无关

候选按规范化路径和确定性元数据排序；相同等级只在路径一致时比较完整度。测试交换JSON键顺序并随机打乱候选20次，结果始终一致。跨线程注册顺序也由原子状态机决定。

## 15. 同页临时冲突

两条同等级临时路径不一致时返回`temporary_conflicted`且evidence为空，不写入可用公共路径。商品按`favorite_data.category_trail`、`data-category-trail`顺序回退；后续合法最终证据仍可把Registry升级为final。

## 16. 同页最终冲突

两个独立最终证据冲突时返回`final_conflicted`。公共页面路径停用，不按长度、来源、DOM顺序或到达顺序猜测。

## 17. Registry状态

- `missing`：尚无证据；
- `temporary`：有可用临时证据；
- `temporary_conflicted`：临时证据冲突，公共路径停用；
- `final`：有已接受最终证据；
- `final_conflicted`：最终证据冲突，公共路径永久停用至本次运行结束。

## 18. Registry状态转换表

| 当前状态 | 临时候选 | 最终候选 | 临时冲突 | 最终冲突 |
|---|---|---|---|---|
| missing | temporary | final | temporary_conflicted | final_conflicted |
| temporary | 一致则择完整，冲突则temporary_conflicted | final | temporary_conflicted | final_conflicted |
| temporary_conflicted | 保持冲突 | final | 保持冲突 | final_conflicted |
| final | 忽略低级候选 | 一致则择完整，冲突则final_conflicted | 保持final | final_conflicted |
| final_conflicted | 保持粘性冲突 | 保持粘性冲突 | 保持粘性冲突 | 保持粘性冲突 |

## 19. 临时/最终替换矩阵

- 临时长 → 最终短：采用最终短；
- 最终短 → 临时长：保留最终短；
- 临时短 → 最终长：采用最终长；
- 最终长 → 临时短：保留最终长；
- 同级一致：采用更完整路径；
- 同级临时冲突：temporary_conflicted；
- 同级最终冲突：final_conflicted。

## 20. 三页证据确认

Crawler按逻辑`page_number`仅观察1、2、3页。第1页得到final后，第2/3页仍各提取一次以发现冲突；第4页及以后不再提取公共层级。观察集合不由线程完成顺序决定。

## 21. HTTP与Soup性能边界

- 没有为类目确认新增HTTP请求；只观察本来计划抓取的页面。
- 不请求商品详情页。
- HTTP 200每页只创建一次Soup。
- 每个观察页面只调用一次类目证据提取。
- 不按商品卡重复提取。
- 每类目最多观察3个逻辑页面。

## 22. 后发现冲突时早期商品回退

ProductItem和extra从未写入公共路径。Exporter只在最终写JSON/CSV/XLSX时查询Registry并调用统一输出转换；若第3页才进入final_conflicted，之前加入Exporter的第1、2页商品也会在写盘时回退各自原始extra路径，无需重请求、重解析或修改落盘文件。

## 23. 商品自身路径回退顺序

1. `favorite_data.category_trail`
2. `data-category-trail`
3. 两者均无效时不输出一级至五级，不猜测

## 24. Session generation设计

Crawler全局维护`_session_generation`。thread-local同时记录客户端generation；`_get_client()`只有在代次匹配且manager/client仍活动时才复用。Session创建、代次读取和全局注册在`_sessions_lock`内完成。

## 25. finalize与旧thread-local失效

`_close_all_sessions()`在锁内取出当前Session列表、清空并递增generation，然后关闭旧manager。其他线程无需从主线程强制清空thread-local；下次调用自动发现代次不匹配并建立新上下文。重复finalize不会重复关闭时报错。

## 26. finalize后再次请求结果

本地动态HTTP服务器验证：同一线程旧对象与新对象不同，新manager处于活动状态并能返回HTTP 200；单线程ThreadPoolExecutor复用同一工作线程时也不会返回旧客户端；“请求—finalize—再请求”连续两轮成功。

## 27. request_call_started准确语义

初始为False；客户端成功获取并完成活动检查后，紧邻调用`client.get()`前设为True。它表示已经开始调用客户端方法，不声称数据包已发送成功。`response_received`仅在`client.get()`返回响应对象后为True。

## 28. 异常阶段区分

- `configuration_validation`：非法retries，请求前失败；
- `session_creation`：Session创建/进入上下文失败；
- `before_client_get`：客户端已取得但活动校验或get前钩子失败；
- `inside_client_get`：已调用get但未返回响应；
- `response_processing`：响应已返回，读取内容/元数据时失败。

诊断包含retries、manager/client当前代次状态、generation、session_active、线程ID、两个请求阶段布尔值；异常文本中的Cookie、Token、Authorization和API Key值会脱敏。

## 29. retries测试

- `retries=0`：在Session创建和`client.get()`前抛出清晰ValueError，服务器请求计数不增加；
- `retries=-1`：同上；
- `retries=1`：本地服务器200或500均只收到一次请求，没有第二次尝试。

## 30. 本轮测试规模

- 新增测试函数：7个；
- 新增参数化场景：86个；
- pytest新增收集项：86项；
- 修复前基线：339 passed；
- 修复后完整收集通过数：425 passed（339+86）。

## 31. 关键反例与测试位置

- A1-A10 `test_a_schema_and_visible_item_binding`：标准Schema、四种name/link关系、父级边界、错误末级、JSON-LD和原有可见格式；
- B11-B18 `test_b_node_metadata_remains_bound`：中间重复、根/空项、position、六级末级验证、data-id和冲突链接；
- C19-C29 `test_c_same_page_decision_is_order_independent`：双向键顺序、随机顺序、临时/最终冲突和商品回退；
- D30-D53 `test_d_registry_and_cross_page_crawler`：完整状态矩阵、前三页、早期商品、并发乱序和跨类目隔离；
- E54-E64 `test_e_session_generation_lifecycle`：同代复用、跨线程、Cookie、finalize、新代次和泄漏；
- F65-F72 `test_f_request_phase_diagnostics`：请求前/中/后标志、retries和脱敏；
- G73-G86 `test_g_production_three_format_round_trip`：真实finalize三格式、冲突回退、动态列、类型及非类目字段不变。

## 32. 专项与相关回归结果

- 本轮新增专项：`86 passed, 38 warnings, 0 failed in 1.65s`
- V2.2.1类目/S1/第二轮：`189 passed, 106 warnings, 0 failed in 4.99s`
- parser/crawler/exporter/WAF/分页/图片/中文导出相关：`183 passed, 2 skipped, 231 warnings, 0 failed in 154.40s`
- 两项skip为既有环境条件，不是本轮失败。

## 33. 两次完整测试

- 第一次：`425 passed, 340 warnings, 0 failed in 164.80s`
- 第二次：`425 passed, 334 warnings, 0 failed in 146.82s`

警告为既有lxml/BeautifulSoup弃用警告；两轮均无失败。

## 34. 三次并发乱序专项

- 第1次：`24 passed, 62 deselected, 0 failed in 0.83s`
- 第2次：`24 passed, 62 deselected, 0 failed in 0.75s`
- 第3次：`24 passed, 62 deselected, 0 failed in 0.75s`

## 35. 三次Session生命周期专项

- 第1次：`11 passed, 75 deselected, 0 failed in 1.18s`
- 第2次：`11 passed, 75 deselected, 0 failed in 1.15s`
- 第3次：`11 passed, 75 deselected, 0 failed in 1.17s`

## 36. 质量检查

- `pip check`：`No broken requirements found.`
- Python编译：24个Git跟踪/本轮新增Python文件全部通过；
- `git diff --check`和`git diff --cached --check`：通过；
- 项目未配置pyproject/setup.cfg/tox/ruff/mypy/flake8/pylint静态检查，未新增大型依赖；
- 测试后残留Python进程：无。

## 37. 三格式自动化写盘

真实调用生产`Exporters.finalize()`：

- 综合冲突/三级/四级/五级fixture：JSON/CSV/XLSX均7条；
- 三页真实Crawler链fixture：JSON/CSV/XLSX均3条；
- 同页临时冲突、跨页最终冲突和第3页晚发现冲突均回退商品自身路径；
- JSON不足层级不输出空键；CSV/XLSX按批次最高五级动态列；
- CSV保留UTF-8 BOM；XLSX价格、评分为float，评价数为int。

## 38. 非类目数据完整性

逐项比较输出前后：商品数量、顺序、PNK、product_id、offer_id、标题、URL、价格、折扣、库存、品牌、标签、链接打标、评分、评价数量、图片路径和其他普通字段保持不变。ProductItem内部英文对象及extra深拷贝比较完全一致；仅一级至五级允许变化。

## 39. 真实HTTP验证配置与功能SHA

- 功能SHA：`95b81ec1de152f2b314404b91c0f54fe0eeec5ad`
- UTC：`2026-08-19T08:24:17.996869+00:00`
- URL：`https://www.emag.ro/boxe/c?ref=bc`
- 纯HTTP、1类目、1页、无图片、无详情页；
- page/category/max_in_flight均1；
- `retries=1`。

## 40. 真实HTTP结果

- 真实请求调用计数：1；
- HTTP状态：511；
- 程序状态：`waf_blocked`；
- 退出码：3；
- `request_call_started=True`；
- `response_received=True`；
- 没有重试或第二次请求；
- 候选卡片0、成功解析0、完成类目0/1；
- Soup创建0、类目证据提取0。

## 41. 真实响应与输出哈希

- 解码正文UTF-8长度：2988 bytes；
- SHA-256：`db7e39c2b256aad21ff2b1475e9b0779c6ee3b78914beb4dce332abc33f6c70c`；
- `products.json`：0条，2 bytes，`4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`；
- `products.csv`：0条，424 bytes，`fa1fb75c7afd796119ce10c3eb4b6f6becd3dacf8228170a47b87cd2ad2a9705`；
- `products.xlsx`：0条，5649 bytes，`799fe050a32165daaa2befa17ab153b6ec5263a9bbf4c1a76c072c5788128bf9`。

哈希口径和全部参数见`REAL_HTTP_EVIDENCE_V2.2.1.md`。

## 42. 真实类目证据与Schema边界

HTTP 511在DOM解析前终止，因此实际采用的类目证据来源和类目路径均为无；没有构建Soup，也无法判断响应是否包含标准父级`a[itemprop=item]`。本报告没有沿用旧200响应、没有伪造面包屑片段，也没有声称页面缺少某个中间层。

## 43. 临时文件与敏感信息

完整响应从未写盘。三个输出、run_summary、errors和WAF诊断位于系统临时目录；记录数量/大小/哈希后逐个删除，临时目录已删除。暂存内容扫描未发现真实Cookie、Authorization、Token、API Key或`.env`；测试中的合成占位值仅验证脱敏逻辑。

## 44. 已知限制

1. 本轮唯一真实请求被HTTP 511 WAF阻断，无法用线上页面核验当前Boxe面包屑结构和商品类目路径；自动化使用标准Schema、动态本地HTTP和生产写盘链完整验证。
2. `final_conflicted`在单次运行中有意保持粘性；当前没有尝试从第三条路径猜测两条冲突最终证据谁正确。
3. 只观察已计划抓取的前三个逻辑页面；若真实站点仅在第4页后改变类目证据，本轮不增加扫描预算或HTTP请求。

## 45. 最终修改文件清单

1. `category_hierarchy.py`
2. `crawler.py`
3. `exporters.py`
4. `tests/test_v221_category_accuracy_round2.py`
5. `tests/test_v221_category_hierarchy.py`
6. `tests/test_v221_category_accuracy_s1.py`
7. `tests/test_v221_category_accuracy_s1_followup.py`
8. `README.md`
9. `CHANGELOG.md`
10. `REAL_HTTP_EVIDENCE_V2.2.1.md`
11. `TASK_REPORT_V2.2.1.md`

## 46. 需要ChatGPT重点复核的最终行号

- `category_hierarchy.py:45`：`CategoryBreadcrumbNode`
- `category_hierarchy.py:162`：`_clean_breadcrumb_nodes()`
- `category_hierarchy.py:283`：`decide_category_evidence()`
- `category_hierarchy.py:335`：`extract_page_category_decision()`
- `category_hierarchy.py:517`：`CategoryLevelRegistry`
- `category_hierarchy.py:589`：`_registry_replacement()`
- `category_hierarchy.py:715`：`_bounded_breadcrumb_link()`
- `category_hierarchy.py:827`：`_visible_breadcrumb_paths()`
- `category_hierarchy.py:919`：`_breadcrumb_items_to_descriptor()`
- `crawler.py:49`：异常敏感值脱敏
- `crawler.py:170`：generation感知的`_get_client()`
- `crawler.py:207`：Session诊断快照
- `crawler.py:229`：关闭Session并递增generation
- `crawler.py:249`：真实请求阶段诊断
- `crawler.py:476`：逻辑页1至3的证据观察
- `exporters.py:78`：注册证据或冲突决策
- `exporters.py:111`：最终写盘边界投影
- `tests/test_v221_category_accuracy_round2.py:122`：Schema A组
- `tests/test_v221_category_accuracy_round2.py:140`：节点对齐B组
- `tests/test_v221_category_accuracy_round2.py:204`：同页顺序C组
- `tests/test_v221_category_accuracy_round2.py:294`：三页生产Crawler fixture
- `tests/test_v221_category_accuracy_round2.py:370`：Registry/Crawler D组
- `tests/test_v221_category_accuracy_round2.py:510`：Session E组
- `tests/test_v221_category_accuracy_round2.py:580`：诊断F组
- `tests/test_v221_category_accuracy_round2.py:629`：三格式生产fixture
- `tests/test_v221_category_accuracy_round2.py:686`：三格式G组

## 47. 完成状态

六项代码问题、自动化测试、生产三格式写盘、双轮全量、三轮并发、三轮Session生命周期、质量检查、功能提交和一次规定的真实HTTP验证均已完成。真实HTTP因HTTP 511 WAF阻断，线上类目DOM核验暂未完成；此状态已如实记录，V2.2.1仍待ChatGPT验收。
