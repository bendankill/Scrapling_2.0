# V2.2.1 真实 HTTP 脱敏证据

## 结论

本轮规定的唯一一次 eMAG 请求真实发出并收到 HTTP 511。程序按 WAF 规则在 DOM 解析前终止，状态为 `waf_blocked`、退出码为 3；没有重试，也没有第二次真实请求。因此本次响应不包含可供生产类目解析器采用的类目页面证据，不能用它验证 Schema.org 面包屑或 Boxe 中间层级。

## 请求与运行参数

- UTC 时间：`2026-08-19T08:24:17.996869+00:00`
- 功能提交 SHA：`95b81ec1de152f2b314404b91c0f54fe0eeec5ad`
- URL：`https://www.emag.ro/boxe/c?ref=bc`
- 最终 URL：`https://www.emag.ro/boxe/c?ref=bc`
- 抓取方式：Scrapling 纯 HTTP
- 类目数：1
- 页数：1
- 下载图片：否
- 商品详情请求：0
- `page_workers=1`
- `category_workers=1`
- `max_in_flight=1`
- `retries=1`（总尝试次数为1）
- 浏览器、Playwright、Chromium、Selenium：均未使用

## HTTP 与生命周期事实

- 生产 `_fetch_page()` 调用计数：1
- 实际响应状态：HTTP 511
- Content-Type：`text/html`
- 程序状态：`waf_blocked`
- 退出码：3
- `request_call_started=True`：已经进入 `client.get()` 调用；该字段不声称网络包一定到达远端
- `response_received=True`：已收到 Scrapling 返回的 HTTP 响应对象
- Session generation：0
- Soup 创建次数：0（403/429/511按设计不解析DOM）
- 页面类目证据提取次数：0
- 是否重试：否
- 是否进行了第二次真实请求：否

## 响应摘要与哈希

- 交给抓取结果处理的解码正文长度：2988 bytes
- 哈希口径：**解码后的 HTML 文本重新编码为 UTF-8 bytes**，不是底层原始传输 bytes
- 解码正文 SHA-256：`db7e39c2b256aad21ff2b1475e9b0779c6ee3b78914beb4dce332abc33f6c70c`
- 完整响应正文是否提交：否

脱敏响应结构摘要（仅为真实响应元数据，不冒充类目DOM）：

```html
<response status="511" content-type="text/html" decoded-utf8-length="2988">
  <!-- WAF终止：未构建Soup，未提取breadcrumb -->
</response>
```

## 类目与商品结果

- 候选商品卡片：0
- 成功解析商品：0
- 完成类目：0/1
- 实际采用的公共类目证据来源：无（HTTP 511在DOM解析前终止）
- 规范化真实类目路径：无
- 是否遇到标准Schema.org父级 `a[itemprop=item]`：无法判断；本次没有解析类目DOM
- 脱敏真实breadcrumb/JSON-LD片段：不存在；本次响应是WAF页面，不能从中伪造类目片段
- 是否证明页面缺少 `Audio Hi-Fi` 中间层：否；本次响应不是商品类目页

## 三格式临时输出

WAF后程序仍按既有“保留此前数据”边界创建了空产品输出：

| 文件 | 记录数 | 文件大小 | SHA-256 |
|---|---:|---:|---|
| `products.json` | 0 | 2 bytes | `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945` |
| `products.csv` | 0 | 424 bytes | `fa1fb75c7afd796119ce10c3eb4b6f6becd3dacf8228170a47b87cd2ad2a9705` |
| `products.xlsx` | 0 | 5649 bytes | `799fe050a32165daaa2befa17ab153b6ec5263a9bbf4c1a76c072c5788128bf9` |

## 清理与敏感信息

- 临时输出目录：位于系统临时目录，文件哈希和数量记录后已逐个删除
- 临时目录是否已删除：是
- 正式 `config/categories.txt` 是否使用或修改：否
- 是否记录Cookie具体值：否
- 是否记录Authorization、Token或API Key：否
- 是否提交完整60条商品输出：否；本次实际商品数为0
- 是否提交完整真实响应：否

## 可复核边界

该证据可以独立证明本轮请求确实进入 `client.get()`、收到HTTP 511、只调用一次且正确返回WAF退出码3；它不能证明当前Boxe商品页的类目层级或Schema.org结构。根据“一次低频请求”限制，Codex没有为补齐类目DOM证据再次访问eMAG。
