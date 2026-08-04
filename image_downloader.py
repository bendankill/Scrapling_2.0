"""
图片下载模块 V2.1.3-fix: PNK多文件, 真实图片验证, 线程安全命名, 原子写入
"""
import os, hashlib, logging, time, re, shutil, tempfile
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from threading import Semaphore, Lock
from typing import Optional
import httpx
from utils import get_product_key

logger = logging.getLogger("emag_crawler.images")

BLOCKED_PATTERNS = ["data:image","placeholder","loading","logo","pixel","tracking",".svg","blank","spacer","1x1","base64"]
MAGIC_TO_EXT = {b'\xff\xd8\xff':'.jpg', b'\x89PNG':'.png', b'RIFF':'.webp'}
PROGRESS_INTERVAL_SECONDS = 60
NON_RETRYABLE_ERRORS = frozenset({"HTML_RESPONSE","UNKNOWN_FORMAT","BLOCKED_PATTERN","CORRUPT_IMAGE"})
_INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Windows 保留名
_WIN_RESERVED = frozenset({n.upper() for n in
    "CON,PRN,AUX,NUL,COM1,COM2,COM3,COM4,COM5,COM6,COM7,COM8,COM9,LPT1,LPT2,LPT3,LPT4,LPT5,LPT6,LPT7,LPT8,LPT9".split(",")})

class ImageDownloadError(Exception):
    def __init__(self, image_url: str, error_type: str, http_status: int = 0,
                 detail: str = "", product_keys: list = None):
        self.image_url = image_url; self.error_type = error_type
        self.http_status = http_status; self.detail = detail
        self.product_keys = product_keys or []
        super().__init__(f"[{error_type}] {image_url}: {detail}")


def _safe_pnk_name(raw: str, max_len: int = 120) -> str:
    """Windows安全PNK文件名处理"""
    s = _INVALID_CHARS_RE.sub('_', raw).rstrip('. ')
    if not s: return None
    if s.upper() in _WIN_RESERVED: s = f"_{s}"
    if len(s) > max_len: s = s[:max_len].rstrip('. ')
    return s


def _detect_format(content: bytes, content_type: str = "") -> Optional[str]:
    """检测图片真实格式, 返回扩展名或None"""
    # JPEG: FFD8FF
    if len(content) >= 3 and content[:3] == b'\xff\xd8\xff':
        return '.jpg'
    # PNG: 8字节签名
    if len(content) >= 8 and content[:8] == b'\x89PNG\r\n\x1a\n':
        return '.png'
    # WebP: RIFF....WEBP
    if len(content) >= 12 and content[:4] == b'RIFF' and content[8:12] == b'WEBP':
        return '.webp'
    # AVIF: ISO BMFF ftyp box with avif brand
    if len(content) >= 12 and content[4:8] == b'ftyp':
        # 检查 brands
        brands = content[8:].split(b'\x00\x00\x00')[0] if len(content) > 12 else b''
        brand_str = brands.decode('ascii', errors='ignore')
        if 'avif' in brand_str or 'avis' in brand_str:
            return '.avif'
    # Content-Type fallback
    ct = content_type.split(";")[0].strip().lower()
    ext_map = {"image/jpeg":".jpg","image/jpg":".jpg","image/png":".png","image/webp":".webp","image/avif":".avif"}
    return ext_map.get(ct)


def _verify_image(content: bytes, ext: str) -> bool:
    """使用Pillow严格验证所有图片格式 (JPEG/PNG/WebP/AVIF)"""
    try:
        from io import BytesIO
        from PIL import Image
        Image.open(BytesIO(content)).verify()
        return True
    except Exception:
        return False


class ImageDownloader:
    def __init__(self, output_dir: str, max_workers: int = 8,
                 max_in_flight: int = 8, global_semaphore: Optional[Semaphore] = None,
                 timeout: int = 30):
        self.output_dir = os.path.join(output_dir, "images")
        os.makedirs(self.output_dir, exist_ok=True)
        self.max_workers = max_workers
        self.max_in_flight = max_in_flight
        self.semaphore = global_semaphore or Semaphore(max_in_flight)
        self.timeout = timeout
        self._cache_lock = Lock()
        self._url_path_cache: dict[str, str] = {}  # URL → (first_local_path, ext)
        # 商品路径缓存: composite_key → final_path (跨批次复用)
        self._product_path_cache: dict[str, str] = {}
        self._prod_cache_lock = Lock()
        # 线程安全: 已预留文件名集合 + PNK计数
        self._reserved_names: set = set()
        self._pnk_count: dict[str, int] = {}
        self._name_lock = Lock()
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout), follow_redirects=True, max_redirects=5,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                     "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"})
        self.success_count = 0; self.fail_count = 0
        self.missing_pnk_count = 0  # 缺少PNK的商品数
        self.errors: list[dict] = []; self._errors_lock = Lock()
        self.progress_interval = PROGRESS_INTERVAL_SECONDS

    def download_batch(self, products: list[dict]) -> dict[str, str]:
        # 按URL分组: url → [{product_dict}, ...]
        url_to_prods: dict[str, list[dict]] = {}
        result: dict[str, str] = {}  # composite_key → local_path
        for p in products:
            img_url = (p.get("main_image_url") or "").strip()
            key = get_product_key(p)
            if not img_url or not key: continue
            ck = self._composite_key(p)
            # 检查商品路径缓存: 跨批次复用
            with self._prod_cache_lock:
                if ck in self._product_path_cache:
                    cached_path = self._product_path_cache[ck]
                    if os.path.exists(cached_path):
                        result[ck] = cached_path
                        continue
            if img_url not in url_to_prods: url_to_prods[img_url] = []
            url_to_prods[img_url].append(p)

        total = len(url_to_prods)
        unique_prod_count = sum(len(v) for v in url_to_prods.values()) + len(result)
        if total == 0 and result:
            logger.info(f"全部 {len(result)} 个商品图片已缓存，无需下载")
            return result
        logger.info(f"准备下载 {total} 个唯一URL (对应 {unique_prod_count} 个商品)")

        url_items = list(url_to_prods.items())
        in_flight_limit = max(1, min(self.max_in_flight, self.max_workers * 2))
        in_flight: dict = {}; next_idx = 0
        batch_start = time.perf_counter(); last_progress = batch_start
        batch_success_start = self.success_count; batch_fail_start = self.fail_count

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            while next_idx < len(url_items) and len(in_flight) < in_flight_limit:
                url, prods = url_items[next_idx]
                fut = executor.submit(self._download_url, url, prods)
                in_flight[fut] = (url, prods)
                next_idx += 1

            while in_flight:
                done, _ = wait(in_flight, timeout=self.progress_interval, return_when=FIRST_COMPLETED)
                for fut in done:
                    url, prods = in_flight.pop(fut)
                    try:
                        paths = fut.result()  # {composite_key: local_path}
                        if paths:
                            for k, v in paths.items(): result[k] = v
                            self.success_count += 1
                        else:
                            self.fail_count += 1
                    except ImageDownloadError as e:
                        self.fail_count += 1
                        with self._errors_lock:
                            self.errors.append({
                                "image_url": e.image_url, "error_type": e.error_type,
                                "http_status": e.http_status, "error_detail": e.detail,
                                "product_key": ", ".join(e.product_keys) if e.product_keys else "",
                                "category": prods[0].get("category_name", "") if prods else "",
                                "page": prods[0].get("page_number", 0) if prods else 0, "url": e.image_url})
                        if e.error_type not in NON_RETRYABLE_ERRORS:
                            logger.warning(f"图片下载失败 [{url[:80]}]: {e}")
                    except Exception as e:
                        self.fail_count += 1
                        with self._errors_lock:
                            self.errors.append({
                                "image_url": url, "error_type": type(e).__name__,
                                "http_status": 0, "error_detail": str(e)[:500],
                                "product_key": ", ".join(get_product_key(p) for p in prods),
                                "category": "", "page": 0, "url": url})
                        logger.warning(f"图片下载失败 [{url[:80]}]: {e}")

                while next_idx < len(url_items) and len(in_flight) < in_flight_limit:
                    url, prods = url_items[next_idx]
                    fut = executor.submit(self._download_url, url, prods)
                    in_flight[fut] = (url, prods)
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
                    logger.info(f"图片进度：{batch_done}/{total}"
                        f"（成功{batch_success}，失败{batch_failed}）"
                        f"速率：{avg_rate:.1f}张/秒，已耗时：{h:02d}:{m:02d}:{s:04.1f}")
                    if not all_done: last_progress = now

        if self.missing_pnk_count > 0:
            logger.info(f"本次 {self.missing_pnk_count} 个商品缺少PNK，已使用兜底文件名")
        return result

    def _composite_key(self, product: dict) -> str:
        """(product_key, image_url) 复合键, 可区分同PNK不同URL"""
        pk = get_product_key(product)
        url = (product.get("main_image_url") or "").strip()
        return f"{pk}|{hashlib.md5(url.encode()).hexdigest()[:8]}"

    def _download_url(self, url: str, products: list[dict]) -> dict[str, str]:
        """下载一个URL, 为每个商品生成PNK文件。返回 {composite_key: local_path}"""
        if not self._should_download(url):
            raise ImageDownloadError(url, "BLOCKED_PATTERN", detail="URL matches blocked pattern",
                                    product_keys=[get_product_key(p) for p in products])
        with self._cache_lock:
            if url in self._url_path_cache:
                cached = self._url_path_cache[url]  # (first_path, ext)
                first_path, ext = cached if isinstance(cached, tuple) else (cached, os.path.splitext(cached)[1])
                # 去重: 相同 composite_key 只处理一次
                seen = set()
                result = {}
                for p in products:
                    ck = self._composite_key(p)
                    if ck in seen: continue
                    seen.add(ck)
                    # 使用线程安全分配获取正式文件名
                    base = self._make_pnk_basename(p)
                    pnk_file = self._alloc_pnk_filename(
                        (p.get("pnk") or "").strip(), p, ext)
                    pnk_path = os.path.join(self.output_dir, pnk_file)
                    if os.path.exists(pnk_path):
                        result[ck] = pnk_path
                        continue
                    self._link_or_copy(first_path, pnk_path)
                    if os.path.exists(pnk_path): result[ck] = pnk_path
                # 写入商品路径缓存
                with self._prod_cache_lock:
                    for ck, path in result.items():
                        if os.path.exists(path):
                            self._product_path_cache[ck] = path
                return result

        with self.semaphore:
            return self._do_download_url(url, products)

    def _do_download_url(self, url: str, products: list[dict]) -> dict[str, str]:
        """实际执行HTTP下载+验证+PNK文件生成"""
        try:
            resp = self._client.get(url)
        except httpx.TimeoutException:
            raise ImageDownloadError(url, "TIMEOUT", detail="Request timed out",
                                    product_keys=[get_product_key(p) for p in products])
        except httpx.ConnectError as e:
            raise ImageDownloadError(url, "CONNECT_ERROR", detail=str(e)[:200],
                                    product_keys=[get_product_key(p) for p in products])
        except Exception as e:
            raise ImageDownloadError(url, "NETWORK_ERROR", detail=str(e)[:200],
                                    product_keys=[get_product_key(p) for p in products])

        if resp.status_code != 200:
            raise ImageDownloadError(url, f"HTTP_{resp.status_code}", http_status=resp.status_code,
                                    detail=f"HTTP {resp.status_code}",
                                    product_keys=[get_product_key(p) for p in products])

        content = resp.content
        if not content:
            raise ImageDownloadError(url, "EMPTY_RESPONSE", http_status=200,
                                    detail="Empty response body",
                                    product_keys=[get_product_key(p) for p in products])

        stripped = content[:200].strip()
        if stripped.startswith(b"<!") or stripped.startswith(b"<html") or stripped.startswith(b"<HTML"):
            raise ImageDownloadError(url, "HTML_RESPONSE", http_status=200,
                                    detail="Response is HTML, not an image",
                                    product_keys=[get_product_key(p) for p in products])

        ct = resp.headers.get("content-type", "")
        detected_ext = _detect_format(content, ct)
        if detected_ext is None:
            raise ImageDownloadError(url, "UNKNOWN_FORMAT", http_status=200,
                                    detail=f"Cannot identify format: content-type={ct}",
                                    product_keys=[get_product_key(p) for p in products])

        # 验证图片可打开
        if not _verify_image(content, detected_ext):
            raise ImageDownloadError(url, "CORRUPT_IMAGE", http_status=200,
                                    detail=f"Image failed verification",
                                    product_keys=[get_product_key(p) for p in products])

        # 按 composite_key 去重后生成PNK文件
        result = {}
        first_path = None
        seen_ck = set()
        for p in products:
            pnk = (p.get("pnk") or "").strip()
            if not pnk: self.missing_pnk_count += 1
            ck = self._composite_key(p)
            if ck in seen_ck: continue  # 同PNK+同URL: 复用已有路径
            seen_ck.add(ck)

            pnk_file = self._alloc_pnk_filename(pnk, p, detected_ext)
            pnk_path = os.path.join(self.output_dir, pnk_file)

            if first_path is None:
                if not os.path.exists(pnk_path):
                    self._atomic_write(pnk_path, content)
                first_path = pnk_path
                result[ck] = pnk_path
            else:
                if not os.path.exists(pnk_path):
                    self._link_or_copy(first_path, pnk_path)
                if os.path.exists(pnk_path):
                    result[ck] = pnk_path

        # 缓存: 保存URL缓存 + 商品路径缓存
        if first_path:
            with self._cache_lock:
                self._url_path_cache[url] = (first_path, detected_ext)
        # 将成功路径写入商品路径缓存 (跨批次复用)
        with self._prod_cache_lock:
            for ck, path in result.items():
                if os.path.exists(path):
                    self._product_path_cache[ck] = path
        return result

    def _make_pnk_basename(self, product: dict) -> str:
        """从商品信息生成PNK基础名(不含扩展名)"""
        pnk = (product.get("pnk") or "").strip()
        if pnk:
            safe = _safe_pnk_name(pnk)
            if safe: return safe
        pid = (product.get("product_id") or "").strip()
        if pid:
            safe = _safe_pnk_name(pid)
            if safe: return f"NO_PNK_{safe}"
        url = (product.get("main_image_url") or "").strip()
        h = hashlib.md5(url.encode()).hexdigest()[:12]
        return f"NO_PNK_{h}"

    def _alloc_pnk_filename(self, pnk: str, product: dict, ext: str) -> str:
        """线程安全分配PNK文件名。在锁内完成: 检查+预留+返回"""
        base = _safe_pnk_name(pnk) if pnk else None
        if not base:
            pid = (product.get("product_id") or "").strip()
            if pid: base = f"NO_PNK_{_safe_pnk_name(pid)}"
            else:
                h = hashlib.md5((product.get("main_image_url") or "").encode()).hexdigest()[:12]
                base = f"NO_PNK_{h}"
        with self._name_lock:
            # 尝试 base.ext
            candidate = f"{base}{ext}"
            if candidate not in self._reserved_names and not os.path.exists(os.path.join(self.output_dir, candidate)):
                self._reserved_names.add(candidate)
                return candidate
            # 冲突: base_N.ext
            cnt = self._pnk_count.get(base, 1)
            while True:
                cnt += 1
                candidate = f"{base}_{cnt}{ext}"
                if candidate not in self._reserved_names and not os.path.exists(os.path.join(self.output_dir, candidate)):
                    self._reserved_names.add(candidate)
                    self._pnk_count[base] = cnt
                    return candidate

    @staticmethod
    def _atomic_write(path: str, content: bytes):
        """原子写入: tmp → os.replace"""
        tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, 'wb') as f:
                f.write(content)
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    @staticmethod
    def _link_or_copy(src: str, dst: str):
        """硬链接优先, 失败则复制"""
        try:
            os.link(src, dst)
        except OSError:
            shutil.copyfile(src, dst)

    def _should_download(self, url: str) -> bool:
        if not url: return False
        for p in BLOCKED_PATTERNS:
            if p in url.lower(): return False
        return True

    def close(self): self._client.close()

    def get_stats(self) -> dict:
        with self._errors_lock:
            return {"success": self.success_count, "failed": self.fail_count,
                    "missing_pnk": self.missing_pnk_count, "errors": list(self.errors)}
