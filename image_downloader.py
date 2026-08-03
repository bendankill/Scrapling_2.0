"""
图片下载模块 V2.0.3: 下载主图, 结构化错误记录, 同URL多商品回填
"""
import os
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Semaphore, Lock
from typing import Optional

import httpx
from models import ProductItem
from utils import sanitize_filename, get_product_key

logger = logging.getLogger("emag_crawler.images")

BLOCKED_PATTERNS = [
    "data:image", "placeholder", "loading", "logo",
    "pixel", "tracking", ".svg", "blank", "spacer", "1x1", "base64",
]
MIN_IMAGE_SIZE = 1024
VALID_CONTENT_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/avif"}
VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
MAGIC_TO_EXT = {
    b'\xff\xd8\xff': '.jpg',
    b'\x89PNG': '.png',
    b'RIFF': '.webp',
    b'\x00\x00\x00\x1cftypavif': '.avif',
}


class ImageDownloadError(Exception):
    """图片下载失败, 携带结构化错误信息"""
    def __init__(self, image_url: str, error_type: str, http_status: int = 0,
                 detail: str = "", product_keys: list = None):
        self.image_url = image_url
        self.error_type = error_type
        self.http_status = http_status
        self.detail = detail
        self.product_keys = product_keys or []
        super().__init__(f"[{error_type}] {image_url}: {detail}")


class ImageDownloader:
    """图片下载器 V2.0.3"""

    def __init__(self, output_dir: str, max_workers: int = 8,
                 max_in_flight: int = 16, global_semaphore: Optional[Semaphore] = None,
                 timeout: int = 30):
        self.output_dir = os.path.join(output_dir, "images")
        os.makedirs(self.output_dir, exist_ok=True)
        self.max_workers = max_workers
        self.semaphore = global_semaphore or Semaphore(max_in_flight)
        self.timeout = timeout

        self._downloaded_urls: set = set()
        self._url_lock = Lock()
        self._url_path_cache: dict[str, str] = {}
        self._cache_lock = Lock()

        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
            max_redirects=5,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            },
        )

        self.success_count = 0
        self.fail_count = 0
        self.errors: list[dict] = []
        self._errors_lock = Lock()

    def download_batch(self, products: list[dict]) -> dict[str, str]:
        """
        批量下载图片。仅下载不抛出, 错误记录到 self.errors。
        返回: {product_key: local_path}
        """
        unique_products: dict[str, dict] = {}
        for p in products:
            key = get_product_key(p)
            img_url = (p.get("main_image_url") or "").strip()
            if key and key not in unique_products and img_url:
                unique_products[key] = p

        url_to_keys: dict[str, list[str]] = {}
        for key, prod in unique_products.items():
            url = prod["main_image_url"]
            if url not in url_to_keys:
                url_to_keys[url] = []
            url_to_keys[url].append(key)

        logger.info(f"准备下载 {len(url_to_keys)} 个唯一URL (对应 {len(unique_products)} 个商品)")

        result: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}
            for url, keys in url_to_keys.items():
                # 传入一个商品用于获取类目/页码信息
                sample = unique_products[keys[0]]
                future = executor.submit(self._download_single, url, sample, keys)
                futures[future] = (url, keys)

            for future in as_completed(futures):
                url, keys = futures[future]
                try:
                    local_path = future.result()
                    if local_path:
                        for key in keys:
                            result[key] = local_path
                        self.success_count += 1
                        with self._cache_lock:
                            self._url_path_cache[url] = local_path
                    else:
                        self.fail_count += 1
                except ImageDownloadError as e:
                    self.fail_count += 1
                    with self._errors_lock:
                        self.errors.append({
                            "image_url": e.image_url,
                            "error_type": e.error_type,
                            "http_status": e.http_status,
                            "error_detail": e.detail,
                            "product_key": ", ".join(e.product_keys) if e.product_keys else "",
                            "category": sample.get("category_name", ""),
                            "page": sample.get("page_number", 0),
                            "url": e.image_url,
                        })
                    logger.warning(f"图片下载失败 [{url[:80]}]: {e}")
                except Exception as e:
                    self.fail_count += 1
                    with self._errors_lock:
                        self.errors.append({
                            "image_url": url,
                            "error_type": type(e).__name__,
                            "http_status": 0,
                            "error_detail": str(e)[:500],
                            "product_key": ", ".join(keys) if keys else "",
                            "category": "",
                            "page": 0,
                            "url": url,
                        })
                    logger.warning(f"图片下载失败 [{url[:80]}]: {e}")

        return result

    def _download_single(self, url: str, product: dict, product_keys: list) -> Optional[str]:
        """下载单个图片URL, 失败抛出 ImageDownloadError"""
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
        """执行下载, 失败抛出 ImageDownloadError"""
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
            if content[:len(magic)] == magic:
                detected_ext = ext
                break

        if detected_ext is None:
            content_type = resp.headers.get("content-type", "").split(";")[0].strip()
            ext_map = {"image/jpeg": ".jpg", "image/jpg": ".jpg",
                      "image/png": ".png", "image/webp": ".webp", "image/avif": ".avif"}
            detected_ext = ext_map.get(content_type)
            if detected_ext is None:
                raise ImageDownloadError(url, "UNKNOWN_FORMAT", http_status=200,
                                        detail=f"Cannot identify format: content-type={content_type}",
                                        product_keys=product_keys)

        filename = filename.rsplit(".", 1)[0] + detected_ext
        local_path = os.path.join(self.output_dir, filename)

        with open(local_path, "wb") as f:
            f.write(content)
        return local_path

    def _should_download(self, url: str) -> bool:
        if not url:
            return False
        url_lower = url.lower()
        for pattern in BLOCKED_PATTERNS:
            if pattern in url_lower:
                return False
        return True

    def _filename_from_url(self, url: str) -> str:
        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        ext = os.path.splitext(url.split("?")[0])[1]
        if ext.lower() not in VALID_EXTENSIONS:
            ext = ".jpg"
        return f"{url_hash}{ext}"

    def close(self):
        self._client.close()

    def get_stats(self) -> dict:
        with self._errors_lock:
            return {"success": self.success_count, "failed": self.fail_count,
                    "errors": list(self.errors)}
