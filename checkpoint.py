"""
断点续抓模块 V2.1.1-fix: 统一 RunStatus, 原子产品键, checkpoint管理
"""
import hashlib, json, logging, os
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Optional
from utils import write_atomic_json

logger = logging.getLogger("emag_crawler.checkpoint")
SCHEMA_VERSION = 2
APP_VERSION = "2.1.1"


class RunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    PAUSED_NETWORK = "paused_network"
    PAUSED_WAF = "paused_waf"
    INTERRUPTED = "interrupted"
    FAILED = "failed"

    @property
    def exit_code(self) -> int:
        return {
            RunStatus.COMPLETED: 0,
            RunStatus.RUNNING: 0,
            RunStatus.PAUSED_NETWORK: 2,
            RunStatus.PAUSED_WAF: 3,
            RunStatus.INTERRUPTED: 130,
            RunStatus.FAILED: 2,
        }[self]

    @property
    def is_paused(self) -> bool:
        return self in (RunStatus.PAUSED_NETWORK, RunStatus.PAUSED_WAF,
                        RunStatus.INTERRUPTED, RunStatus.FAILED)

    @property
    def is_stopped(self) -> bool:
        return self != RunStatus.RUNNING


class CheckpointManager:
    def __init__(self, output_dir: str, config_file="", arguments=None):
        self.output_dir = output_dir
        self.pages_dir = os.path.join(output_dir, "checkpoint_pages")
        os.makedirs(self.pages_dir, exist_ok=True)
        self.checkpoint_path = os.path.join(output_dir, "checkpoint.json")
        self._lock = Lock()
        self.config_file = config_file
        self.config_hash = self._compute_config_hash()
        self.arguments = arguments or {}
        self.data = {
            "schema_version": SCHEMA_VERSION, "app_version": APP_VERSION,
            "run_id": datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
            "status": RunStatus.RUNNING.value, "pause_reason": "",
            "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": "",
            "output_dir": output_dir, "config_file": config_file,
            "config_hash": self.config_hash, "arguments": self.arguments,
            "phase": "crawling", "categories": [],
            "completed_product_keys": [], "images_completed": [], "last_error": {},
        }
        self._product_keys: set = set()
        self._keys_lock = Lock()
        self._page_results: dict = {}
        # 每页统计累加计数器 (用于幂等性)
        self._page_stats: dict = {}

    def _compute_config_hash(self) -> str:
        if not self.config_file or not os.path.exists(self.config_file): return ""
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                return hashlib.md5(f.read().encode()).hexdigest()[:12]
        except Exception: return ""

    def _ts(self): self.data["updated_at"] = datetime.now(timezone.utc).isoformat()

    def _write_cp(self): write_atomic_json(self.checkpoint_path, self.data)

    def init_category(self, cat_name, cat_url, effective_pages=None):
        cat_id = hashlib.md5(cat_url.lower().encode()).hexdigest()[:12]
        entry = {"category_id": cat_id, "name": cat_name, "url": cat_url,
                 "status": "in_progress", "effective_pages": effective_pages,
                 "completed_pages": [], "partial_pages": [], "next_page": 1,
                 "stop_reason": "", "cards_found_total": 0,
                 "products_parsed_total": 0, "parse_failed_total": 0,
                 "duplicates_total": 0, "new_unique_total": 0}
        with self._lock: self.data["categories"].append(entry)
        return cat_id

    def get_category(self, cat_url):
        for c in self.data["categories"]:
            if c["url"] == cat_url: return c
        return None

    def update_category(self, cat_url, **kw):
        with self._lock:
            for c in self.data["categories"]:
                if c["url"] == cat_url: c.update(kw); self._ts(); break

    # ---- 原子产品键操作 (S1-3: 持久化到 completed_product_keys) ----
    def check_and_add_product_keys(self, keys: list) -> tuple:
        """原子操作: 检查并添加产品键。返回 (new_unique_keys_list, dup_count)。
        S1-2修复: new_keys只包含首次出现的键, dup_count=总输入-新键数。
        S1-3修复: 同步持久化到 data['completed_product_keys']。
        """
        new, dup = [], 0
        with self._keys_lock:
            for k in keys:
                if k in self._product_keys:
                    dup += 1
                else:
                    self._product_keys.add(k)
                    new.append(k)
            # S1-3: 持久化
            if new:
                existing = set(self.data.get("completed_product_keys", []))
                for k in new:
                    if k not in existing:
                        existing.add(k)
                self.data["completed_product_keys"] = sorted(existing)
        return new, dup

    def has_product_key(self, k):
        with self._keys_lock: return k in self._product_keys

    def add_product_keys(self, keys):
        with self._keys_lock:
            new_added = []
            for k in keys:
                if k not in self._product_keys:
                    self._product_keys.add(k)
                    new_added.append(k)
            if new_added:
                existing = set(self.data.get("completed_product_keys", []))
                for k in new_added:
                    existing.add(k)
                self.data["completed_product_keys"] = sorted(existing)

    def get_product_key_count(self):
        with self._keys_lock: return len(self._product_keys)

    def sync_product_keys_to_checkpoint(self):
        """确保 checkpoint 中的 completed_product_keys 与内存一致"""
        with self._keys_lock:
            self.data["completed_product_keys"] = sorted(self._product_keys)

    # ---- 页面快照 ----
    def save_page_snapshot(self, cat_url, page_num, products):
        cat_id = hashlib.md5(cat_url.lower().encode()).hexdigest()[:12]
        fname = f"{cat_id}_page_{page_num:03d}.json"
        fpath = os.path.join(self.pages_dir, fname)
        data = []
        for p in products:
            if hasattr(p, 'to_dict'): d = p.to_dict()
            else: d = dict(p)
            if isinstance(d.get("extra"), dict) and not d["extra"]: d.pop("extra", None)
            data.append(d)
        write_atomic_json(fpath, data)
        return fpath

    def load_page_snapshot(self, cat_url, page_num):
        cat_id = hashlib.md5(cat_url.lower().encode()).hexdigest()[:12]
        fpath = os.path.join(self.pages_dir, f"{cat_id}_page_{page_num:03d}.json")
        if not os.path.exists(fpath): return None
        try:
            with open(fpath, "r", encoding="utf-8") as f: return json.load(f)
        except Exception: return None

    # ---- 页面状态 (带幂等性) ----
    def mark_page_completed(self, cat_url, page_num, cards_found=0,
                            products_parsed=0, parse_failed=0, duplicates=0, new_unique=0):
        with self._lock:
            # 幂等: 已完成的页不重复
            for c in self.data["categories"]:
                if c["url"] != cat_url: continue
                if page_num in c["completed_pages"]: return
                c["completed_pages"].append(page_num)
                c["completed_pages"].sort()
                c["next_page"] = max(c["next_page"], page_num + 1)
                # 累加统计 (仅在第一次标记时)
                pid = f"{c['category_id']}_{page_num}"
                if pid not in self._page_stats:
                    self._page_stats[pid] = True
                    c["cards_found_total"] += cards_found
                    c["products_parsed_total"] += products_parsed
                    c["parse_failed_total"] += parse_failed
                    c["duplicates_total"] += duplicates
                    c["new_unique_total"] += new_unique
                break
            self._ts(); self._write_cp()

    def mark_page_partial(self, cat_url, page_num):
        with self._lock:
            for c in self.data["categories"]:
                if c["url"] != cat_url:
                    continue
                if page_num not in c["partial_pages"]:
                    c["partial_pages"].append(page_num)
                # 不推进 next_page
                break
            self._ts()
            self._write_cp()

    def mark_category_done(self, cat_url, stop_reason):
        with self._lock:
            for c in self.data["categories"]:
                if c["url"] == cat_url:
                    c["status"] = "completed"
                    c["stop_reason"] = stop_reason
                    break
            self._ts(); self._write_cp()

    # ---- 全局状态 ----
    def set_status(self, status: RunStatus, reason="", last_error=None):
        with self._lock:
            self.data["status"] = status.value
            self.data["pause_reason"] = reason
            if last_error: self.data["last_error"] = last_error
            self._ts(); self._write_cp()

    def get_status(self) -> RunStatus:
        try: return RunStatus(self.data.get("status", "running"))
        except ValueError: return RunStatus.FAILED

    def is_completed(self): return self.get_status() == RunStatus.COMPLETED
    def is_paused(self): return self.get_status().is_paused

    def get_category_statuses(self):
        r = {}
        for c in self.data["categories"]:
            r[c["url"]] = {"status": c["status"], "completed_pages": list(c["completed_pages"]),
                           "next_page": c["next_page"], "stop_reason": c.get("stop_reason", ""),
                           "effective_pages": c.get("effective_pages")}
        return r

    @staticmethod
    def load(checkpoint_path):
        if not os.path.exists(checkpoint_path):
            raise FileNotFoundError(f"checkpoint 不存在: {checkpoint_path}")
        with open(checkpoint_path, "r", encoding="utf-8") as f: data = json.load(f)
        if data.get("schema_version", 0) != SCHEMA_VERSION:
            raise ValueError(f"checkpoint schema 版本不兼容: 期望 {SCHEMA_VERSION}, 实际 {data.get('schema_version')}")
        output_dir = data.get("output_dir", os.path.dirname(checkpoint_path))
        cm = CheckpointManager.__new__(CheckpointManager)
        cm.output_dir = output_dir
        cm.pages_dir = os.path.join(output_dir, "checkpoint_pages")
        os.makedirs(cm.pages_dir, exist_ok=True)
        cm.checkpoint_path = checkpoint_path
        cm._lock = Lock(); cm._keys_lock = Lock()
        cm.config_file = data.get("config_file", "")
        cm.config_hash = data.get("config_hash", "")
        cm.arguments = data.get("arguments", {})
        cm.data = data
        cm._product_keys = set(data.get("completed_product_keys", []))
        cm._page_stats = {}
        return cm

    def validate_config(self):
        return self._compute_config_hash() == self.data.get("config_hash", "")
