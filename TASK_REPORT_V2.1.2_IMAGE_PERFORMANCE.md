# 图片下载性能专项检查与修复报告 V2.1.2

## 最终结论

**正常图片一直在静默下载**。本次实施3项调度优化 + 1项速率修复 + 16个专项测试。

## 版本信息

| 项目 | 值 |
|------|-----|
| 修复前完整 SHA | `e19dbe4b8963cbe98f90e3393c8d4e4319a3e175` |
| 图片代码修复 SHA | `d49c74d` |
| 当前分支 | `V2.1.2` |
| 是否已推送 | 是 |

## 修改文件清单

| 文件 | 变更 |
|------|------|
| `image_downloader.py` | wait(FIRST_COMPLETED) + Future上限修复 + 速率修复 + 60s进度 |
| `tests/test_image_perf.py` | 16个专项测试 (完全重写) |

**本轮没有修改**: `categories.txt`, `crawler.py`, `main.py`, 及其他生产代码。

## 修复详情

### 1. wait(FIRST_COMPLETED) 替代 50ms 轮询
- **位置**: `image_downloader.py:download_batch()` 中的图片调度循环
- **旧代码**: `time.sleep(0.05)` 循环检查 `future.done()`
- **新代码**: `wait(in_flight, timeout=progress_interval, return_when=FIRST_COMPLETED)`
- 完成后立即处理、立即补充

### 2. Future 上限修复
- **旧代码**: `max(self.max_workers * 2, 16)` — max_workers=1时仍16个Future
- **新代码**: `max(1, min(self.max_in_flight, self.max_workers * 2))`
- 默认参数: max_workers=8, max_in_flight=16 → 上限=16

### 3. 速率修复
- **旧问题**: `累计完成数 / 上次打印后的时间` → 虚高
- **新代码**: `batch_done / max(elapsed, 0.001)` — 用 `time.perf_counter()` 和本批次增量
- 第二批从0开始, 不继承上一批速率

### 4. 进度日志 → 60s
- 删除"每500张"触发条件
- 保留60s间隔 + 最终完成
- 批次全部完成时只打印一次

### 5. 错误日志策略不变
- TOO_SMALL/HTML_RESPONSE/UNKNOWN_FORMAT/BLOCKED_PATTERN: 不打印WARNING, 仍入errors.csv
- TIMEOUT/CONNECT_ERROR/NETWORK_ERROR/HTTP_5xx: 继续WARNING

## 测试结果

| 项目 | 值 |
|------|-----|
| 全部测试 | **127 passed, 0 failed** |
| 连续两次 | 127/127, 127/127 |
| 性能测试 | **16/16** |
| 性能三连 | 16/16, 16/16, 16/16 (77s/77s/77s) |
| pip check | 通过 |

### 1/4/8 线程固定延迟测试 (60ms/张, 30张)

| 线程 | 结果 |
|------|------|
| 1线程 | 串行, 基线 |
| 4线程 | 显著快于1线程 (4w < 1w*0.8) |
| 8线程 | 不快于4线程的1.5倍 (稳定) |
| max_active | 1 < max_active ≤ 4 |

### 快速200任务测试
- 200个任务, 8线程, 无轮询延迟
- `wait(FIRST_COMPLETED)` 正确工作, 无固定轮询开销

### 真实图片测试
- 60张图片, 4线程
- 60/0 成功/失败
- 7.9张/秒
- 进度日志正常: `图片进度：60/60（成功60，失败0）速率：7.9张/秒`

## 已知限制

1. `httpx.Client` 共享在所有线程中, 连接池上限由httpx默认值决定
2. 大量TOO_SMALL图片仍需逐个下载后才能检测(无法预判)

## 需要 ChatGPT 复核

1. `image_downloader.py:101` — `wait(FIRST_COMPLETED)` 替代轮询
2. `image_downloader.py:55` — `in_flight_limit = max(1, min(self.max_in_flight, self.max_workers * 2))`
3. `image_downloader.py:140` — 速率计算 `batch_done / max(elapsed, 0.001)` 用 `time.perf_counter()`
4. `tests/test_image_perf.py` — 16个专项测试(并发/有界/进度/速率/混合/回填/errors.csv/快速调度)
