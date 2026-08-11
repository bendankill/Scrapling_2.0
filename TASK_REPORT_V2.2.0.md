# Scrapling 2.0 V2.2.0 字段映射与数据完整性修复报告

## 1. 完成状态

**自动化修复完成，真实 HTTP 验证因 WAF 阻断暂未完成。**

本次只修复两个 S1 问题：`campaign_name` 输出名称错误，以及扩展信息字段翻译时的键冲突与静默覆盖。没有修改抓取、解析、去重、图片、详情页或商品字段值逻辑。

## 2. 当前分支

`V2.2.0`，继续使用现有分支，没有创建新分支、Pull Request 或标签。

## 3. 修复前完整 SHA

`aa65ab9a14308a6eb66e9dc3cbc450f850761e37`

任务开始时本地 HEAD 与 `origin/V2.2.0` 完全一致。

## 4. 修复后完整 SHA

代码、测试、README 首页版本记录和 CHANGELOG 修复提交：

`9e0ef2e5883f6482c08a7cfcf73f78f83556a272`

提交信息：`fix: preserve V2.2.0 export mapping integrity`

本报告在代码 SHA 产生后单独提交，最终包含报告的发布 HEAD 以任务结束时的本地、远程 SHA 核验结果为准。

## 5. 是否已推送

是。代码提交已通过普通 `git push origin V2.2.0` 快进推送，没有强制推送。报告提交完成后将再次正常推送。

## 6. 本地与远程 SHA 是否一致

代码提交推送后，本地 HEAD 与 `origin/V2.2.0` 均为：

`9e0ef2e5883f6482c08a7cfcf73f78f83556a272`

报告提交后再次执行 `git rev-parse HEAD` 和 `git ls-remote origin refs/heads/V2.2.0` 进行最终核验。

## 7. config/categories.txt 是否未修改

Codex 没有修改、恢复、暂存或提交 `config/categories.txt`。老大在任务开始前已有未提交配置，任务开始和修复完成时的文件哈希均为：

`01f70b7d8a1f47e86ee3d0d070f5cce88ef93958`

用户文件 `config/categories - 副本.txt1` 同样保持未跟踪状态，没有加入提交。真实 HTTP 验证使用 `output/` 下临时配置，验证后已删除。

## 8. campaign_name 修复前后映射

- 修复前：`campaign_name -> 活动名称`
- 修复后：`campaign_name -> 链接打标`
- `badges -> 商品标签` 保持独立，不合并、不覆盖。
- 输入 `campaign_name="Top Favorite"` 时，JSON、CSV、XLSX 回读值均为 `链接打标="Top Favorite"`。
- 三种格式均不再出现 `活动名称`。
- 顶层产品映射仍为原有 34 项，没有增加未授权商品字段。

## 9. 扩展信息键冲突根因

旧实现把 `favorite_data` 内部字段和 `extra` 顶层字段放在同一张通用映射表中，并在 `translate_extra()` 中直接按翻译后的键写入普通字典。由此产生两个问题：

1. 顶层意外出现的 `category_trail` 被错误套用 `favorite_data.category_trail` 的规则，与顶层 `data-category-trail` 同时变成 `类目路径`。
2. 翻译目标键已被其他原键或现有中文键占用时，后写值直接覆盖先写值，字段数和数据值静默减少。

修复前的只读复现确认：场景 A 丢失 `A/B`，场景 B 丢失 `RAW`，场景 C 丢失 `RawName`。

## 10. 最终冲突处理规则

映射被严格拆为两个数据层：

### favorite_data 内部专用映射（9 项）

`pnk`、`productid`、`offerid`、`has_family`、`is_family`、`product_name`、`currency`、`price`、`category_trail`。

### extra 顶层专用映射（7 项）

`favorite_data`、`availability_id`、`data-category-id`、`data-department-id`、`data-category-trail`、`data-category-name`、`data-referrer`。

安全转换函数先计算全部目标键，再识别冲突组：

- 无冲突时正常翻译。
- 多个原键指向同一目标键时，冲突组全部保留原键名。
- 翻译目标与输入中已有键重名时，相关原键全部保留。
- 不使用 `_2`、`duplicate` 等自创后缀。
- 保持输入顺序、字段值、值类型和同层字段数量。
- 生成新字典，不修改传入对象。
- 最终字段数不一致时显式抛出异常，禁止静默丢数据。

## 11. 三个冲突案例修复前后结果

### 场景 A

输入：`{"category_trail":"A/B","data-category-trail":"C/D"}`

- 修复前：`{"类目路径":"C/D"}`，2 个字段变 1 个。
- 修复后：`{"category_trail":"A/B","类目路径":"C/D"}`，2 个字段仍为 2 个。

### 场景 B

输入：`favorite_data={"pnk":"RAW","PNK码":"EXISTING"}`

- 修复前：`{"PNK码":"EXISTING"}`，`RAW` 被覆盖。
- 修复后：`{"pnk":"RAW","PNK码":"EXISTING"}`，两个键和值均保留。

### 场景 C

输入：`{"data-category-name":"RawName","类目名称":"ExistingName"}`

- 修复前：`{"类目名称":"ExistingName"}`，`RawName` 被覆盖。
- 修复后：`{"data-category-name":"RawName","类目名称":"ExistingName"}`，两个键和值均保留。

### 不同层级正常场景 D

`extra.favorite_data.category_trail` 输出为 `扩展信息.收藏数据.类目路径`，顶层 `data-category-trail` 输出为 `扩展信息.类目路径`；两个字段位于不同字典层级，均正常翻译。

## 12. 修改文件清单

- `output_schema.py`：修正“链接打标”，拆分两层映射，新增通用无损冲突保护。
- `tests/test_v220_chinese_exports.py`：新增 6 个回归用例，并强化三格式字段和值断言。
- `README.md`：同步更新 GitHub 首页 V2.2.0 版本日志。
- `CHANGELOG.md`：记录 2026-08-11 数据完整性修复和测试数量。
- `TASK_REPORT_V2.2.0.md`：本报告。

没有修改 crawler、parser、exporters、models、图片下载或正式配置。

## 13. 新增测试名称和关键断言

修复前 V2.2.0 专项为 26 个用例，本次新增 6 个，总计 32 个：

1. `test_scenario_a_top_level_category_trails_do_not_collide`：两个路径值和 2 个字段全部保留。
2. `test_scenario_b_existing_chinese_favorite_key_preserves_both_original_keys`：`RAW` 与 `EXISTING` 均保留。
3. `test_scenario_c_existing_chinese_top_key_preserves_both_original_keys`：`RawName` 与 `ExistingName` 均保留。
4. `test_scenario_d_same_label_in_different_layers_translates_normally`：两个层级分别正常翻译。
5. `test_extra_translation_preserves_order_types_counts_and_input`：顺序、类型、字段数不变，输入深度比较不变。
6. `test_three_formats_round_trip_campaign_and_collision_values`：三格式实际写入回读“Top Favorite”，旧标签不存在，扩展信息无丢键。

现有映射、JSON、CSV、XLSX 用例同时增加：

- `campaign_name -> 链接打标` 精确断言。
- `badges -> 商品标签` 独立值断言。
- 三种格式 `活动名称` 不存在断言。
- 专用映射严格为 9 项和 7 项。
- 顶层意外 `pnk` 保持原名。

没有删除测试、使用空断言或降低旧断言强度。

## 14. 专项测试结果

`tests/test_v220_chinese_exports.py`：

`32 passed in 2.20s`

## 15. 两次完整测试结果

- 第 1 次：`236 passed, 233 warnings, 0 failed in 127.20s`
- 第 2 次：`236 passed, 228 warnings, 0 failed in 115.23s`

通过数由修复前 230 增至 236。警告均为 lxml/BeautifulSoup 的 `strip_cdata` 依赖弃用提示，不是测试失败。

## 16. pip check、编译检查和 git diff --check

- `pip check`：`No broken requirements found.`
- `python -m py_compile output_schema.py tests/test_v220_chinese_exports.py`：退出码 0。
- 额外 AST 语法解析：2 个修改的 Python 文件通过。
- `git diff --check -- output_schema.py tests/test_v220_chinese_exports.py README.md CHANGELOG.md`：退出码 0。
- 全工作区检查只显示老大已有 `config/categories.txt` 的末尾空行提示；该文件哈希未变，未进入提交。

## 17. JSON、CSV、XLSX 实际写盘记录数量

在被 Git 忽略的 `output/s1_actual_write/` 中创建两条 ProductItem，实际调用生产 `Exporters.finalize()`，随后分别重新读取三个文件：

- products.json：2 条
- products.csv：2 条
- products.xlsx：2 条

额外实际确认：

- CSV 以 UTF-8 BOM `EF BB BF` 开头。
- XLSX 的价格、评分为浮点数，评价数量为整数。
- 整批最高三级，CSV/XLSX 动态列包含到 `三级类`。
- JSON 的 `扩展信息` 是对象。
- CSV/XLSX 的 `扩展信息` 是可由 `json.loads` 完整还原的字符串。

## 18. 三种格式“链接打标”的实际值

- JSON：`Top Favorite`
- CSV：`Top Favorite`
- XLSX：`Top Favorite`

三者完全一致，且三个输出中均不存在 `活动名称`。

## 19. 扩展信息转换前后的字段数量

实际写盘冲突样本：

- extra 顶层：转换前 5，转换后 5。
- favorite_data 层：转换前 2，转换后 2。
- 场景 A：转换前 2，转换后 2。
- 场景 B：转换前 2，转换后 2。
- 场景 C：转换前 2，转换后 2。

所有原值、值类型、嵌套结构和顺序均由专项测试或实际三格式回读确认。

## 20. 真实 HTTP 验证结果

唯一一次请求：

- 北京时间：`2026-08-11T11:27:04+08:00`
- URL：`https://www.emag.ro/boxe/c?ref=bc`
- 条件：纯 HTTP、1 页、无图片，类目/页面/总请求并发均为 1。
- HTTP 状态：`511`
- 错误证据：`HTTP 511; captcha in body; WAF in body`
- 程序状态：`waf_blocked`
- 退出码：`3`
- 商品：`0`
- 完成类目：`0/1`
- JSON/CSV/XLSX：`0/0/0`
- 是否重试：否，首次 511 后立即停止。
- 临时配置：已删除。

**真实 HTTP 验证因 WAF 阻断暂未完成。** 不能用自动化 fixture 冒充线上商品验证。

## 21. 已知限制

- HTTP 511 阻止了本次线上商品回读，因此无法确认当前 Boxe 页面上 PNK `DR8D26BBM` 是否仍存在。
- 冲突时按要求保留原字段名，因此单个冲突字典可能同时包含英文键和已有中文键；这是防止数据丢失的明确规则，不是重复翻译。
- CSV 没有原生数值单元类型；数值按 CSV 文本规范输出，JSON 和 XLSX 保持原生数值。
- 老大的 `config/categories.txt` 修改和未跟踪备份继续保留，所以最终 `git status --short` 会显示它们。

## 22. 需要 ChatGPT 重点复核的文件、函数和准确行号

- `output_schema.py:12`：`PRODUCT_OUTPUT_FIELD_MAP`，检查 `campaign_name -> 链接打标` 与 `badges -> 商品标签`。
- `output_schema.py:49`：`FAVORITE_DATA_OUTPUT_FIELD_MAP` 9 项专用映射。
- `output_schema.py:61`：`EXTRA_TOP_LEVEL_OUTPUT_FIELD_MAP` 7 项顶层映射。
- `output_schema.py:116`：`translate_extra()` 分层转换，不修改输入。
- `output_schema.py:133`：`_translate_mapping_without_loss()` 冲突组识别、原键回退和字段数断言。
- `output_schema.py:168`：`product_to_output_dict()` 唯一产品导出边界。
- `tests/test_v220_chinese_exports.py:104`：场景 A。
- `tests/test_v220_chinese_exports.py:110`：场景 B。
- `tests/test_v220_chinese_exports.py:118`：场景 C。
- `tests/test_v220_chinese_exports.py:127`：正常分层场景 D。
- `tests/test_v220_chinese_exports.py:137`：顺序、类型、数量和输入不变。
- `tests/test_v220_chinese_exports.py:292`：JSON/CSV/XLSX 实际写入回读。
- `README.md:9`：GitHub 首页 V2.2.0 版本日志。
- `CHANGELOG.md:5`：本次数据完整性修复记录。

## 分支与安全核验

代码推送后再次读取远程分支：

- `V2.1.1`：`a94cc5c0a0dbb14011018e856c827f7b69eda088`
- `V2.1.2`：`ff6963a64aa09b65ab489d0903f9cf1806964b88`
- `V2.1.3`：`4306b1fb1176dde258f892cc803c52550fc241a8`
- `V2.1.4`：`6f28db70231a1a66e3c931944b46061392b83a12`
- `main`：`75500e45d244b61854de553eedbaad21e9d3005d`

以上分支均未变化。本任务没有提交 output、图片、日志、缓存、虚拟环境、临时配置、Cookie、Token、Authorization 或真实响应原文，也没有执行重置、清理、强制推送或详情页请求。
