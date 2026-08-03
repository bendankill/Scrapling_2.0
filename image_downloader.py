"""
图片下载模块 V2.0.2: 下载商品主图, 错误记录, 同URL多商品回填
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
    """图片下载器, 支持并发控制、缓存、同URL多商品回填"""

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

        # URL → local_path 缓存 (同URL多商品共享)
        self._url_path_cache: dict[str, str] = {}
        self._cache_lock = Lock()

        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,  # 跟随重定向
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
        批量下载图片。
        返回: {product_key: local_path} (同URL多商品均回填)
        """
        # 按 product_key 去重
        unique_products: dict[str, dict] = {}
        for p in products:
            key = get_product_key(p)
            img_url = (p.get("main_image_url") or "").strip()
            if key and key not in unique_products and img_url:
                unique_products[key] = p

        # 按图片URL去重: url → [product_keys]
        url_to_keys: dict[str, list[str]] = {}
        for key, prod in unique_products.items():
            url = prod["main_image_url"]
            if url not in url_to_keys:
                url_to_keys[url] = []
            url_to_keys[url].append(key)

        logger.info(f"准备下载 {len(url_to_keys)} 个唯一URL的图片 (对应 {len(unique_products)} 个商品)")

        # 并发下载
        result: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}
            for url in url_to_keys:
                future = executor.submit(self._download_url, url)
                futures[future] = url

            for future in as_completed(futures):
                url = futures[future]
                try:
                    local_path = future.result()
                    if local_path:
                        # 回填所有使用此URL的商品
                        for key in url_to_keys[url]:
                            result[key] = local_path
                        self.success_count += 1
                        with self._cache_lock:
                            self._url_path_cache[url] = local_path
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

    def _download_url(self, url: str) -> Optional[str]:
        """下载单个图片URL"""
        if not self._should_download(url):
            return None

        # 检查缓存
        with self._cache_lock:
            if url in self._url_path_cache:
                return self._url_path_cache[url]

        with self.semaphore:
            # 使用 PNK 优先命名
            filename = self._filename_from_url(url)
            return self._do_download(url, filename)

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

            # 魔数检测实际格式
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

    def _filename_from_url(self, url: str) -> str:
        """从 URL 生成文件名 (基于 URL 哈希, 确保一致性)"""
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
