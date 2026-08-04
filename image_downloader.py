"""
图片下载模块 V2.1.3: PNK命名, 有效小图片保留, 魔数验证
"""
import os, hashlib, logging, time, re, shutil
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from threading import Semaphore, Lock
from typing import Optional
import httpx
from utils import sanitize_filename, get_product_key

logger = logging.getLogger("emag_crawler.images")

BLOCKED_PATTERNS = ["data:image","placeholder","loading","logo","pixel","tracking",".svg","blank","spacer","1x1","base64"]
VALID_CONTENT_TYPES = {"image/jpeg","image/jpg","image/png","image/webp","image/avif"}
VALID_EXTENSIONS = {".jpg",".jpeg",".png",".webp",".avif"}
MAGIC_TO_EXT = {b'\xff\xd8\xff':'.jpg', b'\x89PNG':'.png', b'RIFF':'.webp', b'\x00\x00\x00\x1cftypavif':'.avif'}
PROGRESS_INTERVAL_SECONDS = 60
NON_RETRYABLE_ERRORS = frozenset({"HTML_RESPONSE", "UNKNOWN_FORMAT", "BLOCKED_PATTERN"})
# Windows 文件名非法字符
_INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

class ImageDownloadError(Exception):
    def __init__(self, image_url: str, error_type: str, http_status: int = 0,
                 detail: str = "", product_keys: list = None):
        self.image_url = image_url; self.error_type = error_type
        self.http_status = http_status; self.detail = detail
        self.product_keys = product_keys or []
        super().__init__(f"[{error_type}] {image_url}: {detail}")


class ImageDownloader:
    def __init__(self, output_dir: str, max_workers: int = 8,
                 max_in_flight: int = 4, global_semaphore: Optional[Semaphore] = None,
                 timeout: int = 30):
        self.output_dir = os.path.join(output_dir, "images")
        os.makedirs(self.output_dir, exist_ok=True)
        self.max_workers = max_workers
        self.max_in_flight = max_in_flight
        self.semaphore = global_semaphore or Semaphore(max_in_flight)
        self.timeout = timeout
        self._url_lock = Lock()
        self._url_path_cache: dict[str, str] = {}  # URL → local_path
        self._cache_lock = Lock()
        # PNK → 已用后缀计数 (处理PNK冲突)
        self._pnk_count: dict[str, int] = {}
        self._pnk_lock = Lock()
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout), follow_redirects=True, max_redirects=5,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                     "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"})
        self.success_count = 0; self.fail_count = 0
        self.errors: list[dict] = []; self._errors_lock = Lock()
        self.progress_interval = PROGRESS_INTERVAL_SECONDS

    def download_batch(self, products: list[dict]) -> dict[str, str]:
        # 按URL分组: url → [product_keys] (每个URL下载一次, 所有相关商品回填)
        url_to_keys: dict[str, list[str]] = {}
        url_sample: dict[str, dict] = {}  # url → 样本商品(用于PNK命名)
        for p in products:
            img_url = (p.get("main_image_url") or "").strip()
            key = get_product_key(p)
            if not img_url or not key:
                continue
            if img_url not in url_to_keys:
                url_to_keys[img_url] = []
                url_sample[img_url] = p
            url_to_keys[img_url].append(key)

        total = len(url_to_keys)
        unique_prod_count = len(set(k for keys in url_to_keys.values() for k in keys))
        logger.info(f"准备下载 {total} 个唯一URL (对应 {unique_prod_count} 个商品)")

        result: dict[str, str] = {}
        url_items = list(url_to_keys.items())
        in_flight_limit = max(1, min(self.max_in_flight, self.max_workers * 2))
        in_flight: dict = {}; next_idx = 0
        batch_start = time.perf_counter(); last_progress = batch_start
        batch_success_start = self.success_count; batch_fail_start = self.fail_count

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            while next_idx < len(url_items) and len(in_flight) < in_flight_limit:
                url, keys = url_items[next_idx]
                sample = url_sample[url]
                fut = executor.submit(self._download_single, url, sample, keys)
                in_flight[fut] = (url, keys, sample)
                next_idx += 1

            while in_flight:
                done, _ = wait(in_flight, timeout=self.progress_interval, return_when=FIRST_COMPLETED)
                for fut in done:
                    url, keys, sample = in_flight.pop(fut)
                    try:
                        local_path = fut.result()
                        if local_path:
                            for key in keys: result[key] = local_path
                            self.success_count += 1
                            with self._cache_lock: self._url_path_cache[url] = local_path
                        else:
                            self.fail_count += 1
                    except ImageDownloadError as e:
                        self.fail_count += 1
                        with self._errors_lock:
                            self.errors.append({
                                "image_url": e.image_url, "error_type": e.error_type,
                                "http_status": e.http_status, "error_detail": e.detail,
                                "product_key": ", ".join(e.product_keys) if e.product_keys else "",
                                "category": sample.get("category_name", ""),
                                "page": sample.get("page_number", 0), "url": e.image_url})
                        if e.error_type not in NON_RETRYABLE_ERRORS:
                            logger.warning(f"图片下载失败 [{url[:80]}]: {e}")
                    except Exception as e:
                        self.fail_count += 1
                        with self._errors_lock:
                            self.errors.append({
                                "image_url": url, "error_type": type(e).__name__,
                                "http_status": 0, "error_detail": str(e)[:500],
                                "product_key": ", ".join(keys) if keys else "",
                                "category": "", "page": 0, "url": url})
                        logger.warning(f"图片下载失败 [{url[:80]}]: {e}")

                while next_idx < len(url_items) and len(in_flight) < in_flight_limit:
                    url, keys = url_items[next_idx]
                    sample = url_sample[url]
                    fut = executor.submit(self._download_single, url, sample, keys)
                    in_flight[fut] = (url, keys, sample)
                    next_idx += 1

                now = time.perf_counter()
                batch_success = self.success_count - batch_success_start
                batch_failed = self.fail_count - batch_fail_start
                batch_done = batch_success + batch_failed
                all_done = batch_done >= total
                if all_done or now - last_progress >= self.progress_interval:
                    elapsed = now - batch_start
                    avg_rate = batch_done / max(elapsed, 0.001)
                    h = int(elapsed // 3600); m = int((elapsed % 3600) // 60); s = elapsed % 60
                    logger.info(
                        f"图片进度：{batch_done}/{total}"
                        f"（成功{batch_success}，失败{batch_failed}）"
                        f"速率：{avg_rate:.1f}张/秒，已耗时：{h:02d}:{m:02d}:{s:04.1f}")
                    if not all_done: last_progress = now

        return result

    def _download_single(self, url: str, product: dict, product_keys: list) -> Optional[str]:
        if not self._should_download(url):
            raise ImageDownloadError(url, "BLOCKED_PATTERN", detail="URL matches blocked pattern",
                                    product_keys=product_keys)
        with self._cache_lock:
            if url in self._url_path_cache:
                return self._url_path_cache[url]
        with self.semaphore:
            return self._do_download(url, product, product_keys)

    def _do_download(self, url: str, product: dict, product_keys: list) -> Optional[str]:
        # 生成基于PNK的文件名(扩展名后续由魔数检测决定)
        base_name = self._make_pnk_basename(product)
        try:
            resp = self._client.get(url)
        except httpx.TimeoutException:
            raise ImageDownloadError(url, "TIMEOUT", detail="Request timed out", product_keys=product_keys)
        except httpx.ConnectError as e:
            raise ImageDownloadError(url, "CONNECT_ERROR", detail=str(e)[:200], product_keys=product_keys)
        except Exception as e:
            raise ImageDownloadError(url, "NETWORK_ERROR", detail=str(e)[:200], product_keys=product_keys)

        if resp.status_code != 200:
            raise ImageDownloadError(url, f"HTTP_{resp.status_code}", http_status=resp.status_code,
                                    detail=f"HTTP {resp.status_code}", product_keys=product_keys)

        content = resp.content
        if not content:
            raise ImageDownloadError(url, "EMPTY_RESPONSE", http_status=200,
                                    detail="Empty response body", product_keys=product_keys)

        # HTML/WAF 检测
        stripped = content[:200].strip()
        if stripped.startswith(b"<!") or stripped.startswith(b"<html") or stripped.startswith(b"<HTML"):
            raise ImageDownloadError(url, "HTML_RESPONSE", http_status=200,
                                    detail="Response is HTML, not an image", product_keys=product_keys)

        # 魔数检测真实格式
        detected_ext = None
        for magic, ext in MAGIC_TO_EXT.items():
            if content[:len(magic)] == magic:
                detected_ext = ext
                break

        if detected_ext is None:
            ct = resp.headers.get("content-type", "").split(";")[0].strip()
            ext_map = {"image/jpeg":".jpg","image/jpg":".jpg","image/png":".png","image/webp":".webp","image/avif":".avif"}
            detected_ext = ext_map.get(ct)
            if detected_ext is None:
                raise ImageDownloadError(url, "UNKNOWN_FORMAT", http_status=200,
                                        detail=f"Cannot identify format: content-type={ct}",
                                        product_keys=product_keys)

        # 确定最终文件名 (处理PNK冲突)
        filename = self._resolve_pnk_filename(base_name, detected_ext)
        local_path = os.path.join(self.output_dir, filename)
        with open(local_path, "wb") as f: f.write(content)
        return local_path

    def _make_pnk_basename(self, product: dict) -> str:
        """从商品信息生成PNK基础名"""
        pnk = (product.get("pnk") or "").strip()
        if pnk:
            # Windows 安全处理: 替换非法字符
            safe = _INVALID_CHARS_RE.sub('_', pnk)
            safe = safe.rstrip('. ')
            if safe:
                return safe
        # 缺少PNK: 使用兜底名
        pid = (product.get("product_id") or "").strip()
        if pid:
            safe = _INVALID_CHARS_RE.sub('_', pid)
            safe = safe.rstrip('. ')
            if safe:
                return f"NO_PNK_{safe}"
        # 最终兜底: URL哈希
        url = (product.get("main_image_url") or "").strip()
        if url:
            url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
            return f"NO_PNK_{url_hash}"
        return f"NO_PNK_{hashlib.md5(str(product).encode()).hexdigest()[:12]}"

    def _resolve_pnk_filename(self, base_name: str, ext: str) -> str:
        """处理PNK文件名冲突: 第一张{PNK}.ext, 后续{PNK}_2.ext"""
        candidate = f"{base_name}{ext}"
        local_path = os.path.join(self.output_dir, candidate)
        if not os.path.exists(local_path):
            return candidate
        # 文件已存在: 检查大小是否有效
        if os.path.getsize(local_path) > 0:
            with self._pnk_lock:
                count = self._pnk_count.get(base_name, 1)
                while True:
                    count += 1
                    candidate = f"{base_name}_{count}{ext}"
                    if not os.path.exists(os.path.join(self.output_dir, candidate)):
                        self._pnk_count[base_name] = count
                        return candidate
        return candidate

    def _should_download(self, url: str) -> bool:
        if not url: return False
        for p in BLOCKED_PATTERNS:
            if p in url.lower(): return False
        return True

    def close(self): self._client.close()

    def get_stats(self) -> dict:
        with self._errors_lock:
            return {"success": self.success_count, "failed": self.fail_count, "errors": list(self.errors)}
