"""
导出模块：将商品数据导出为 CSV、XLSX、JSONL 格式
"""
import csv
import json
import logging
import os

from models import ProductItem

logger = logging.getLogger("emag_crawler.exporters")


class Exporters:
    """商品数据导出器"""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.csv_path = os.path.join(output_dir, "products.csv")
        self.xlsx_path = os.path.join(output_dir, "products.xlsx")
        self.jsonl_path = os.path.join(output_dir, "products.jsonl")

        # JSONL 增量写入模式
        self._jsonl_file = open(self.jsonl_path, "w", encoding="utf-8")

        # CSV 缓冲
        self._csv_buffer: list[dict] = []
        self._csv_header_written = False

    def add_product(self, product: ProductItem) -> None:
        """添加一个商品到所有输出"""
        d = product.to_dict()
        # 将 extra 字段解析回 dict 用于 JSONL
        if product.extra:
            d["extra"] = product.extra
        else:
            d.pop("extra", None)

        # JSONL: 立即写入
        self._jsonl_file.write(json.dumps(d, ensure_ascii=False) + "\n")
        self._jsonl_file.flush()

        # CSV: 缓冲写入
        self._csv_buffer.append(product.to_dict())

    def add_products(self, products: list[ProductItem]) -> None:
        """批量添加商品"""
        for p in products:
            self.add_product(p)

    def finalize(self) -> None:
        """完成导出，写入 XLSX 和 CSV"""
        # 关闭 JSONL
        self._jsonl_file.close()

        # 写入 CSV
        self._write_csv()

        # 写入 XLSX
        self._write_xlsx()

    def _write_csv(self) -> None:
        """写入 CSV 文件（UTF-8 BOM）"""
        if not self._csv_buffer:
            logger.warning("没有商品数据可写入 CSV")
            return

        columns = ProductItem.csv_columns()
        field_names = ProductItem.field_names()

        with open(self.csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=field_names, extrasaction="ignore")
            writer.writerow(dict(zip(field_names, columns)))  # 中文表头
            for row in self._csv_buffer:
                writer.writerow(row)

        logger.info(f"CSV 已写入: {self.csv_path} ({len(self._csv_buffer)} 行)")

    def _write_xlsx(self) -> None:
        """写入 XLSX 文件，带格式"""
        if not self._csv_buffer:
            logger.warning("没有商品数据可写入 XLSX")
            return

        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill, Alignment, numbers
            from openpyxl.utils import get_column_letter
        except ImportError:
            logger.warning("openpyxl 未安装，跳过 XLSX 导出")
            return

        wb = Workbook()
        ws = wb.active
        ws.title = "商品数据"

        # 列定义
        columns = ProductItem.excel_columns()

        # 写入表头
        header_font = Font(bold=True, size=11)
        header_fill = PatternFill(start_color="005EB8", end_color="005EB8", fill_type="solid")
        header_font_white = Font(bold=True, size=11, color="FFFFFF")

        for col_idx, (col_name, _) in enumerate(columns, 1):
            cell = ws.cell(row=1, column=col_idx, value=col_name)
            cell.font = header_font_white
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        # 写入数据
        price_fields = {"price_current", "price_old", "price_promo"}
        url_fields = {"category_url", "source_page_url", "product_url", "main_image_url"}

        for row_idx, item in enumerate(self._csv_buffer, 2):
            for col_idx, (_, field_name) in enumerate(columns, 1):
                value = item.get(field_name, "")
                cell = ws.cell(row=row_idx, column=col_idx)

                if field_name in price_fields and value is not None:
                    cell.value = float(value)
                    cell.number_format = '#,##0.00'
                elif field_name in url_fields:
                    cell.value = str(value) if value else ""
                elif field_name == "discount_percent" and value is not None:
                    cell.value = int(value)
                elif field_name == "rating" and value is not None:
                    cell.value = float(value)
                    cell.number_format = '0.00'
                elif field_name == "review_count" and value is not None:
                    cell.value = int(value)
                elif field_name == "page_number" and value is not None:
                    cell.value = int(value)
                elif field_name == "position_in_page" and value is not None:
                    cell.value = int(value)
                elif field_name == "http_status" and value is not None:
                    cell.value = int(value)
                else:
                    cell.value = str(value) if value is not None else ""

        # 自动筛选
        ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{len(self._csv_buffer) + 1}"

        # 冻结首行
        ws.freeze_panes = "A2"

        # 设置合理列宽
        col_widths = {
            1: 16, 2: 50, 3: 60, 4: 8, 5: 10,
            6: 12, 7: 14, 8: 12, 9: 12, 10: 45, 11: 65,
            12: 12, 13: 18, 14: 12, 15: 18, 16: 12, 17: 18,
            18: 12, 19: 8, 20: 14, 21: 16, 22: 14, 23: 14,
            24: 30, 25: 18, 26: 10, 27: 12, 28: 70, 29: 50,
            30: 22, 31: 12, 32: 22, 33: 40,
        }
        for col_idx, width in col_widths.items():
            ws.column_dimensions[get_column_letter(col_idx)].width = width

        wb.save(self.xlsx_path)
        logger.info(f"XLSX 已写入: {self.xlsx_path} ({len(self._csv_buffer)} 行)")

    def get_product_count(self) -> int:
        """获取已写入的商品数"""
        return len(self._csv_buffer)
