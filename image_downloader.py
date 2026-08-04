"""
图片下载模块 V2.1.2-fix: wait(FIRST_COMPLETED), 有界Future, 60s进度, 准确速率
"""
import os, hashlib, logging, time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from threading import Semaphore, Lock
from typing import Optional
import httpx
from utils import sanitize_filename, get_product_key

logger = logging.getLogger("emag_crawler.images")

BLOCKED_PATTERNS = ["data:image","placeholder","loading","logo","pixel","tracking",".svg","blank","spacer","1x1","base64"]
MIN_IMAGE_SIZE = 1024
VALID_CONTENT_TYPES = {"image/jpeg","image/jpg","image/png","image/webp","image/avif"}
VALID_EXTENSIONS = {".jpg",".jpeg",".png",".webp",".avif"}
MAGIC_TO_EXT = {b'\xff\xd8\xff':'.jpg', b'\x89PNG':'.png', b'RIFF':'.webp', b'\x00\x00\x00\x1cftypavif':'.avif'}
PROGRESS_INTERVAL_SECONDS = 60
# 不可恢复的图片错误: 不打印WARNING
NON_RETRYABLE_ERRORS = frozenset({"TOO_SMALL", "HTML_RESPONSE", "UNKNOWN_FORMAT", "BLOCKED_PATTERN"})


class ImageDownloadError(Exception):
    def __init__(self, image_url: str, error_type: str, http_status: int = 0,
                 detail: str = "", product_keys: list = None):
        self.image_url = image_url; self.error_type = error_type
        self.http_status = http_status; self.detail = detail
        self.product_keys = product_keys or []
        super().__init__(f"[{error_type}] {image_url}: {detail}")


class ImageDownloader:
    def __init__(self, output_dir: str, max_workers: int = 8,
                 max_in_flight: int = 16, global_semaphore: Optional[Semaphore] = None,
                 timeout: int = 30):
        self.output_dir = os.path.join(output_dir, "images")
        os.makedirs(self.output_dir, exist_ok=True)
        self.max_workers = max_workers
        self.max_in_flight = max_in_flight
        self.semaphore = global_semaphore or Semaphore(max_in_flight)
        self.timeout = timeout
        self._downloaded_urls: set = set(); self._url_lock = Lock()
        self._url_path_cache: dict[str, str] = {}; self._cache_lock = Lock()
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout), follow_redirects=True, max_redirects=5,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                     "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"})
        self.success_count = 0; self.fail_count = 0
        self.errors: list[dict] = []; self._errors_lock = Lock()
        # progress_interval 可被 monkeypatch 覆盖用于测试
        self.progress_interval = PROGRESS_INTERVAL_SECONDS

    def download_batch(self, products: list[dict]) -> dict[str, str]:
        unique_products: dict[str, dict] = {}
        for p in products:
            key = get_product_key(p)
            img_url = (p.get("main_image_url") or "").strip()
            if key and key not in unique_products and img_url:
                unique_products[key] = p

        url_to_keys: dict[str, list[str]] = {}
        for key, prod in unique_products.items():
            url = prod["main_image_url"]
            if url not in url_to_keys: url_to_keys[url] = []
            url_to_keys[url].append(key)

        total = len(url_to_keys)
        logger.info(f"准备下载 {total} 个唯一URL (对应 {len(unique_products)} 个商品)")

        result: dict[str, str] = {}
        url_items = list(url_to_keys.items())
        # Future上限: 同时受 max_in_flight 和 max_workers*2 约束
        in_flight_limit = max(1, min(self.max_in_flight, self.max_workers * 2))
        in_flight: dict = {}; next_idx = 0
        batch_start = time.perf_counter()
        last_progress = batch_start
        batch_success_start = self.success_count
        batch_fail_start = self.fail_count

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 初始有界提交
            while next_idx < len(url_items) and len(in_flight) < in_flight_limit:
                url, keys = url_items[next_idx]
                sample = unique_products[keys[0]]
                fut = executor.submit(self._download_single, url, sample, keys)
                in_flight[fut] = (url, keys, sample)
                next_idx += 1

            while in_flight:
                # wait(FIRST_COMPLETED) 替代轮询
                done, _ = wait(in_flight, timeout=self.progress_interval,
                               return_when=FIRST_COMPLETED)

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

                # 补充新任务
                while next_idx < len(url_items) and len(in_flight) < in_flight_limit:
                    url, keys = url_items[next_idx]
                    sample = unique_products[keys[0]]
                    fut = executor.submit(self._download_single, url, sample, keys)
                    in_flight[fut] = (url, keys, sample)
                    next_idx += 1

                # 60秒低频进度 (或所有完成时)
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
                    if not all_done:
                        last_progress = now

        return result

    def _download_single(self, url: str, product: dict, product_keys: list) -> Optional[str]:
        if not self._should_download(url):
            raise ImageDownloadError(url, "BLOCKED_PATTERN", detail="URL matches blocked pattern",
                                    product_keys=product_keys)
        with self._cache_lock:
            if url in self._url_path_cache:
                return self._url_path_cache[url]
        with self.semaphore:
            filename = self._filename_from_url(url)
            return self._do_download(url, filename, product_keys)

    def _do_download(self, url: str, filename: str, product_keys: list) -> Optional[str]:
        local_path = os.path.join(self.output_dir, filename)
        if os.path.exists(local_path) and os.path.getsize(local_path) > MIN_IMAGE_SIZE:
            return local_path
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
        if len(content) < MIN_IMAGE_SIZE:
            raise ImageDownloadError(url, "TOO_SMALL", http_status=200,
                                    detail=f"Image too small: {len(content)} bytes", product_keys=product_keys)
        if content[:100].strip().startswith(b"<!") or content[:100].strip().startswith(b"<html"):
            raise ImageDownloadError(url, "HTML_RESPONSE", http_status=200,
                                    detail="Response is HTML, not an image", product_keys=product_keys)
        detected_ext = None
        for magic, ext in MAGIC_TO_EXT.items():
            if content[:len(magic)] == magic: detected_ext = ext; break
        if detected_ext is None:
            ct = resp.headers.get("content-type", "").split(";")[0].strip()
            ext_map = {"image/jpeg":".jpg","image/jpg":".jpg","image/png":".png","image/webp":".webp","image/avif":".avif"}
            detected_ext = ext_map.get(ct)
            if detected_ext is None:
                raise ImageDownloadError(url, "UNKNOWN_FORMAT", http_status=200,
                                        detail=f"Cannot identify format: content-type={ct}",
                                        product_keys=product_keys)
        filename = filename.rsplit(".", 1)[0] + detected_ext
        local_path = os.path.join(self.output_dir, filename)
        with open(local_path, "wb") as f: f.write(content)
        return local_path

    def _should_download(self, url: str) -> bool:
        if not url: return False
        for p in BLOCKED_PATTERNS:
            if p in url.lower(): return False
        return True

    def _filename_from_url(self, url: str) -> str:
        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        ext = os.path.splitext(url.split("?")[0])[1]
        if ext.lower() not in VALID_EXTENSIONS: ext = ".jpg"
        return f"{url_hash}{ext}"

    def close(self): self._client.close()

    def get_stats(self) -> dict:
        with self._errors_lock:
            return {"success": self.success_count, "failed": self.fail_count, "errors": list(self.errors)}
