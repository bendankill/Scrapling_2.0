"""
工具函数：日志、价格解析、URL 处理、文件操作等
"""
import re
import os
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


def setup_logging(log_dir: str, level: str = "INFO") -> logging.Logger:
    """配置日志：同时输出到控制台和文件"""
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger("emag_crawler")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    # 控制台 handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
    )
    console.setFormatter(console_fmt)
    logger.addHandler(console)

    # 文件 handler
    log_file = os.path.join(log_dir, "run.log")
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    file_handler.setFormatter(file_fmt)
    logger.addHandler(file_handler)

    return logger


def parse_romanian_price(text: str) -> Optional[float]:
    """
    解析罗马尼亚价格格式
    例: "45,99Lei" → 45.99
         "1.234,56 Lei" → 1234.56
         "45,99" → 45.99
    """
    if not text:
        return None

    # 去除货币符号和多余空格
    text = text.strip()
    text = re.sub(r'(?i)\s*lei\s*', '', text)
    text = re.sub(r'(?i)\s*RON\s*', '', text)
    text = re.sub(r'(?i)\s*PRP\s*:\s*', '', text)
    text = text.strip()

    # 罗马尼亚格式: 千位分隔符是 "." 小数点用 ","
    # 例: "1.234,56" → 1234.56
    if re.match(r'^(\d{1,3}(\.\d{3})*),\d{1,2}$', text):
        text = text.replace('.', '').replace(',', '.')
    # 只有小数点: "45,99"
    elif re.match(r'^\d+,\d{1,2}$', text):
        text = text.replace(',', '.')
    # 整数
    elif re.match(r'^\d+$', text):
        pass
    else:
        # 尝试通用清理
        text = text.replace('.', '').replace(',', '.').strip()

    try:
        return round(float(text), 2)
    except (ValueError, TypeError):
        return None


def make_output_dir(base: str = "output") -> str:
    """创建带时间戳的输出目录"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(base, timestamp)
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "images"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "logs"), exist_ok=True)
    return out_dir


def sanitize_filename(name: str) -> str:
    """清理文件名，确保 Windows 兼容"""
    # 替换非法字符
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    # 限制长度
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
        # 从 base_url 提取域名
        match = re.match(r'(https?://[^/]+)', base_url)
        if match:
            return match.group(1) + path
    return path


def write_jsonl(filepath: str, data: dict) -> None:
    """增量写入一行 JSONL"""
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")


def write_errors_csv(filepath: str, error_data: dict, write_header: bool = False) -> None:
    """追加写入错误记录"""
    import csv
    file_exists = os.path.exists(filepath) and os.path.getsize(filepath) > 0
    with open(filepath, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=error_data.keys())
        if not file_exists or write_header:
            writer.writeheader()
        writer.writerow(error_data)


def load_json_config(filepath: str) -> dict:
    """加载 JSON 配置文件，带错误提示"""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"配置文件不存在: {filepath}")
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"配置文件 JSON 格式错误 ({filepath}): {e}")


def get_version() -> str:
    """读取版本号"""
    version_file = os.path.join(os.path.dirname(__file__), "VERSION")
    if os.path.exists(version_file):
        with open(version_file, "r") as f:
            return f.read().strip()
    return "2.0.0"
