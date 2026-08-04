"""
项目统一配置 V2.1.3-fix: categories.txt 解析 + 安全兜底值
配置优先级: 命令行显式参数 > config/categories.txt > 安全兜底值
"""
import os, sys, re
from urllib.parse import urlparse

# ============================================================
# 安全兜底值 (categories.txt 正常时不会使用)
# ============================================================
DEFAULT_PAGE_WORKERS = 1
DEFAULT_CATEGORY_WORKERS = 1
DEFAULT_MAX_IN_FLIGHT = 4
DEFAULT_IMAGE_WORKERS = 8
DEFAULT_IMAGE_MAX_IN_FLIGHT = 8
DEFAULT_IMAGES_PER_PRODUCT = 1

# 允许的配置键
_VALID_KEYS = {
    "page_workers", "category_workers", "max_in_flight",
    "image_workers", "image_max_in_flight", "images_per_product",
}
# 必须为正整数的键
_INT_KEYS = {"page_workers", "category_workers", "max_in_flight",
             "image_workers", "image_max_in_flight", "images_per_product"}


def load_config(filepath: str) -> tuple[dict, list[str]]:
    """
    从 categories.txt 解析配置和类目URL。
    返回: (config_dict, category_urls)
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"配置文件不存在: {filepath}")

    config = {}
    urls = []
    seen_keys = set()
    errors = []

    with open(filepath, "r", encoding="utf-8-sig") as f:
        for line_num, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue

            # 判断是URL还是配置项
            if line.startswith("http://") or line.startswith("https://"):
                parsed = urlparse(line)
                host = (parsed.hostname or "").lower()
                if host not in ("www.emag.ro", "emag.ro"):
                    errors.append((line_num, raw, "域名必须是 www.emag.ro 或 emag.ro"))
                    continue
                path = parsed.path.rstrip("/")
                if "/pd/" in path:
                    errors.append((line_num, raw, "URL 是商品详情页 /pd/，不是类目 /c 路径"))
                    continue
                if not path.endswith("/c"):
                    errors.append((line_num, raw, f"URL 路径必须以 /c 结尾"))
                    continue
                normalized = (host + path).lower()
                if normalized in {u.lower().rstrip("/").split("?")[0] for u in urls}:
                    print(f"[警告] 第{line_num}行 URL 重复: {line}", file=sys.stderr)
                    continue
                urls.append(line)
                continue

            # 配置项: key=value
            if "=" not in line:
                errors.append((line_num, raw, "无法识别的行(非URL非配置)"))
                continue

            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()

            if key not in _VALID_KEYS:
                errors.append((line_num, raw, f"未知配置键: '{key}'，有效键: {', '.join(sorted(_VALID_KEYS))}"))
                continue

            if key in seen_keys:
                errors.append((line_num, raw, f"重复配置键: '{key}'"))
                continue
            seen_keys.add(key)

            if key in _INT_KEYS:
                try:
                    v = int(value)
                except ValueError:
                    errors.append((line_num, raw, f"'{key}' 必须是整数，当前值: '{value}'"))
                    continue
                if v < 0:
                    errors.append((line_num, raw, f"'{key}' 不能为负数: {v}"))
                    continue
                if key == "images_per_product" and v > 1:
                    errors.append((line_num, raw, f"images_per_product 当前只允许 0 或 1 (只支持一张主图)，当前值: {v}"))
                    continue
                config[key] = v
            else:
                config[key] = value

    if errors:
        print("[错误] 配置文件存在以下问题:", file=sys.stderr)
        for ln, raw, reason in errors:
            print(f"  第{ln}行: {raw.strip()}", file=sys.stderr)
            print(f"    原因: {reason}", file=sys.stderr)
        raise ValueError(f"配置文件有 {len(errors)} 个错误")

    if not urls:
        raise ValueError("配置文件中没有有效的类目URL")
    return config, urls
