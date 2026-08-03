"""
图片下载模块：下载商品主图，处理错误和缓存
"""
import os
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Semaphore
from typing import Optional

import httpx
from models import ProductItem
from utils import sanitize_filename

logger = logging.getLogger("emag_crawler.images")

# 禁止下载的 URL 模式
BLOCKED_PATTERNS = [
    "data:image",
    "placeholder",
    "loading",
    "logo",
    "pixel",
    "tracking",
    ".svg",  # SVG 可能是 logo
    "blank",
    "spacer",
    "1x1",
    "base64",
]

# 有效图片大小下限（字节）
MIN_IMAGE_SIZE = 1024  # 1KB

# 有效 Content-Type
VALID_CONTENT_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/avif",
}

# 允许的扩展名
VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif"}

# 魔数签名 -> 扩展名映射
MAGIC_TO_EXT = {
    b'\xff\xd8\xff': '.jpg',       # JPEG
    b'\x89PNG': '.png',             # PNG
    b'RIFF': '.webp',               # WebP (RIFF....WEBP)
    b'\x00\x00\x00\x1cftypavif': '.avif',  # AVIF
}


class ImageDownloader:
    """图片下载器，支持并发控制和缓存检查"""

    def __init__(
        self,
        output_dir: str,
        max_workers: int = 8,
        max_in_flight: int = 16,
        timeout: int = 30,
    ):
        self.output_dir = os.path.join(output_dir, "images")
        os.makedirs(self.output_dir, exist_ok=True)

        self.max_workers = max_workers
        self.semaphore = Semaphore(max_in_flight)
        self.timeout = timeout

        # 已下载的 URL 缓存（避免重复下载）
        self._downloaded_urls: set = set()
        self._downloaded_hashes: set = set()

        # HTTP 客户端（复用连接）
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

    def download_for_product(self, product: ProductItem) -> Optional[str]:
        """为单个商品下载主图，返回本地路径"""
        if not product.main_image_url:
            return None

        url = product.main_image_url

        # 检查是否应该下载
        if not self._should_download(url):
            return None

        # 检查是否已下载过
        if url in self._downloaded_urls:
            return self._get_local_path(url)

        # 生成文件名
        filename = self._generate_filename(product, url)

        with self.semaphore:
            local_path = self._do_download(url, filename)

        if local_path:
            self._downloaded_urls.add(url)
            self.success_count += 1
        else:
            self.fail_count += 1

        return local_path

    def download_batch(self, products: list[ProductItem]) -> dict:
        """批量下载图片，返回 {product_id: local_path}"""
        result = {}
        unique_urls = {}

        # 先去重：按 URL 去重
        for p in products:
            if p.main_image_url and p.main_image_url not in unique_urls:
                unique_urls[p.main_image_url] = []

        # 使用线程池并发下载
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}
            for url in unique_urls:
                future = executor.submit(
                    self._download_single, url
                )
                futures[future] = url

            for future in as_completed(futures):
                url = futures[future]
                try:
                    local_path = future.result()
                    if local_path:
                        unique_urls[url] = local_path
                        self._downloaded_urls.add(url)
                        self.success_count += 1
                    else:
                        self.fail_count += 1
                except Exception as e:
                    self.fail_count += 1
                    self.errors.append({
                        "url": url,
                        "error_type": type(e).__name__,
                        "error_detail": str(e)[:500],
                    })
                    logger.warning(f"图片下载失败 [{url[:80]}]: {e}")

        # 将下载路径映射回商品
        for p in products:
            if p.main_image_url and p.main_image_url in unique_urls:
                result[p.product_id or p.pnk] = unique_urls[p.main_image_url]

        return result

    def _download_single(self, url: str) -> Optional[str]:
        """下载单个 URL 的图片"""
        try:
            filename = self._generate_filename_from_url(url)
            return self._do_download(url, filename)
        except Exception as e:
            logger.debug(f"下载失败 [{url[:80]}]: {e}")
            return None

    def _do_download(self, url: str, filename: str) -> Optional[str]:
        """执行实际下载"""
        local_path = os.path.join(self.output_dir, filename)

        # 检查文件是否已存在
        if os.path.exists(local_path) and os.path.getsize(local_path) > MIN_IMAGE_SIZE:
            return local_path

        try:
            resp = self._client.get(url)
            if resp.status_code != 200:
                raise Exception(f"HTTP {resp.status_code}")

            # 检查大小
            content = resp.content
            if len(content) < MIN_IMAGE_SIZE:
                raise Exception(f"图片过小: {len(content)} 字节")

            # 检查是否为 HTML 错误页面
            if content[:100].strip().startswith(b"<!") or content[:100].strip().startswith(b"<html"):
                raise Exception("响应内容为 HTML 页面，非图片")

            # 根据魔数检测实际图片格式
            detected_ext = None
            for magic, ext in MAGIC_TO_EXT.items():
                if content[:len(magic)] == magic:
                    detected_ext = ext
                    break

            if detected_ext is None:
                # 尝试通过 content-type 检测
                content_type = resp.headers.get("content-type", "").split(";")[0].strip()
                if content_type in VALID_CONTENT_TYPES:
                    ext_map = {
                        "image/jpeg": ".jpg", "image/jpg": ".jpg",
                        "image/png": ".png", "image/webp": ".webp",
                        "image/avif": ".avif",
                    }
                    detected_ext = ext_map.get(content_type, ".jpg")
                else:
                    raise Exception(f"无法识别图片格式")

            # 使用检测到的扩展名
            filename = filename.rsplit(".", 1)[0] + detected_ext
            local_path = os.path.join(self.output_dir, filename)

            # 写入文件
            with open(local_path, "wb") as f:
                f.write(content)

            return local_path

        except Exception as e:
            logger.debug(f"下载失败 [{url[:80]}]: {e}")
            return None

    def _should_download(self, url: str) -> bool:
        """检查 URL 是否应该下载"""
        if not url:
            return False
        url_lower = url.lower()
        for pattern in BLOCKED_PATTERNS:
            if pattern in url_lower:
                return False
        return True

    def _generate_filename(self, product: ProductItem, url: str) -> str:
        """生成稳定的文件名"""
        # 优先使用 PNK
        base = product.pnk or product.product_id or hashlib.md5(url.encode()).hexdigest()[:12]
        base = sanitize_filename(base)
        ext = os.path.splitext(url.split("?")[0])[1]
        if ext.lower() not in VALID_EXTENSIONS:
            ext = ".jpg"
        return f"{base}{ext}"

    def _generate_filename_from_url(self, url: str) -> str:
        """从 URL 生成文件名"""
        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        ext = os.path.splitext(url.split("?")[0])[1]
        if ext.lower() not in VALID_EXTENSIONS:
            ext = ".jpg"
        return f"{url_hash}{ext}"

    def _get_local_path(self, url: str) -> Optional[str]:
        """根据 URL 获取已有的本地路径"""
        filename = self._generate_filename_from_url(url)
        local_path = os.path.join(self.output_dir, filename)
        if os.path.exists(local_path):
            return local_path
        return None

    def close(self):
        """关闭 HTTP 客户端"""
        self._client.close()

    def get_stats(self) -> dict:
        """获取下载统计"""
        return {
            "success": self.success_count,
            "failed": self.fail_count,
            "errors": self.errors,
        }
