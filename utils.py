"""
工具函数 V2.1.2: 日志、价格解析、URL处理、TXT配置加载、WAF检测、产品键、RunStatus
"""
import re, os, json, csv, logging, sys, hashlib
from datetime import datetime
from enum import Enum
from typing import Optional
from urllib.parse import urlparse


# ============================================================
# 退出码定义
# ============================================================
EXIT_SUCCESS = 0; EXIT_CONFIG_ERROR = 1; EXIT_NETWORK_ERROR = 2
EXIT_CAPTCHA = 3; EXIT_INTERRUPT = 130


# ============================================================
# 统一运行状态 (从 checkpoint.py 迁移, 去掉断点相关状态)
# ============================================================
class RunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    NETWORK_ERROR = "network_error"
    WAF_BLOCKED = "waf_blocked"
    INTERRUPTED = "interrupted"

    @property
    def exit_code(self) -> int:
        return {RunStatus.COMPLETED: 0, RunStatus.RUNNING: 0,
                RunStatus.NETWORK_ERROR: 2, RunStatus.WAF_BLOCKED: 3,
                RunStatus.INTERRUPTED: 130}[self]

    @property
    def is_stopped(self) -> bool:
        return self != RunStatus.RUNNING


# ============================================================
# 自定义异常
# ============================================================
class WafBlockError(Exception):
    """检测到WAF/验证码/访问限制, 必须人工处理后重新运行"""
    def __init__(self, status_code: int, category: str, page: int, url: str,
                 block_type: str, evidence: str):
        self.status_code = status_code
        self.category = category
        self.page = page
        self.url = url
        self.block_type = block_type
        self.evidence = evidence
        super().__init__(f"[{block_type}] HTTP {status_code} at {url}")


# 保持旧名兼容
CaptchaRequiredError = WafBlockError


# ============================================================
# TXT 类目配置加载 (使用 urllib.parse)
# ============================================================
def load_txt_categories(filepath: str) -> list[dict]:
    """
    从 categories.txt 加载类目配置。
    每行一个URL，忽略空行和 # 开头的注释行。
    自动从URL路径生成类目名称。
    使用 urllib.parse 进行URL校验。
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"配置文件不存在: {filepath}")

    lines = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line_num, raw_line in enumerate(f, 1):
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#"):
                continue
            lines.append((line_num, raw_line.rstrip("\n\r"), line))

    if not lines:
        raise ValueError(f"配置文件中没有有效的类目URL ({filepath})")

    errors = []
    categories = []
    seen_urls = set()

    for line_num, raw, url in lines:
        # 使用 urllib.parse 解析
        try:
            parsed = urlparse(url)
        except Exception:
            errors.append((line_num, raw, "URL 无法解析"))
            continue

        # 协议检查
        if parsed.scheme not in ("http", "https"):
            errors.append((line_num, raw, f"不支持的协议: {parsed.scheme}，仅支持 http/https"))
            continue

        # 域名检查
        hostname = (parsed.hostname or "").lower()
        valid_hosts = ("www.emag.ro", "emag.ro")
        if hostname not in valid_hosts:
            errors.append((line_num, raw, f"域名必须是 www.emag.ro 或 emag.ro，当前: {hostname}"))
            continue

        # 路径检查: 必须是 /.../c 格式，拒绝 /pd/
        path = parsed.path.rstrip("/")
        if "/pd/" in path:
            errors.append((line_num, raw, "URL 是商品详情页 /pd/，不是类目 /c 路径"))
            continue

        # 必须以 /c 结尾 (例如 /mouse/c 或 /laptop/accesorii-laptop/c)
        if not path.endswith("/c"):
            errors.append((line_num, raw, f"URL 路径必须以 /c 结尾 (类目路径)，当前路径: {path}"))
            continue

        # 去重 (忽略尾部斜杠、大小写)
        normalized = (hostname + path).lower()
        if normalized in seen_urls:
            print(f"[警告] 第{line_num}行 URL 重复，已跳过: {url}", file=sys.stderr)
            continue
        seen_urls.add(normalized)

        # 从URL路径生成类目名称
        name = _category_name_from_url(url, len(categories) + 1)
        categories.append({"name": name, "url": url, "enabled": True})

    if errors:
        print("[错误] 类目配置文件存在以下问题:", file=sys.stderr)
        for line_num, raw, reason in errors:
            print(f"  第{line_num}行: {raw.strip()}", file=sys.stderr)
            print(f"    原因: {reason}", file=sys.stderr)
        raise ValueError(f"配置文件有 {len(errors)} 个错误，已全部列出。请修正后重新运行。")

    if not categories:
        raise ValueError("配置文件中没有有效的类目URL")

    return categories


def _category_name_from_url(url: str, index: int) -> str:
    """从 eMAG 类目 URL 路径中提取名称"""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    parts = path.split("/")
    # 找 /c 前面的部分: /mouse/c → mouse, /laptop/accesorii/c → accesorii
    for i, part in enumerate(parts):
        if part == "c" and i > 0:
            name = parts[i-1]
            return name.replace("-", " ").title()
    return f"Category_{index:03d}"


# ============================================================
# WAF / Captcha / 访问限制 检测 (V2.0.2 统一处理)
# ============================================================
def detect_waf_block(html: str, http_status: int, url: str,
                     category: str = "", page_num: int = 0,
                     soup=None) -> Optional[WafBlockError]:
    """HTTP 403/429/511无条件阻断。HTTP 200+soup: 强WAF标题优先。soup=None时仅检查标题。"""
    if http_status in (403, 429, 511):
        block_names = {403: "HTTP_403_FORBIDDEN", 429: "HTTP_429_RATE_LIMIT", 511: "HTTP_511_WAF"}
        ep = [f"HTTP {http_status}"]
        if html:
            hl = html.lower()
            if "captcha" in hl: ep.append("captcha in body")
            if "waf" in hl: ep.append("WAF in body")
        return WafBlockError(http_status, category, page_num, url, block_names.get(http_status, f"HTTP_{http_status}"), "; ".join(ep))
    if not html:
        return None

    # HTTP 200: 强WAF证据(标题+正文+可见控件)
    if soup is not None:
        title = _get_page_title_soup(soup)
        if _is_strong_waf_title(title):
            return WafBlockError(http_status, category, page_num, url, "STRONG_WAF_EVIDENCE", f"Strong WAF title: {title[:60]}")
        # S0-2: 检查正文中的强WAF证据(可见文本+控件)
        body_evidence = _soup_strong_waf_body(soup)
        if body_evidence:
            return WafBlockError(http_status, category, page_num, url, "STRONG_WAF_EVIDENCE", f"Strong WAF body: {body_evidence[:80]}")
    else:
        try:
            from bs4 import BeautifulSoup
            s2 = BeautifulSoup(html, "lxml")
            title = _get_page_title_soup(s2)
            if _is_strong_waf_title(title):
                return WafBlockError(http_status, category, page_num, url, "STRONG_WAF_EVIDENCE", f"Strong WAF title: {title[:60]}")
            body_evidence = _soup_strong_waf_body(s2)
            if body_evidence:
                return WafBlockError(http_status, category, page_num, url, "STRONG_WAF_EVIDENCE", f"Strong WAF body: {body_evidence[:80]}")
        except Exception:
            if "emag captcha" in html.lower():
                return WafBlockError(http_status, category, page_num, url, "STRONG_WAF_EVIDENCE", "eMAG Captcha title (text)")
            hl = html.lower()
            strong_body = ["please verify you are human", "verify you are human",
                           "human verification", "trafic neobisnuit"]
            for sb in strong_body:
                if sb in hl:
                    return WafBlockError(http_status, category, page_num, url, "STRONG_WAF_EVIDENCE", f"Strong WAF body text: {sb}")
    return None


def _soup_strong_waf_body(soup) -> str:
    """检查soup中可见正文是否包含强WAF证据"""
    # 提取可见文本
    visible_texts = []
    for el in soup.select("body *"):
        if not _is_visible_element(el):
            continue
        txt = el.get_text(strip=True)
        if txt:
            visible_texts.append(txt.lower())
    body_lower = " ".join(visible_texts)
    # 强WAF正文标记
    strong_markers = [
        "please verify you are human", "verify you are human",
        "human verification", "am detectat trafic neobisnuit",
        "trafic neobisnuit", "access denied",
    ]
    for m in strong_markers:
        if m in body_lower:
            return m
    # 可见captcha控件
    captcha_containers = soup.select(
        "#captcha, .captcha, [class*=captcha], [id*=captcha], "
        "#challenge, .challenge, [class*=challenge]"
    )
    for c in captcha_containers:
        if _is_visible_element(c):
            return f"visible captcha/challenge container: {c.get('id','') or c.get('class','')}"
    return ""


def _is_visible_element(el) -> bool:
    """判断元素是否可见(排除隐藏节点)"""
    if not hasattr(el, 'get'): return True
    style = (el.get("style") or "").lower()
    if "display:none" in style or "display: none" in style: return False
    if "visibility:hidden" in style or "visibility: hidden" in style: return False
    if el.get("hidden") is not None: return False
    if el.get("aria-hidden") == "true": return False
    return True


def _is_strong_waf_title(title: str) -> bool:
    """页面标题是否为强验证码证据"""
    if not title: return False
    tl = title.lower().strip()
    if "emag captcha" in tl: return True
    if "captcha" in tl and ("verify" in tl or "human" in tl): return True
    if "human verification" in tl: return True
    return False


def _get_page_title_soup(soup) -> str:
    t = soup.select_one("title")
    return t.get_text(strip=True) if t else ""


def _page_has_valid_product_soup(soup) -> bool:
    """统一商品卡片判断: 使用已解析soup检查有效商品卡片"""
    from bs4 import Tag
    cards = soup.select(".card-item.card-standard.js-product-data")
    if not cards:
        cards = soup.select("[data-product-id]")
    for card in cards:
        if not isinstance(card, Tag): continue
        pid = (card.get("data-product-id") or "").strip()
        title = (card.get("data-name") or "").strip()
        url = (card.get("data-url") or "").strip()
        if pid and title and url and "/pd/" in url:
            return True
    return False


# 保持旧函数名兼容
detect_captcha = detect_waf_block


# ============================================================
# 产品唯一键
# ============================================================
def get_product_key(product: dict) -> str:
    """
    统一产品唯一键函数。
    优先级: pnk > product_id > 规范化product_url > 图片URL哈希 > 标题哈希
    """
    pnk = (product.get("pnk") or "").strip()
    if pnk:
        return f"pnk:{pnk}"

    pid = (product.get("product_id") or "").strip()
    if pid:
        return f"pid:{pid}"

    url = (product.get("product_url") or "").strip()
    if url:
        normalized = url.split("?")[0].rstrip("/").lower()
        return f"url:{hashlib.md5(normalized.encode()).hexdigest()[:12]}"

    img_url = (product.get("main_image_url") or "").strip()
    if img_url:
        return f"img:{hashlib.md5(img_url.encode()).hexdigest()[:12]}"

    title = (product.get("title") or "").strip()
    return f"title:{hashlib.md5(title.encode()).hexdigest()[:12]}"


# ============================================================
# 日志
# ============================================================
def setup_logging(log_dir: str, level: str = "INFO") -> logging.Logger:
    """配置日志：同时输出到控制台和文件"""
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger("emag_crawler")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if logger.handlers:
        return logger

    console_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
    )
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(console_fmt)
    logger.addHandler(console)

    log_file = os.path.join(log_dir, "run.log")
    file_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_fmt)
    logger.addHandler(file_handler)

    return logger


# ============================================================
# 罗马尼亚价格解析
# ============================================================
def parse_romanian_price(text: str) -> Optional[float]:
    """
    解析罗马尼亚价格格式。
    例: "45,99Lei" → 45.99, "1.234,56 Lei" → 1234.56
    """
    if not text or not text.strip():
        return None

    text = text.strip()
    text = re.sub(r'(?i)\s*lei\s*', '', text)
    text = re.sub(r'(?i)\s*RON\s*', '', text)
    text = re.sub(r'(?i)\s*PRP\s*:\s*', '', text)
    text = text.strip()

    if not text:
        return None

    if re.match(r'^(\d{1,3}(\.\d{3})+),\d{1,2}$', text):
        text = text.replace('.', '').replace(',', '.')
    elif re.match(r'^\d+,\d{1,2}$', text):
        text = text.replace(',', '.')
    elif re.match(r'^\d+$', text):
        pass
    else:
        cleaned = text.replace('.', '').replace(',', '.').strip()
        try:
            return round(float(cleaned), 2)
        except (ValueError, TypeError):
            return None

    try:
        return round(float(text), 2)
    except (ValueError, TypeError):
        return None


# ============================================================
# 文件和路径工具
# ============================================================
def make_output_dir(base: str = "output") -> str:
    """创建带时间戳的输出目录"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(base, timestamp)
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "images"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "logs"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "diagnostics"), exist_ok=True)
    return out_dir


def sanitize_filename(name: str) -> str:
    """清理文件名，确保 Windows 兼容"""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    if len(name) > 200:
        name = name[:200]
    return name.strip()


def write_errors_csv(filepath: str, error_data: dict, write_header: bool = False,
                     fieldnames: list = None) -> None:
    """追加写入错误记录"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    file_exists = os.path.exists(filepath) and os.path.getsize(filepath) > 0
    names = fieldnames or list(error_data.keys())
    with open(filepath, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=names, extrasaction="ignore")
        if not file_exists or write_header:
            writer.writeheader()
        writer.writerow(error_data)


def ensure_errors_csv(filepath: str, fieldnames: list = None) -> None:
    """确保 errors.csv 存在（至少包含表头）"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
        headers = fieldnames or ["时间", "类目", "页码", "URL", "错误类型", "HTTP状态码", "重试次数", "错误详情"]
        with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)


def get_version() -> str:
    """读取版本号"""
    version_file = os.path.join(os.path.dirname(__file__), "VERSION")
    if os.path.exists(version_file):
        with open(version_file, "r") as f:
            return f.read().strip()
    return "2.0.2"


def write_atomic_json(filepath: str, data) -> None:
    """原子写入JSON文件: 先写 .tmp, 成功后 os.replace"""
    tmp_path = filepath + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, filepath)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
