"""
图片下载模块：下载商品主图，处理错误和缓存
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


class ImageDownloader:
    """图片下载器，支持并发控制和缓存检查"""

    def __init__(self, output_dir: str, max_workers: int = 8,
                 max_in_flight: int = 16, global_semaphore: Optional[Semaphore] = None,
                 timeout: int = 30):
        self.output_dir = os.path.join(output_dir, "images")
        os.makedirs(self.output_dir, exist_ok=True)
        self.max_workers = max_workers
        self.semaphore = global_semaphore or Semaphore(max_in_flight)
        self.timeout = timeout

        # 已经下载的图片URL缓存
        self._downloaded_urls: set = set()
        self._url_lock = Lock()

        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
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
        批量下载图片。
        products: list of dict (每个dict需包含 pnk, product_id, product_url, main_image_url)
        返回: {product_key: local_path}
        """
        # 按 product_key 去重: 每个唯一商品只下载一次
        unique_products: dict[str, dict] = {}
        for p in products:
            key = get_product_key(p)
            img_url = (p.get("main_image_url") or "").strip()
            if key and key not in unique_products and img_url:
                unique_products[key] = p

        logger.info(f"准备下载 {len(unique_products)} 个唯一商品的图片")
        result: dict[str, str] = {}

        # 先按图片URL再次去重
        url_to_key: dict[str, str] = {}
        for key, prod in unique_products.items():
            url = prod["main_image_url"]
            if url not in url_to_key:
                url_to_key[url] = key

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}
            for url, key in url_to_key.items():
                prod = unique_products[key]
                future = executor.submit(self._download_single, url, prod)
                futures[future] = (url, key)

            for future in as_completed(futures):
                url, key = futures[future]
                try:
                    local_path = future.result()
                    if local_path:
                        result[key] = local_path
                        self.success_count += 1
                    else:
                        self.fail_count += 1
                except Exception as e:
                    self.fail_count += 1
                    with self._errors_lock:
                        self.errors.append({
                            "url": url,
                            "error_type": type(e).__name__,
                            "error_detail": str(e)[:500],
                        })
                    logger.warning(f"图片下载失败 [{url[:80]}]: {e}")

        return result

    def _download_single(self, url: str, product: dict) -> Optional[str]:
        """下载单个商品的主图"""
        if not self._should_download(url):
            return None

        # 检查是否已下载
        with self._url_lock:
            if url in self._downloaded_urls:
                return self._get_local_path(url)

        with self.semaphore:
            filename = self._generate_filename(product, url)
            local_path = self._do_download(url, filename)

        if local_path:
            with self._url_lock:
                self._downloaded_urls.add(url)
        return local_path

    def _do_download(self, url: str, filename: str) -> Optional[str]:
        """执行实际下载"""
        local_path = os.path.join(self.output_dir, filename)

        if os.path.exists(local_path) and os.path.getsize(local_path) > MIN_IMAGE_SIZE:
            return local_path

        try:
            resp = self._client.get(url)
            if resp.status_code != 200:
                raise Exception(f"HTTP {resp.status_code}")

            content = resp.content
            if len(content) < MIN_IMAGE_SIZE:
                raise Exception(f"图片过小: {len(content)} 字节")

            if content[:100].strip().startswith(b"<!") or content[:100].strip().startswith(b"<html"):
                raise Exception("响应为 HTML 页面，非图片")

            # 魔数检测
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
                    raise Exception(f"无法识别图片格式: content-type={content_type}")

            filename = filename.rsplit(".", 1)[0] + detected_ext
            local_path = os.path.join(self.output_dir, filename)

            with open(local_path, "wb") as f:
                f.write(content)
            return local_path

        except Exception as e:
            logger.debug(f"下载失败 [{url[:80]}]: {e}")
            return None

    def _should_download(self, url: str) -> bool:
        if not url:
            return False
        url_lower = url.lower()
        for pattern in BLOCKED_PATTERNS:
            if pattern in url_lower:
                return False
        return True

    def _generate_filename(self, product: dict, url: str) -> str:
        pnk = (product.get("pnk") or "").strip()
        pid = (product.get("product_id") or "").strip()
        base = pnk or pid or hashlib.md5(url.encode()).hexdigest()[:12]
        base = sanitize_filename(base)
        ext = os.path.splitext(url.split("?")[0])[1]
        if ext.lower() not in VALID_EXTENSIONS:
            ext = ".jpg"
        return f"{base}{ext}"

    def _get_local_path(self, url: str) -> Optional[str]:
        for fname in os.listdir(self.output_dir):
            fpath = os.path.join(self.output_dir, fname)
            if os.path.isfile(fpath) and os.path.getsize(fpath) > MIN_IMAGE_SIZE:
                # Use a hash to check if file matches URL
                if hashlib.md5(url.encode()).hexdigest()[:12] in fname:
                    return fpath
        return None

    def close(self):
        self._client.close()

    def get_stats(self) -> dict:
        with self._errors_lock:
            return {"success": self.success_count, "failed": self.fail_count, "errors": list(self.errors)}
