"""
数据模型：定义爬虫使用的数据结构
"""
from dataclasses import dataclass, field, asdict
from typing import Optional
import json


@dataclass
class ProductItem:
    """商品数据结构，包含列表页可获取的全部字段"""
    # 类目信息
    category_name: str = ""
    category_url: str = ""

    # 来源信息
    source_page_url: str = ""
    page_number: int = 0
    position_in_page: int = 0

    # 商品标识
    product_id: str = ""
    pnk: str = ""
    sku: str = ""
    offer_id: str = ""

    # 商品基本信息
    title: str = ""
    product_url: str = ""

    # 价格信息（数值）
    price_current: Optional[float] = None
    price_old: Optional[float] = None
    price_promo: Optional[float] = None

    # 价格信息（原始文本，保留格式用于核对）
    price_current_raw: str = ""
    price_old_raw: str = ""
    price_promo_raw: str = ""

    # 价格辅助信息
    discount_percent: Optional[int] = None
    currency: str = "RON"

    # 库存和卖家
    availability: str = ""
    stock_text: str = ""
    seller: str = ""

    # 品牌
    brand: str = ""

    # 标签和活动
    badges: str = ""  # 逗号分隔
    campaign_name: str = ""

    # 配送
    shipping_text: str = ""

    # 评分
    rating: Optional[float] = None
    review_count: Optional[int] = None

    # 图片
    main_image_url: str = ""
    main_image_local_path: str = ""

    # 采集元数据
    collected_at: str = ""
    http_status: int = 0
    parse_source: str = ""  # html/json-ld/embedded_json/hybrid

    # 额外字段（保留原始数据中未明确映射的字段）
    extra: dict = field(default_factory=dict)

    def to_dict(self, stringify_extra: bool = False) -> dict:
        """转为字典。stringify_extra=True 时将 extra 转为 JSON 字符串 (用于 CSV/XLSX)"""
        d = asdict(self)
        if stringify_extra and self.extra:
            d["extra"] = json.dumps(self.extra, ensure_ascii=False)
        elif not stringify_extra and not self.extra:
            d.pop("extra", None)
        return d

    @staticmethod
    def excel_columns(max_category_level: int = 0) -> list:
        """兼容旧接口；列定义由唯一产品输出映射表提供。"""
        from output_schema import output_column_pairs
        return output_column_pairs(max_category_level)

    @staticmethod
    def csv_columns(max_category_level: int = 0) -> list:
        """返回 CSV 列名"""
        return [col[0] for col in ProductItem.excel_columns(max_category_level)]

    @staticmethod
    def field_names(max_category_level: int = 0) -> list:
        """返回统一输出字段名列表"""
        return ProductItem.csv_columns(max_category_level)
