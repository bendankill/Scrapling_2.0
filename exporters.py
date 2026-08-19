"""
导出模块：商品数据导出为 CSV、XLSX、标准 JSON (数组)
"""
import csv
import logging
import os
from threading import Lock

from category_hierarchy import (
    CategoryEvidenceDecision,
    CategoryEvidenceStatus,
    CategoryLevelRegistry,
    CategoryPathEvidence,
)
from models import ProductItem
from output_schema import (
    max_category_level,
    output_columns,
    product_to_output_dict,
)
from utils import write_atomic_json

logger = logging.getLogger("emag_crawler.exporters")


class Exporters:
    """商品数据导出器, 线程安全"""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.csv_path = os.path.join(output_dir, "products.csv")
        self.xlsx_path = os.path.join(output_dir, "products.xlsx")
        self.json_path = os.path.join(output_dir, "products.json")

        # 所有商品数据 (list of dict)
        self._products: list[dict] = []
        self._lock = Lock()
        self._category_levels = CategoryLevelRegistry()

    def add_product(self, product: ProductItem) -> None:
        """添加一个商品 (线程安全)"""
        d = _product_to_json_dict(product)
        with self._lock:
            self._products.append(d)

    def add_products(self, products: list[ProductItem]) -> None:
        """批量添加商品 (线程安全)"""
        dicts = [_product_to_json_dict(p) for p in products]
        with self._lock:
            self._products.extend(dicts)

    def get_products_sorted(self) -> list[dict]:
        """按类目、页码、页面位置排序后返回"""
        with self._lock:
            sorted_list = sorted(
                self._products,
                key=lambda x: (
                    x.get("category_name", ""),
                    x.get("page_number", 0),
                    x.get("position_in_page", 0),
                ),
            )
            return sorted_list

    def get_product_count(self) -> int:
        """已写入商品数"""
        with self._lock:
            return len(self._products)

    def register_category_levels(
        self,
        category_url: str,
        evidence: CategoryPathEvidence,
    ) -> bool:
        """线程安全注册页面级旁路类目证据。"""
        return self._category_levels.register(category_url, evidence)

    def register_category_decision(
        self,
        category_url: str,
        decision: CategoryEvidenceDecision,
    ) -> bool:
        """Atomically register accepted evidence or its explicit conflict state."""
        if decision.status == CategoryEvidenceStatus.FINAL_CONFLICTED:
            return self._category_levels.register_conflict(category_url, final=True)
        if decision.status == CategoryEvidenceStatus.TEMPORARY_CONFLICTED:
            return self._category_levels.register_conflict(category_url, final=False)
        if decision.evidence:
            return self._category_levels.register(category_url, decision.evidence)
        return False

    def get_category_level_evidence(
        self,
        category_url: str,
    ) -> CategoryPathEvidence | None:
        """按规范化类目 URL 返回旁路证据副本。"""
        return self._category_levels.get(category_url)

    def get_category_level_state(self, category_url: str) -> CategoryEvidenceStatus:
        return self._category_levels.get_state(category_url)

    def get_csv_buffer(self) -> list[dict]:
        """获取用于 CSV/XLSX 导出的中文数据 (扩展信息转为 JSON 字符串)"""
        sorted_prods = self.get_products_sorted()
        return [self._to_output(product, stringify_extra=True) for product in sorted_prods]

    def get_json_buffer(self) -> list[dict]:
        """获取 products.json 使用的中文数据，扩展信息保持 object。"""
        return [self._to_output(product) for product in self.get_products_sorted()]

    def _to_output(self, product: dict, *, stringify_extra: bool = False) -> dict:
        evidence = self.get_category_level_evidence(
            str(product.get("category_url") or ""))
        return product_to_output_dict(
            product,
            stringify_extra=stringify_extra,
            category_evidence=evidence,
        )

    def write_json(self) -> None:
        """写入标准 products.json (JSON 数组, 原子写入)"""
        buffer = self.get_json_buffer()
        write_atomic_json(self.json_path, buffer)
        logger.info(f"JSON 已写入: {self.json_path} ({len(buffer)} 条)")

    def _write_csv(self) -> None:
        """写入 CSV (UTF-8 BOM)"""
        buffer = self.get_csv_buffer()
        columns = output_columns(max_category_level(buffer))
        if not buffer:
            # 至少创建表头
            with open(self.csv_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
                writer.writeheader()
            logger.info(f"CSV 已写入(仅表头): {self.csv_path}")
            return

        with open(self.csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(buffer)
        logger.info(f"CSV 已写入: {self.csv_path} ({len(buffer)} 行)")

    def _write_xlsx(self) -> None:
        """写入 XLSX (带格式化)"""
        buffer = self.get_csv_buffer()
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill, Alignment
            from openpyxl.utils import get_column_letter
        except ImportError:
            logger.warning("openpyxl 未安装，跳过 XLSX")
            return

        wb = Workbook()
        ws = wb.active
        ws.title = "商品数据"

        columns = output_columns(max_category_level(buffer))
        header_fill = PatternFill(start_color="005EB8", end_color="005EB8", fill_type="solid")
        header_font = Font(bold=True, size=11, color="FFFFFF")

        for col_idx, col_name in enumerate(columns, 1):
            cell = ws.cell(row=1, column=col_idx, value=col_name)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        if buffer:
            price_fields = {"前端价格", "PRP原价", "活动价格"}
            integer_fields = {"前端折扣", "评价数量", "页码", "页内位置", "HTTP状态码"}
            for row_idx, item in enumerate(buffer, 2):
                for col_idx, field_name in enumerate(columns, 1):
                    value = item.get(field_name, "")
                    cell = ws.cell(row=row_idx, column=col_idx)
                    if field_name in price_fields and value is not None and value != "":
                        try:
                            cell.value = float(value)
                            cell.number_format = '#,##0.00'
                        except (ValueError, TypeError):
                            cell.value = str(value) if value is not None else ""
                    elif field_name in integer_fields:
                        try:
                            cell.value = int(value) if value is not None and value != "" else value
                        except (ValueError, TypeError):
                            cell.value = str(value) if value is not None else ""
                    elif field_name == "评论分数" and value is not None and value != "":
                        try:
                            cell.value = float(value)
                        except (ValueError, TypeError):
                            cell.value = str(value)
                    else:
                        cell.value = str(value) if value is not None else ""

        ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{max(len(buffer) + 1, 2)}"
        ws.freeze_panes = "A2"

        col_widths = {
            "类目名称": 16, "产品标题": 45, "产品链接": 65,
            "前端价格": 12, "前端价格原文": 18, "PRP原价": 12,
            "PRP原价原文": 18, "配送信息": 30, "产品图片": 70,
            "本地图片路径": 50, "抓取时间": 22, "扩展信息": 40,
        }
        for field_name, width in col_widths.items():
            if field_name in columns:
                col_idx = columns.index(field_name) + 1
                ws.column_dimensions[get_column_letter(col_idx)].width = width

        wb.save(self.xlsx_path)
        logger.info(f"XLSX 已写入: {self.xlsx_path} ({len(buffer)} 行)")

    def finalize(self) -> None:
        """完成导出"""
        self.write_json()
        self._write_csv()
        self._write_xlsx()


def _product_to_json_dict(product: ProductItem) -> dict:
    """将 ProductItem 转为内部英文 dict；最终中文化只在写文件时发生。"""
    from dataclasses import asdict
    d = asdict(product)
    # JSON 中 extra 保持为原始 dict, 不被二次编码
    if not d.get("extra"):
        d.pop("extra", None)
    return d
