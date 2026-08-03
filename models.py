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

    def to_dict(self) -> dict:
        """转为字典，用于导出"""
        d = asdict(self)
        d["extra"] = json.dumps(self.extra, ensure_ascii=False) if self.extra else ""
        return d

    @staticmethod
    def excel_columns() -> list:
        """返回 Excel 列名和字段映射"""
        return [
            ("类目名称", "category_name"),
            ("类目URL", "category_url"),
            ("来源页面", "source_page_url"),
            ("页码", "page_number"),
            ("页面位置", "position_in_page"),
            ("商品ID", "product_id"),
            ("PNK", "pnk"),
            ("SKU", "sku"),
            ("Offer ID", "offer_id"),
            ("商品标题", "title"),
            ("商品URL", "product_url"),
            ("当前售价", "price_current"),
            ("当前售价(原始)", "price_current_raw"),
            ("原价", "price_old"),
            ("原价(原始)", "price_old_raw"),
            ("活动价", "price_promo"),
            ("活动价(原始)", "price_promo_raw"),
            ("折扣百分比", "discount_percent"),
            ("货币", "currency"),
            ("库存状态", "availability"),
            ("库存文本", "stock_text"),
            ("卖家", "seller"),
            ("品牌", "brand"),
            ("标签", "badges"),
            ("活动名称", "campaign_name"),
            ("配送信息", "shipping_text"),
            ("评分", "rating"),
            ("评论数", "review_count"),
            ("主图URL", "main_image_url"),
            ("主图本地路径", "main_image_local_path"),
            ("采集时间", "collected_at"),
            ("HTTP状态", "http_status"),
            ("解析来源", "parse_source"),
            ("额外字段", "extra"),
        ]

    @staticmethod
    def csv_columns() -> list:
        """返回 CSV 列名"""
        return [col[0] for col in ProductItem.excel_columns()]

    @staticmethod
    def field_names() -> list:
        """返回字段名列表"""
        return [col[1] for col in ProductItem.excel_columns()]
