# Scrapling 2.0 V2.1.4 Codex 接管与修复报告

## 结论

本次严格按“先保存接管基线、再诊断修复”的顺序执行。接管前状态已先正常推送到远程 `V2.1.4`，随后完成 HTTP 200 页面分类、WAF 可见性、商品卡片解析、逐卡错误记录和异常诊断修复。专项测试、包含单元与集成测试的完整测试连续两次均为零失败；三次纯 HTTP 低频真实验证均成功，`Tricouri Sport` 每次解析 60/60 个商品，三类目验证均继续执行了后续类目。

## 1. Codex 接管时间

- 接管保存提交时间：`2026-08-07T16:30:55+08:00`（Asia/Shanghai）
- 修复代码提交时间：`2026-08-07T17:02:42+08:00`（Asia/Shanghai）

## 2. 接管前本地 SHA

`e4430564012f15c4b69c2db9ea4be3317a0db94c`

## 3. 接管前远程 SHA

`e4430564012f15c4b69c2db9ea4be3317a0db94c`

接管检查时本地与 `origin/V2.1.4` 一致，没有分叉。

## 4. CODEX_TAKEOVER_BASE_SHA

`627ad849d2e64fe5843c51dca46c048d87353c97`

## 5. 当前版本第一次保存提交 SHA

`627ad849d2e64fe5843c51dca46c048d87353c97`

提交信息：`chore: preserve V2.1.4 state before Codex takeover`

该提交仅保存接管前已有的 `AGENTS.md` 和 `config/categories.txt` 修改，并已在任何第二阶段代码修改前正常推送到远程 `V2.1.4`。没有使用强制推送。

## 6. 修复前完整 SHA

`627ad849d2e64fe5843c51dca46c048d87353c97`

## 7. 修复后完整 SHA

代码与测试修复提交：`ce49aaa53dd8f4cde456132970051e6950ef5311`

提交信息：`fix: resolve V2.1.4 HTTP200 classification and WAF false positives`

说明：任务报告在代码提交 SHA 产生后单独提交，因此报告可以准确记录代码修复 SHA；最终包含本报告的远程 HEAD SHA 以推送后 `git rev-parse HEAD` 和 `git ls-remote` 的核验结果为准。

## 8. 当前分支

`V2.1.4`

未切换或创建其他分支，未合并到 `main`。

## 9. 当前版本是否先成功推送

是。第一阶段保存提交 `627ad849d2e64fe5843c51dca46c048d87353c97` 已先推送，并确认当时本地 HEAD、`origin/V2.1.4` 和 `git ls-remote` 三者完全一致，之后才开始第二阶段。

## 10. Tricouri Sport 真实根因

真实页面是正常商品分类页，不是验证码、WAF、空类目、类目下架或重定向页。

低频诊断请求得到：

- HTTP 状态：`200`
- 原始 URL 与最终 URL：`https://www.emag.ro/tricouri-sport/c?ref=search_menu_category`
- 重定向链：空
- Content-Type：`text/html; charset=UTF-8`
- 响应长度：`886497` 字节
- 页面标题：`Tricouri sport barbati. Căutarea nu se oprește niciodată - eMAG.ro`
- HTML MD5：`c501770be941a9b5d1b72fff62d5c1b6`
- 真实商品卡片：`60`
- 下一页：`https://www.emag.ro/tricouri-sport/p2/c`
- 总页数：`100`
- WAF、空类目、不可用类目证据：均无

真实商品根节点使用 `.card-item.card-fashion.js-product-data.js-card-clickable`，旧选择器要求 `.card-standard`，因此把 60 张真实商品卡片识别为 0 张。该页面的商品 ID 还位于收藏按钮 JSON 的 `productid` 中，而不是卡片根节点的 `data-product-id`；旧备用选择器同样无法命中。

## 11. HTTP 200 页面实际类型

正常商品分类页。修复后分类为商品页，候选卡片 `60`，成功解析 `60`，失败 `0`。

## 12. 原来退出码 2 的原因

旧商品选择器漏掉 `.card-fashion` 页面，导致：

1. 页面实际有商品，但候选卡片计数为 0；
2. 页面又没有空类目、不可用类目或 WAF 证据；
3. 最终落入 `UNKNOWN_HTTP200_PAGE`；
4. 状态成为 `network_error`，退出码为 `2`，当前类目不完成并停止后续类目。

## 13. 隐藏父节点误判根因

旧可见性规则只检查目标元素自身，没有沿全部祖先节点检查 `hidden`、`display:none`、`visibility:hidden` 和 `aria-hidden=true`。因此隐藏父容器中的验证码子节点会被误当成可见验证码正文。

修复后元素自身或任意祖先命中上述任一条件均视为隐藏；`aria-hidden` 的值不区分大小写。

## 14. script 内容误判根因

旧正文提取会把 `script` 中的字符串并入页面文本，导致 `Please verify you are human` 等预加载文案被当成可见验证码正文。

修复后可见正文排除 `script`、`style`、`noscript`、`template`、`head`、隐藏元素和隐藏祖先的所有后代；AWS WAF 预加载脚本单独存在不会判定为 WAF。

## 15. 实际修改方案

- 用 `.card-item.js-product-data` 作为稳定商品卡片入口，并保留 `data-product-id` 备用入口。
- 从收藏按钮 JSON 补取 `productid` 和 `offerid`，兼容真实 `.card-fashion` 结构。
- 每个 HTTP 200 页面只构建一次 BeautifulSoup DOM，并在解析、WAF、空类目和诊断之间复用。
- 403/429/511 在构建 DOM 前直接判定 WAF。
- HTTP 200 依次执行强验证码标题、真实卡片解析、可见验证码 UI/正文、明确空类目/不可用类目、未知页面分类。
- WAF 正文判断排除商品卡片文本，商品标题中的普通 `access denied` 不会单独终止任务。
- 未知 HTTP 200 页面仅在异常时保存 HTML 和 JSON，JSON 包含 URL、重定向链、响应元数据、标题、哈希、卡片/解析计数、证据及致命错误详情。
- 全部卡片解析失败时，先把每张卡片的位置、商品键、URL、原始异常类型和详情写入 `errors.csv`，再追加页面级 `ALL_PARSE_FAILED`。
- 明确空类目或不可用类目按成功完成当前类目并继续；未知 HTTP 200 保持退出码 2；HTTP 200 WAF 保持退出码 3，并保留此前数据。

## 16. 删除的重复函数

- 删除 `utils._page_has_valid_product_soup()`。
- 删除 `crawler._check_body_waf()`，所有模块统一调用 `utils.detect_waf_block()` 及其同一套可见性规则。

## 17. 修改文件清单

- `crawler.py`：唯一 HTTP 200 分类流程、响应元数据、异常诊断、逐卡错误写入和调度状态。
- `parser.py`：真实商品卡片选择器和收藏 JSON ID 回退。
- `utils.py`：唯一 WAF 判断、祖先可见性和正文过滤。
- `tests/fixtures/tricouri_sport_fashion_minimal.html`：根据真实 DOM 脱敏后的最小 fixture。
- `tests/test_v214_codex_takeover.py`：19 个真实断言的专项测试。
- `TASK_REPORT_V2.1.4_CODEX_TAKEOVER_AND_FIX.md`：本报告。

没有修改与本任务无关的抓取、导出或图片下载模块。

## 18. 新增测试名称和核心断言

共新增 `19` 个测试函数，没有删除或放宽旧测试：

1. `test_hidden_parent_captcha_does_not_block_product`：隐藏父节点验证码不判 WAF，商品输出，退出码 0。
2. `test_script_captcha_string_does_not_block_product`：script 验证码字符串不判 WAF，商品输出。
3. `test_hidden_parent_makes_unmarked_child_invisible`：子节点自身无隐藏属性仍继承祖先隐藏。
4. `test_aria_hidden_captcha_does_not_block_product`：`aria-hidden=true` 验证码不判 WAF。
5. `test_visibility_hidden_captcha_does_not_block_product`：`visibility:hidden` 验证码不判 WAF。
6. `test_visible_captcha_title_is_waf`：可见验证码标题判 WAF，退出码 3。
7. `test_visible_captcha_ui_and_body_is_waf`：可见验证码 UI/正文判 WAF，退出码 3。
8. `test_aws_waf_preload_script_does_not_block_product`：AWS WAF 预加载脚本不妨碍商品解析。
9. `test_access_denied_product_title_does_not_block`：商品标题中的普通阻断字符串不误判。
10. `test_fallback_card_v2_title_is_saved`：`.card-v2-title` 可正常保存商品。
11. `test_tricouri_real_structure_fixture_is_exact_product_page`：真实最小 fixture 必须精确判定商品页并解析成功。
12. `test_three_category_schedule_continues_after_tricouri`：A、Tricouri、B 三类目都执行且成功。
13. `test_all_cards_failed_writes_each_error_then_page_error`：每卡错误和 `ALL_PARSE_FAILED` 都写入 CSV，退出码 2。
14. `test_unknown_http200_writes_html_json_diagnostics`：退出码 2、完成类目 0、HTML/JSON 诊断存在且字段正确。
15. `test_explicit_empty_category_completes_and_continues`：明确空类目退出码 0、计为完成并继续。
16. `test_http200_waf_preserves_prior_data_and_stops_next`：退出码 3、先前数据保留、后续类目不请求。
17. `test_normal_http200_builds_dom_once`：正常 HTTP 200 构建 DOM 严格等于 1 次。
18. `test_http200_waf_builds_dom_once`：HTTP 200 WAF 构建 DOM 严格等于 1 次。
19. `test_http_status_waf_builds_no_dom`：403/429/511 构建 DOM 严格等于 0 次。

## 19. 完整测试连续两次结果

测试使用 Python `3.12.13`，并将 pytest 临时目录放入仓库忽略的 `output/` 路径，避免系统临时目录权限影响。

- 新增专项测试：`19 passed, 44 warnings in 10.89s`
- 相关旧测试与专项测试：`101 passed, 144 warnings in 24.59s`
- 完整测试第 1 次：`204 passed, 224 warnings in 169.39s (0:02:49)`
- 完整测试第 2 次：`204 passed, 224 warnings in 169.16s (0:02:49)`
- 两次完整测试均为 `0` 失败。
- 完整测试包括现有 `test_integration.py`，因此单元和集成测试均被覆盖。
- 警告均为 BeautifulSoup/lxml 的 `strip_cdata` 弃用警告，不是测试失败。

## 20. pip check 结果

`No broken requirements found.`

## 21. 三次真实低频验证逐次结果

全部使用 Scrapling 纯 HTTP、1 页、无图片，不使用浏览器、代理、验证码破解或高频重试。正式 `config/categories.txt` 未被修改，临时配置与验证输出均位于被 Git 忽略的 `output/`。

### 第 1 次：仅 Tricouri Sport

- 请求时间：`2026-08-07T08:58:26.823479+00:00`
- HTTP：`200`
- 最终 URL：与请求 URL 相同
- 重定向链：空
- 页面标题：正常 Tricouri Sport 商品标题
- HTML MD5：`61e2eae8ac1949586759752cc6df7285`
- 响应长度：`884428`
- 候选/成功/失败：`60/60/0`
- 下一页：`https://www.emag.ro/tricouri-sport/p2/c`
- 总页数：`100`
- stop_reason：`requested_limit_reached`
- 状态/退出码：`completed/0`
- 是否继续下一个类目：否；本次临时配置只含一个类目
- JSON/CSV/XLSX：`60/60/60`

### 第 2 次：正常 A、Tricouri Sport、正常 B

- Tricouri 请求时间：`2026-08-07T08:58:52.642030+00:00`
- HTTP：`200`
- 最终 URL：与请求 URL 相同
- 重定向链：空
- 页面标题：正常 Tricouri Sport 商品标题
- HTML MD5：`1b26f8d0d2e9d1c64ee8baeaf58e2109`
- 响应长度：`884527`
- 候选/成功/失败：`60/60/0`
- stop_reason：`requested_limit_reached`
- 状态/退出码：`completed/0`
- 是否继续下一个类目：是；B 类目也成功抓取 60 条
- 三类目完成：`3/3`
- JSON/CSV/XLSX：`180/180/180`

### 第 3 次：正常 A、Tricouri Sport、正常 B

- Tricouri 请求时间：`2026-08-07T08:59:27.798233+00:00`
- HTTP：`200`
- 最终 URL：与请求 URL 相同
- 重定向链：空
- 页面标题：正常 Tricouri Sport 商品标题
- HTML MD5：`1348ebcb9a0b62353b446cbaf41abf84`
- 响应长度：`884526`
- 候选/成功/失败：`60/60/0`
- stop_reason：`requested_limit_reached`
- 状态/退出码：`completed/0`
- 是否继续下一个类目：是；B 类目也成功抓取 60 条
- 三类目完成：`3/3`
- JSON/CSV/XLSX：`180/180/180`

## 22. 是否抓到大于 0 条真实商品

是。三次真实验证中的 `Tricouri Sport` 都抓到并成功解析 `60` 条真实商品。

## 23. 是否继续执行下一个类目

是。第 2、3 次三类目验证均在 `Tricouri Sport` 后继续请求并完成正常 B 类目；整体状态 `completed`，退出码 `0`。

## 24. 已抓数据是否完整保留

是。原输出目录 `output/20260806_174633` 未被删除或覆盖，`run_summary.json` 中原有 `9599` 条记录仍在；修复和测试不会清空既有输出。HTTP 200 WAF 集成测试也验证了发生致命阻断时先前成功数据仍会导出并保留。

## 25. JSON/CSV/XLSX 数量是否一致

一致：

- 原输出 `output/20260806_174633`：`9599/9599/9599`
- 第 1 次真实验证：`60/60/60`
- 第 2 次真实验证：`180/180/180`
- 第 3 次真实验证：`180/180/180`

## 26. config/categories.txt 是否未修改

第二阶段未修改。接管基线与修复后的 Git blob 哈希均为：

`13d7c3e357e76f90754e46dcb67e762f96cf621c`

第一阶段提交保存的是用户接管前已有的该文件修改，没有覆盖其内容。

## 27. 其他分支是否保持不变

保持不变。修复前后本地分支 SHA：

- `V2.1.1`：`a94cc5c0a0dbb14011018e856c827f7b69eda088`
- `V2.1.2`：`ff6963a64aa09b65ab489d0903f9cf1806964b88`
- `V2.1.3`：`4306b1fb1176dde258f892cc803c52550fc241a8`
- `main`：`6ffcf97335e0e4cc937ace3558873d19fea728f4`

## 28. 已知限制

- 真实网站未来仍可能变更 DOM；当前修复针对稳定语义类和真实页面结构，不对单个商品写死。
- 纯 HTTP 请求未来仍可能遇到真实 WAF；程序会停止、保留数据并输出诊断，不会尝试验证码破解、浏览器或代理绕过。
- 本机仓库原 `.venv` 的 Python 启动器指向已不存在的 Windows Store Python；本次使用 Codex 的 Python 3.12.13 与项目 `.venv` site-packages 完成测试。这是本地环境问题，不属于本次抓取逻辑修复。
- 无图片模式下，现有分类摘要中的 `image_failed` 与总计字段口径，以及达到页数上限时分类 `elapsed_seconds` 为 0，是本次范围外的既有统计问题，本次未顺便修改。

## 29. 需要 ChatGPT 重点复核的文件、函数和行号

- `parser.py:25`：`select_product_cards()` 的真实卡片选择器。
- `parser.py:70`：`_parse_product_card()` 的收藏 JSON ID 回退。
- `utils.py:160`：`detect_waf_block()` 的唯一 WAF 流程。
- `utils.py:224`：`_visible_text_chunks()` 的可见正文提取。
- `utils.py:244`：`_soup_strong_waf_body()` 的可见验证码上下文判断。
- `utils.py:267`：`_is_visible_element()` 的全祖先隐藏判断。
- `crawler.py:199`：`_fetch_and_parse_page()` 的 HTTP 状态与单 DOM 分类顺序。
- `crawler.py:297`：`_parse_products_soup()` 的逐卡原始异常收集。
- `crawler.py:320`：`_save_unknown_http200_diagnostic()` 的异常 HTML/JSON 输出。
- `crawler.py:501`：`_commit_page_errors()` 的逐卡错误落盘。
- `crawler.py:569`：`ERROR_FIELDNAMES` 中新增的卡片位置字段。
- `tests/test_v214_codex_takeover.py:123` 至 `371`：19 个专项测试。
- `tests/fixtures/tricouri_sport_fashion_minimal.html:1`：脱敏真实结构 fixture。

## 附加核验

- DOM 解析次数：正常 HTTP 200 为 `1`，HTTP 200 WAF 为 `1`，403/429/511 为 `0`。
- `git diff --check`：通过；只有 Git 的 LF/CRLF 工作区提示，没有空白错误。
- 没有提交运行输出、真实诊断 HTML、Cookie、Token、Authorization、账号信息、日志、图片或缓存。
- 没有使用 `git reset --hard`、`git checkout --`、`git clean -fd` 或强制推送。
