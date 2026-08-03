"""
断点续抓模块 V2.1.1: checkpoint管理、页面快照、恢复支持
"""
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from utils import write_atomic_json, get_product_key

logger = logging.getLogger("emag_crawler.checkpoint")

SCHEMA_VERSION = 1
APP_VERSION = "2.1.1"


class CheckpointManager:
    """管理断点续抓的 checkpoint 和页面快照"""

    def __init__(self, output_dir: str, config_file: str = "",
                 arguments: dict = None):
        self.output_dir = output_dir
        self.pages_dir = os.path.join(output_dir, "checkpoint_pages")
        os.makedirs(self.pages_dir, exist_ok=True)

        self.checkpoint_path = os.path.join(output_dir, "checkpoint.json")
        self._lock = Lock()

        # 配置哈希
        self.config_file = config_file
        self.config_hash = self._compute_config_hash()

        # 运行时参数
        self.arguments = arguments or {}

        # checkpoint 数据
        self.data = {
            "schema_version": SCHEMA_VERSION,
            "app_version": APP_VERSION,
            "run_id": datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_") + os.path.basename(output_dir),
            "status": "running",
            "pause_reason": "",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": "",
            "output_dir": output_dir,
            "config_file": config_file,
            "config_hash": self.config_hash,
            "arguments": self.arguments,
            "phase": "crawling",
            "categories": [],
            "completed_product_keys": [],
            "images_completed": [],
            "last_error": {},
        }

        # 内存中的商品去重集合
        self._product_keys: set = set()
        self._keys_lock = Lock()

        # 页面结果缓存: {page_num: PageResult}
        self._page_results: dict = {}

    def _compute_config_hash(self) -> str:
        if not self.config_file or not os.path.exists(self.config_file):
            return ""
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                return hashlib.md5(f.read().encode()).hexdigest()[:12]
        except Exception:
            return ""

    def _update_timestamp(self):
        self.data["updated_at"] = datetime.now(timezone.utc).isoformat()

    def init_category(self, cat_name: str, cat_url: str, effective_pages: Optional[int] = None):
        """初始化类目进度条目"""
        cat_id = hashlib.md5(cat_url.lower().encode()).hexdigest()[:12]
        entry = {
            "category_id": cat_id,
            "name": cat_name,
            "url": cat_url,
            "status": "in_progress",
            "effective_pages": effective_pages,
            "completed_pages": [],
            "partial_pages": [],
            "next_page": 1,
            "stop_reason": "",
            "cards_found_total": 0,
            "products_parsed_total": 0,
            "parse_failed_total": 0,
            "duplicates_total": 0,
            "new_unique_total": 0,
        }
        with self._lock:
            self.data["categories"].append(entry)
        return cat_id

    def get_category(self, cat_url: str) -> Optional[dict]:
        for c in self.data["categories"]:
            if c["url"] == cat_url:
                return c
        return None

    def update_category(self, cat_url: str, **kwargs):
        with self._lock:
            for c in self.data["categories"]:
                if c["url"] == cat_url:
                    c.update(kwargs)
                    self._update_timestamp()
                    break

    def save_page_snapshot(self, cat_url: str, page_num: int, products: list) -> str:
        """保存页面商品快照 (原子写入), 返回文件路径"""
        cat_id = hashlib.md5(cat_url.lower().encode()).hexdigest()[:12]
        fname = f"{cat_id}_page_{page_num:03d}.json"
        fpath = os.path.join(self.pages_dir, fname)
        # 转为 JSON-serializable 格式
        data = []
        for p in products:
            if hasattr(p, 'to_dict'):
                d = p.to_dict()
            else:
                d = dict(p)
            if isinstance(d.get("extra"), dict) and not d["extra"]:
                d.pop("extra", None)
            data.append(d)
        write_atomic_json(fpath, data)
        return fpath

    def load_page_snapshot(self, cat_url: str, page_num: int) -> Optional[list]:
        """加载页面商品快照"""
        cat_id = hashlib.md5(cat_url.lower().encode()).hexdigest()[:12]
        fname = f"{cat_id}_page_{page_num:03d}.json"
        fpath = os.path.join(self.pages_dir, fname)
        if not os.path.exists(fpath):
            return None
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def mark_page_completed(self, cat_url: str, page_num: int,
                            cards_found: int = 0, products_parsed: int = 0,
                            parse_failed: int = 0, duplicates: int = 0,
                            new_unique: int = 0):
        """标记页面完成并更新 checkpoint"""
        with self._lock:
            for c in self.data["categories"]:
                if c["url"] == cat_url:
                    if page_num not in c["completed_pages"]:
                        c["completed_pages"].append(page_num)
                        c["completed_pages"].sort()
                    if page_num in c["partial_pages"]:
                        c["partial_pages"].remove(page_num)
                    c["next_page"] = max(c["next_page"], page_num + 1)
                    c["cards_found_total"] += cards_found
                    c["products_parsed_total"] += products_parsed
                    c["parse_failed_total"] += parse_failed
                    c["duplicates_total"] += duplicates
                    c["new_unique_total"] += new_unique
                    break
            self._update_timestamp()
            self._write_checkpoint()

    def mark_page_partial(self, cat_url: str, page_num: int):
        """标记页面部分完成"""
        with self._lock:
            for c in self.data["categories"]:
                if c["url"] == cat_url:
                    if page_num not in c["partial_pages"]:
                        c["partial_pages"].append(page_num)
                    break
            self._update_timestamp()

    def mark_category_done(self, cat_url: str, stop_reason: str):
        """标记类目完成"""
        with self._lock:
            for c in self.data["categories"]:
                if c["url"] == cat_url:
                    c["status"] = "completed"
                    c["stop_reason"] = stop_reason
                    break
            self._update_timestamp()
            self._write_checkpoint()

    def set_paused(self, reason: str, phase: str = "crawling",
                   last_error: dict = None):
        """设置暂停状态"""
        with self._lock:
            self.data["status"] = "paused"
            self.data["pause_reason"] = reason
            self.data["phase"] = phase
            if last_error:
                self.data["last_error"] = last_error
            self._update_timestamp()
            self._write_checkpoint()

    def set_completed(self):
        """设置完成状态"""
        with self._lock:
            self.data["status"] = "completed"
            self.data["phase"] = "done"
            self.data["pause_reason"] = ""
            self._update_timestamp()
            self._write_checkpoint()

    def set_interrupted(self):
        """设置 Ctrl+C 中断状态"""
        with self._lock:
            self.data["status"] = "interrupted"
            self.data["pause_reason"] = "user_interrupt"
            self._update_timestamp()
            self._write_checkpoint()

    def add_product_keys(self, keys: list):
        """批量添加商品键"""
        with self._keys_lock:
            self._product_keys.update(keys)

    def has_product_key(self, key: str) -> bool:
        with self._keys_lock:
            return key in self._product_keys

    def get_product_key_count(self) -> int:
        with self._keys_lock:
            return len(self._product_keys)

    def get_completed_pages(self, cat_url: str) -> set:
        for c in self.data["categories"]:
            if c["url"] == cat_url:
                return set(c["completed_pages"])
        return set()

    def get_next_page(self, cat_url: str) -> int:
        for c in self.data["categories"]:
            if c["url"] == cat_url:
                return c["next_page"]
        return 1

    def _write_checkpoint(self):
        """原子写入 checkpoint (内部调用, 已加锁)"""
        write_atomic_json(self.checkpoint_path, self.data)

    def save(self):
        """公开的保存方法"""
        with self._lock:
            self._update_timestamp()
            self._write_checkpoint()

    def is_completed(self) -> bool:
        return self.data.get("status") == "completed"

    def is_paused(self) -> bool:
        return self.data.get("status") in ("paused", "interrupted", "waf_blocked")

    def get_category_statuses(self) -> dict:
        """返回各类目状态: {cat_url: status}"""
        result = {}
        for c in self.data["categories"]:
            result[c["url"]] = {
                "status": c["status"],
                "completed_pages": list(c["completed_pages"]),
                "next_page": c["next_page"],
                "stop_reason": c.get("stop_reason", ""),
                "effective_pages": c.get("effective_pages"),
            }
        return result

    @staticmethod
    def load(checkpoint_path: str) -> "CheckpointManager":
        """加载已有 checkpoint"""
        if not os.path.exists(checkpoint_path):
            raise FileNotFoundError(f"checkpoint 不存在: {checkpoint_path}")

        with open(checkpoint_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 校验版本
        if data.get("schema_version", 0) != SCHEMA_VERSION:
            raise ValueError(
                f"checkpoint schema 版本不兼容: "
                f"期望 {SCHEMA_VERSION}, 实际 {data.get('schema_version')}"
            )

        output_dir = data.get("output_dir", os.path.dirname(checkpoint_path))
        cm = CheckpointManager.__new__(CheckpointManager)
        cm.output_dir = output_dir
        cm.pages_dir = os.path.join(output_dir, "checkpoint_pages")
        os.makedirs(cm.pages_dir, exist_ok=True)
        cm.checkpoint_path = checkpoint_path
        cm._lock = Lock()
        cm.config_file = data.get("config_file", "")
        cm.config_hash = data.get("config_hash", "")
        cm.arguments = data.get("arguments", {})
        cm.data = data
        cm._product_keys = set(data.get("completed_product_keys", []))
        cm._keys_lock = Lock()
        cm._page_results = {}
        return cm

    def validate_config(self) -> bool:
        """校验当前配置文件是否与 checkpoint 记录一致"""
        current_hash = self._compute_config_hash()
        recorded_hash = self.data.get("config_hash", "")
        if not recorded_hash:
            return True
        return current_hash == recorded_hash
