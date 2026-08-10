# Scrapling 2.0 V2.2.0 任务报告

## 1. 完成状态

**自动化修复完成，真实HTTP验证因WAF阻断暂未完成。**

产品输出字段中文化、扩展信息明确字段中文化、一级至五级类目拆分、JSON/CSV/XLSX动态列和内部逻辑隔离均已实现，并通过连续两次完整自动化测试。唯一一次真实 eMAG 低频请求返回 HTTP 511，程序按要求立即停止，没有重试，因此不能声称真实商品抓取验证完成。

## 2. 当前分支

`V2.2.0`

该分支已设置跟踪 `origin/V2.2.0`。

## 3. 创建分支时 V2.1.4 完整 SHA

`6f28db70231a1a66e3c931944b46061392b83a12`

创建前本地 `V2.1.4`、`origin/V2.1.4` 和 `git ls-remote` 完全一致。远程和本地均不存在来源不明的 `V2.2.0`，因此从该 SHA 新建分支并首次正常推送。

## 4. V2.2.0 修复后完整 SHA

代码、测试和版本文档提交：

`6de80e086b341c59d9a6c5cdecc237ab9f36df0c`

提交信息：`feat: add Chinese export fields and category hierarchy for V2.2.0`

本报告在代码 SHA 产生后单独提交，以便准确记录代码修复 SHA。最终包含本报告的远程 HEAD SHA 以报告提交后的 `git rev-parse HEAD` 和 `git ls-remote` 核验为准。

## 5. 是否已推送

是。代码提交已正常快进推送到 `origin/V2.2.0`，没有强制推送，没有创建 Pull Request，没有合并其他分支。

## 6. 本地与远程 SHA 是否一致

代码提交推送后本地和远程均为：

`6de80e086b341c59d9a6c5cdecc237ab9f36df0c`

任务报告提交后将再次核验最终 HEAD。

## 7. 修改文件清单

- `output_schema.py`：唯一产品字段映射、扩展信息映射、类目来源选择、五级拆分和动态列。
- `exporters.py`：只在最终写文件边界转换 JSON/CSV/XLSX，内部商品字典继续使用英文。
- `models.py`：旧列接口改为委托唯一输出模式，不再维护第二套字段翻译。
- `tests/test_v220_chinese_exports.py`：V2.2.0 专项测试。
- `tests/fixtures/v220_product_sample.json`：脱敏最小商品 fixture。
- `tests/test_v214_codex_takeover.py`：将三处产品 JSON 旧英文键断言更新为 V2.2.0 中文键，断言强度未降低。
- `crawler.py`：`run_summary.json` 版本号更新为 2.2.0；内部字段及统计结构不变。
- `main.py`：CLI 显示版本更新为 V2.2.0。
- `VERSION`：更新为 `2.2.0`。
- `README.md`：更新 V2.2.0 标题与分支版本记录。
- `CHANGELOG.md`：新增 V2.2.0 变更和验证范围。
- `TASK_REPORT_V2.2.0.md`：本报告。

没有修改 parser、图片下载或抓取请求逻辑，没有增加详情页请求。

## 8. 完整顶层字段映射表

唯一映射表共 `34` 项：

| 内部字段 | 产品输出字段 |
|---|---|
| `category_name` | `类目名称` |
| `category_url` | `类目链接` |
| `source_page_url` | `来源页面链接` |
| `page_number` | `页码` |
| `position_in_page` | `页内位置` |
| `product_id` | `产品ID` |
| `pnk` | `PNK码` |
| `sku` | `SKU` |
| `offer_id` | `报价ID` |
| `title` | `产品标题` |
| `product_url` | `产品链接` |
| `price_current` | `前端价格` |
| `price_old` | `PRP原价` |
| `price_promo` | `活动价格` |
| `price_current_raw` | `前端价格原文` |
| `price_old_raw` | `PRP原价原文` |
| `price_promo_raw` | `活动价格原文` |
| `discount_percent` | `前端折扣` |
| `currency` | `货币` |
| `availability` | `库存状态` |
| `stock_text` | `库存原文` |
| `seller` | `卖家` |
| `brand` | `品牌` |
| `badges` | `商品标签` |
| `campaign_name` | `活动名称` |
| `shipping_text` | `配送信息` |
| `rating` | `评论分数` |
| `review_count` | `评价数量` |
| `main_image_url` | `产品图片` |
| `main_image_local_path` | `本地图片路径` |
| `collected_at` | `抓取时间` |
| `http_status` | `HTTP状态码` |
| `parse_source` | `解析来源` |
| `extra` | `扩展信息` |

英文键不会与对应中文键同时出现在产品输出中。

## 9. 扩展信息字段映射表

明确映射共 `16` 项：

| 内部键 | 输出键 |
|---|---|
| `favorite_data` | `收藏数据` |
| `pnk` | `PNK码` |
| `productid` | `产品ID` |
| `offerid` | `报价ID` |
| `has_family` | `是否存在系列商品` |
| `is_family` | `是否为系列商品` |
| `product_name` | `产品名称` |
| `currency` | `货币` |
| `price` | `价格` |
| `category_trail` | `类目路径` |
| `availability_id` | `库存状态ID` |
| `data-category-id` | `类目ID` |
| `data-department-id` | `部门ID` |
| `data-category-trail` | `类目路径` |
| `data-category-name` | `类目名称` |
| `data-referrer` | `来源路径` |

所有值和数据类型保持原样；只生成新字典，不修改内部原始 extra。

## 10. 保持原名的未确认字段清单

- `options_modal`
- `scm_super_category`
- `data-has-unfair-price`
- 所有未列入 16 项明确映射表的其他技术字段
- `scm_super_category` 内部的 `id`、`name`

专项测试验证上述字段的名称、嵌套结构和值均保持原样。

## 11. 类目层级数据来源和优先级

当前实现只使用商品对象已经抓到的真实 extra 证据，不访问详情页，也不从 `category_name` 猜完整路径：

1. `extra.favorite_data.category_trail`
2. `extra["data-category-trail"]`

当两个来源同时存在时：

- 先要求路径真实末级与当前商品 `category_name` 一致；
- 在一致的候选中选择完整层级数更多的路径；
- 层级数相同时选择 `favorite_data.category_trail`；
- 从不拼接两个路径；
- 所有来源都不一致时不输出任何层级字段。

没有把页面 breadcrumb 新增到 ProductItem，因为当前 ProductItem 没有该字段，强行增加会违反本次“保持现有数据和字段范围”的要求。

## 12. 一级至五级拆分规则

- 只按 `/` 拆分。
- 每段去除前后空白。
- 连续 `/` 产生的空段被忽略，不制造空层级。
- 逗号、连字符、`&` 和罗马尼亚语特殊字符不作为分隔符。
- 第 1—5 段依次输出 `一级类`、`二级类`、`三级类`、`四级类`、`五级类`。
- 缺少路径时不使用 `category_name` 补造一级类。

示例路径严格拆分为：

- `一级类`：`TV, Audio-Video & Foto`
- `二级类`：`Audio HI-FI & Profesionale`
- `三级类`：`Boxe`

没有补充 `Audio Hi-Fi` 或任何参考数据中的中间层级。

## 13. 超过五级的处理规则

先读取完整路径并用真实最后一级校验当前商品类目，然后只输出前五段。第六级及后续段不生成新顶层字段，原始完整路径继续保存在翻译后的 `扩展信息.收藏数据.类目路径` 或 `扩展信息.类目路径` 中。

## 14. JSON 缺失层级处理方式

每条商品只包含实际存在的层级键。三级商品不会出现空的 `四级类` 和 `五级类`；路径缺失或无法确认时，一级至五级都不输出。

## 15. CSV/XLSX 动态列处理方式

- 先转换整批商品并计算实际出现的最高层级。
- 最高三级时只加入一级至三级列。
- 最高四级时加入一级至四级列。
- 最高五级或原路径超过五级时加入一级至五级列。
- 层级不足的商品在后续固定列中留空。
- 没有商品或整批没有真实路径时不增加空的类目层级列。
- JSON、CSV、XLSX 的普通字段顺序均来自同一份映射表。

## 16. 字段值保持不变的验证结果

专项 fixture 对 33 个非 extra 原字段逐项执行值相等和 Python 类型完全相等断言，并另外验证翻译后的 extra 值：

- PNK、产品 ID、Offer ID、标题、URL、三个价格、折扣、货币、库存、卖家、品牌、标签、配送、评分、评价数量、图片 URL、图片路径、抓取时间、HTTP 状态和解析来源均不变。
- `brand=""` 仍为空字符串，没有从标题中的 JRH 推断。
- `price_current=529.97` 仍为浮点数 `529.97`。
- `review_count=4` 仍为整数 `4`。
- `price_promo=null` 在 JSON 中仍为 `null`，在 XLSX 中为空单元格。
- 罗马尼亚语标题 `Boxă ...` 和 `în stoc` 保持 UTF-8 原文。
- 布尔值 `false` 仍为布尔值。
- 导出前后的内部英文商品列表深度比较完全相等。
- 商品排序继续使用内部 `category_name/page_number/position_in_page`，验证顺序为 `A`、`Z`，没有因翻译改变。
- run_summary、errors.csv、去重和图片路径回填仍使用内部英文键。

## 17. 修复前后记录数量对比

自动化三格式一致性用例输入 `2` 条 ProductItem：

- 转换前内部记录：`2`
- products.json：`2`
- products.csv：`2`
- products.xlsx：`2`

记录数量没有因为字段映射增加或丢失。真实请求因 HTTP 511 在商品页返回前被阻断，因此真实记录为 `0`，不能用于证明真实商品值映射。

## 18. JSON/CSV/XLSX 数量对比

- 自动化真实写盘测试：`2/2/2`，一致。
- WAF 真实请求输出：`0/0/0`，一致。

WAF 空输出的 CSV 使用 UTF-8 BOM；原始开头字节为 `EF BB BF`，首字段 UTF-8 字节对应 `类目名称`。XLSX 首字段 Unicode 码点同样对应 `类目名称`。PowerShell 回显乱码是终端代码页问题，不是文件乱码。

## 19. 新增测试名称和断言

新增 `21` 个测试函数、共 `26` 个 pytest 测试用例（一级至六级路径使用 6 组参数化用例）：

1. `test_complete_mapping_has_exact_34_fields`：完整 34 项映射和指定名称。
2. `test_sample_values_and_types_are_unchanged`：逐字段值和类型不变、英文键消失。
3. `test_output_order_inserts_real_levels_after_source`：字段顺序和类目插入位置。
4. `test_extra_mapping_preserves_unknown_values_and_structure`：明确键翻译、未知结构不变。
5. `test_extra_mapping_table_has_exact_confirmed_fields`：扩展映射严格 16 项。
6. `test_confirmed_top_level_extra_key_is_also_translated`：顶层明确 extra 键转换。
7. `test_product_item_legacy_column_api_uses_unique_schema`：旧列接口委托唯一模式。
8. `test_three_levels_split_only_on_slash`：示例三级路径精确拆分，不按逗号拆分。
9. `test_one_to_six_level_paths[1..6]`：一级至六级，六级只输出前五级。
10. `test_whitespace_empty_segments_comma_and_romanian_characters`：空白、连续斜杠、逗号、特殊字符。
11. `test_missing_path_never_uses_category_name_as_guess`：缺失路径不猜层级。
12. `test_more_complete_consistent_source_wins_without_concatenation`：选择更完整一致来源，不拼接。
13. `test_mismatched_favorite_source_falls_back_to_consistent_source`：不一致高优先级来源被拒绝。
14. `test_equal_length_consistent_sources_use_favorite_priority`：同层级数按稳定来源优先。
15. `test_all_mismatched_sources_produce_no_guessed_levels`：全部不一致时不猜测。
16. `test_json_chinese_keys_dynamic_levels_and_whitelist`：中文键、类型、动态层级和禁止字段。
17. `test_csv_utf8_bom_dynamic_columns_empty_cells_and_count`：BOM、动态列、空单元格、值和行数。
18. `test_xlsx_chinese_dynamic_columns_numeric_and_empty_values`：中文表头、五级列、数值和空值类型。
19. `test_three_format_counts_order_and_core_values_match`：三格式数量、顺序和核心值一致。
20. `test_product_item_and_exporter_internal_fields_stay_english`：内部英文字段和图片路径回填不变。
21. `test_run_summary_and_errors_schema_are_not_product_translated`：run_summary/errors.csv 不被产品映射影响。

没有删除旧测试；三处旧 JSON 断言仅将 `title` 改为新规范的 `产品标题`，期望值和业务断言均未放宽。

## 20. 完整测试连续两次结果

测试环境：Python `3.12.13`，pytest 临时目录位于被 Git 忽略的 `output/`。

- 专项测试：`26 passed in 0.80s`
- 相关回归：`116 passed, 2 skipped, 0 failed in 100.98s`
- 完整测试第 1 次：`230 passed, 228 warnings, 0 failed in 115.10s`
- 完整测试第 2 次：`230 passed, 228 warnings, 0 failed in 115.39s`

警告均为项目依赖 lxml/BeautifulSoup 的 `strip_cdata` 弃用提示。完整套件覆盖现有单元、集成、HTTP/WAF、分页、图片、导出和统计测试。

## 21. pip check 结果

`No broken requirements found.`

Python 语法编译检查和 `git diff --check` 同样通过。

## 22. 真实 eMAG 测试结果

临时配置仅包含：

`https://www.emag.ro/boxe/c?ref=bc`

执行条件：纯 HTTP、1 页、无图片、类目/页面/总请求并发均为 1。正式 `config/categories.txt` 没有用于本次请求，也没有被测试脚本覆盖。

- 请求开始：`2026-08-10T13:38:33.737751+00:00`（上海时间 21:38:33）
- HTTP 状态：`511`
- WAF 证据：`HTTP 511; captcha in body; WAF in body`
- 程序状态：`waf_blocked`
- 退出码：`3`
- 请求页：`1`
- 成功页：`0`
- 完成类目：`0/1`
- 抓取商品：`0`
- JSON/CSV/XLSX：`0/0/0`
- 是否重试：否；首次 511 后立即停止
- 示例 PNK `DR8D26BBM`：页面被 WAF 阻断，无法检查是否仍在当前页
- 临时配置：验证后已删除

## 23. 是否抓到大于 0 条真实商品

否。原因是唯一一次真实请求返回 HTTP 511 WAF，而不是解析或字段映射失败。按任务要求没有连续重试，因此真实验证状态必须保持“暂未完成”。

## 24. 是否修改 config/categories.txt

Codex 本次没有修改、暂存或提交该文件。

任务开始时该文件已有老大确认的测试配置修改，内容哈希为：

`7732ac1747d3f708630105b8eb1cdb7513c2e74d`

创建分支前使用可恢复 stash 临时保存，创建后立即恢复；实现、测试、真实请求和提交后的哈希仍为同一值。该文件继续以未暂存状态保留在老大工作区，不进入 V2.2.0 提交。

## 25. V2.1.4 及其他分支是否保持不变

保持不变：

- `V2.1.1`：`a94cc5c0a0dbb14011018e856c827f7b69eda088`
- `V2.1.2`：`ff6963a64aa09b65ab489d0903f9cf1806964b88`
- `V2.1.3`：`4306b1fb1176dde258f892cc803c52550fc241a8`
- `V2.1.4`：`6f28db70231a1a66e3c931944b46061392b83a12`
- `main`：`75500e45d244b61854de553eedbaad21e9d3005d`

本任务的当前明确要求是其他分支 SHA 不变，因此只更新了 `V2.2.0` 分支中的 README/CHANGELOG 版本记录，没有改动默认分支 `main`。

## 26. 已知限制

- 真实 eMAG 验证被 HTTP 511 阻断，当前无法证明线上 `boxe` 商品的实际字段和值；不能把自动化 fixture 结果冒充真实验证。
- 当前类目层级只使用 ProductItem 已有的两个 extra 路径来源。若真实页面只在 breadcrumb 提供路径而商品 extra 没有路径，本版本宁可不输出层级，也不会新增抓取字段或猜测。
- 真实路径末级与 URL 生成的 `category_name` 无法一致确认时不输出层级，以避免错误类目。
- CSV 本身没有原生数值类型；它按 CSV 标准输出数值文本，JSON 和 XLSX 保持原生数值类型。
- `config/categories.txt` 保留老大的未提交测试配置，因此最终 `git status --short` 会如实显示该文件，但它不属于 V2.2.0 提交。

## 27. 需要 ChatGPT 重点复核的文件、函数和行号

- `output_schema.py:12`：`PRODUCT_OUTPUT_FIELD_MAP` 34 项唯一映射。
- `output_schema.py:49`：`EXTRA_OUTPUT_FIELD_MAP` 16 项明确映射。
- `output_schema.py:82`：`split_category_path()` 只按 `/` 拆分和五级截断。
- `output_schema.py:94`：`extract_category_levels()` 完整度、末级一致性和来源优先级。
- `output_schema.py:119`：`translate_extra()` 未确认技术结构保持。
- `output_schema.py:137`：`product_to_output_dict()` 唯一导出边界转换和字段顺序。
- `output_schema.py:160`：`output_columns()` CSV/XLSX 动态列。
- `output_schema.py:177`：`max_category_level()` 批次最高层级。
- `exporters.py:63`：CSV/XLSX 中文缓冲区。
- `exporters.py:68`：JSON 中文缓冲区。
- `exporters.py:78`：UTF-8 BOM CSV 写入。
- `exporters.py:96`：XLSX 中文表头、动态列和数值类型。
- `models.py:86`：旧列接口统一委托输出模式。
- `tests/test_v220_chinese_exports.py:43` 至 `275`：21 个测试函数、26 个测试用例。
- `tests/fixtures/v220_product_sample.json:1`：脱敏最小数据和类型基线。

## 最终安全核验

- 没有提交 output、真实请求响应、日志、图片、虚拟环境、缓存或临时配置。
- 没有提交 Cookie、Authorization、Token、账号信息或真实用户凭据。
- 没有执行 `git reset --hard`、`git checkout --`、`git clean -fd`、强制删除或强制推送。
- 没有访问商品详情页，没有翻译商品内容，没有推断品牌，没有新增未经授权字段。
