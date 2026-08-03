"""
工具函数：日志、价格解析、URL处理、TXT配置加载、Captcha检测、产品键
"""
import re
import os
import json
import csv
import logging
import sys
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional


# ============================================================
# 退出码定义
# ============================================================
EXIT_SUCCESS = 0           # 成功且抓到商品
EXIT_CONFIG_ERROR = 1      # 配置或参数错误
EXIT_NETWORK_ERROR = 2     # 网络、解析或全部页面失败
EXIT_CAPTCHA = 3           # 检测到验证码/WAF，需要人工处理
EXIT_INTERRUPT = 130       # 用户中断 (Ctrl+C)


# ============================================================
# 自定义异常
# ============================================================
class CaptchaRequiredError(Exception):
    """检测到验证码或WAF人工验证, 必须人工处理后重新运行"""
    def __init__(self, status_code: int, category: str, page: int, url: str,
                 captcha_type: str, evidence: str):
        self.status_code = status_code
        self.category = category
        self.page = page
        self.url = url
        self.captcha_type = captcha_type
        self.evidence = evidence
        super().__init__(f"[{captcha_type}] HTTP {status_code} at {url}")


# ============================================================
# TXT 类目配置加载
# ============================================================
def load_txt_categories(filepath: str) -> list[dict]:
    """
    从 categories.txt 加载类目配置。
    每行一个URL，忽略空行和 # 开头的注释行。
    自动从URL路径生成类目名称。
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
        # 验证协议和域名
        if not (url.startswith("https://www.emag.ro/") or url.startswith("https://emag.ro/")):
            errors.append((line_num, raw, "URL 必须是 https://www.emag.ro/ 或 https://emag.ro/ 开头"))
            continue

        # 验证是 /c 类目路径, 拒绝 /pd/ 商品详情页
        if "/pd/" in url:
            errors.append((line_num, raw, "URL 是商品详情页 /pd/，不是类目 /c 路径"))
            continue

        if "/c" not in url.split("?")[0]:
            errors.append((line_num, raw, "URL 不是有效的类目路径 (缺少 /c)"))
            continue

        # 去重
        normalized = url.lower().rstrip("/")
        if normalized in seen_urls:
            print(f"[警告] 第{line_num}行 URL 重复，已跳过: {url}", file=sys.stderr)
            continue
        seen_urls.add(normalized)

        # 从URL路径生成类目名称
        name = _category_name_from_url(url, len(categories) + 1)
        categories.append({"name": name, "url": url, "enabled": True})

    # 打印全部错误
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
    """从 eMAG 类目 URL 路径中提取名称，例如 /mouse/c → Mouse"""
    # 去掉查询参数
    path = url.split("?")[0]
    # 去掉尾部斜杠
    path = path.rstrip("/")
    # 提取路径最后一个 /c 之前的词: /mouse/c → mouse
    parts = path.split("/")
    for i, part in enumerate(parts):
        if part == "c" and i > 0:
            name = parts[i-1]
            # 转为首字母大写
            return name.replace("-", " ").title()
    return f"Category_{index:03d}"


# ============================================================
# Captcha / WAF 检测
# ============================================================
def detect_captcha(html: str, http_status: int, url: str) -> Optional[CaptchaRequiredError]:
    """
    检测是否遇到验证码或WAF人工验证。
    返回 CaptchaRequiredError 或 None（表示无验证码）。
    """
    if not html:
        return None

    html_lower = html.lower()
    html_upper = html.upper()

    # --- 1. HTTP 511 (AWS WAF Network Authentication) ---
    if http_status == 511:
        if "aws-waf-token" in html_lower or "awswaf" in html_lower or "captcha" in html_lower:
            return CaptchaRequiredError(
                511, "", 0, url, "AWS_WAF_511",
                "HTTP 511 + AWS WAF token/captcha markers found"
            )

    # --- 2. AWS WAF captcha specific markers ---
    aws_waf_markers = [
        "aws-waf-token", "awswaf-captcha", "captcha-sdk.awswaf",
        "awsWafCookieDomainList", "AwsWafCaptcha",
    ]
    aws_hits = [m for m in aws_waf_markers if m.lower() in html_lower]
    if aws_hits:
        return CaptchaRequiredError(
            http_status, "", 0, url, "AWS_WAF_CAPTCHA",
            f"AWS WAF markers: {', '.join(aws_hits[:3])}"
        )

    # --- 3. HTTP 403 with captcha page ---
    if http_status == 403:
        has_captcha_indicators = any(m in html_lower for m in [
            "captcha", "waf", "challenge", "blocked",
            "verify you are human", "are you a human",
        ])
        has_product_cards = "data-product-id" in html_lower
        if has_captcha_indicators and not has_product_cards:
            return CaptchaRequiredError(
                403, "", 0, url, "WAF_403_CHALLENGE",
                "HTTP 403 with captcha/challenge markers, no product cards"
            )

    # --- 4. CAPTCHA page markers in HTML (even with HTTP 200) ---
    captcha_title_markers = [
        "<title>emag captcha</title>",
        "<title>captcha</title>",
        "human verification",
        "access denied",
        "please verify you are human",
    ]
    has_products = "data-product-id" in html_lower

    if not has_products:
        for marker in captcha_title_markers:
            if marker.lower() in html_lower:
                return CaptchaRequiredError(
                    http_status, "", 0, url, "CAPTCHA_PAGE",
                    f"Captcha marker '{marker[:60]}' found, no products on page"
                )

        # Check for eMAG-specific captcha page
        if "emag captcha" in html_lower or ("captcha" in html_lower and "emag" in html_lower):
            return CaptchaRequiredError(
                http_status, "", 0, url, "EMAG_CAPTCHA",
                "eMAG captcha page detected, no products present"
            )

    return None


# ============================================================
# 产品唯一键
# ============================================================
def get_product_key(product: dict) -> str:
    """
    统一产品唯一键函数。
    优先级: pnk > product_id > 规范化product_url > 图片URL哈希
    必须在所有去重、图片映射、唯一商品统计中统一使用。
    """
    pnk = (product.get("pnk") or "").strip()
    if pnk:
        return f"pnk:{pnk}"

    pid = (product.get("product_id") or "").strip()
    if pid:
        return f"pid:{pid}"

    url = (product.get("product_url") or "").strip()
    if url:
        # 规范化: 去掉尾部斜杠和查询参数
        normalized = url.split("?")[0].rstrip("/").lower()
        return f"url:{hashlib.md5(normalized.encode()).hexdigest()[:12]}"

    img_url = (product.get("main_image_url") or "").strip()
    if img_url:
        return f"img:{hashlib.md5(img_url.encode()).hexdigest()[:12]}"

    # 最终兜底: 标题哈希
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

    # "1.234,56" format (thousands dot, decimal comma)
    if re.match(r'^(\d{1,3}(\.\d{3})+),\d{1,2}$', text):
        text = text.replace('.', '').replace(',', '.')
    elif re.match(r'^\d+,\d{1,2}$', text):
        text = text.replace(',', '.')
    elif re.match(r'^\d+$', text):
        pass
    else:
        # Last resort: strip dots and treat comma as decimal
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


def normalize_url(base_url: str, path: str) -> str:
    """将相对路径转为绝对 URL"""
    if path.startswith("http"):
        return path
    if path.startswith("//"):
        return "https:" + path
    if path.startswith("/"):
        match = re.match(r'(https?://[^/]+)', base_url)
        if match:
            return match.group(1) + path
    return path


def write_errors_csv(filepath: str, error_data: dict, write_header: bool = False) -> None:
    """追加写入错误记录（线程安全）"""
    file_exists = os.path.exists(filepath) and os.path.getsize(filepath) > 0
    with open(filepath, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(error_data.keys()))
        if not file_exists or write_header:
            writer.writeheader()
        writer.writerow(error_data)


def get_version() -> str:
    """读取版本号"""
    version_file = os.path.join(os.path.dirname(__file__), "VERSION")
    if os.path.exists(version_file):
        with open(version_file, "r") as f:
            return f.read().strip()
    return "2.0.1"


def write_atomic_json(filepath: str, data) -> None:
    """
    原子写入JSON文件: 先写 .tmp, 成功后 os.replace 为正式文件。
    避免程序异常时留下半个JSON文件。
    """
    tmp_path = filepath + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, filepath)
