"""V2.2.0 产品导出模式：统一中文字段与真实类目层级。"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import OrderedDict
from typing import Any, Mapping


PRODUCT_OUTPUT_FIELD_MAP = OrderedDict([
    ("category_name", "类目名称"),
    ("category_url", "类目链接"),
    ("source_page_url", "来源页面链接"),
    ("page_number", "页码"),
    ("position_in_page", "页内位置"),
    ("product_id", "产品ID"),
    ("pnk", "PNK码"),
    ("sku", "SKU"),
    ("offer_id", "报价ID"),
    ("title", "产品标题"),
    ("product_url", "产品链接"),
    ("price_current", "前端价格"),
    ("price_old", "PRP原价"),
    ("price_promo", "活动价格"),
    ("price_current_raw", "前端价格原文"),
    ("price_old_raw", "PRP原价原文"),
    ("price_promo_raw", "活动价格原文"),
    ("discount_percent", "前端折扣"),
    ("currency", "货币"),
    ("availability", "库存状态"),
    ("stock_text", "库存原文"),
    ("seller", "卖家"),
    ("brand", "品牌"),
    ("badges", "商品标签"),
    ("campaign_name", "活动名称"),
    ("shipping_text", "配送信息"),
    ("rating", "评论分数"),
    ("review_count", "评价数量"),
    ("main_image_url", "产品图片"),
    ("main_image_local_path", "本地图片路径"),
    ("collected_at", "抓取时间"),
    ("http_status", "HTTP状态码"),
    ("parse_source", "解析来源"),
    ("extra", "扩展信息"),
])

EXTRA_OUTPUT_FIELD_MAP = {
    "favorite_data": "收藏数据",
    "pnk": "PNK码",
    "productid": "产品ID",
    "offerid": "报价ID",
    "has_family": "是否存在系列商品",
    "is_family": "是否为系列商品",
    "product_name": "产品名称",
    "currency": "货币",
    "price": "价格",
    "category_trail": "类目路径",
    "availability_id": "库存状态ID",
    "data-category-id": "类目ID",
    "data-department-id": "部门ID",
    "data-category-trail": "类目路径",
    "data-category-name": "类目名称",
    "data-referrer": "来源路径",
}

CATEGORY_LEVEL_FIELDS = ("一级类", "二级类", "三级类", "四级类", "五级类")

# 只翻译 favorite_data 中这些已确认字段；未知技术结构保持原样。
_FAVORITE_DATA_FIELD_MAP = {
    key: value for key, value in EXTRA_OUTPUT_FIELD_MAP.items()
    if key not in {
        "favorite_data", "availability_id", "data-category-id",
        "data-department-id", "data-category-trail", "data-category-name",
        "data-referrer",
    }
}
_EXTRA_TOP_LEVEL_FIELD_MAP = dict(EXTRA_OUTPUT_FIELD_MAP)


def split_category_path(path: Any) -> list[str]:
    """只按 / 拆分真实路径，去除空白和空段，最多返回五级。"""
    return _split_category_path_all(path)[:5]


def _split_category_path_all(path: Any) -> list[str]:
    """解析完整真实路径，供末级一致性校验和完整度比较。"""
    if not isinstance(path, str) or not path.strip():
        return []
    return [segment.strip() for segment in path.split("/") if segment.strip()]


def extract_category_levels(extra: Any, category_name: str = "") -> list[str]:
    """从商品已有 extra 证据选择最完整且与当前类目一致的路径。"""
    if not isinstance(extra, Mapping):
        return []

    favorite = extra.get("favorite_data")
    sources = []
    if isinstance(favorite, Mapping):
        sources.append((0, favorite.get("category_trail")))
    sources.append((1, extra.get("data-category-trail")))

    candidates: list[tuple[int, int, list[str]]] = []
    for priority, raw_path in sources:
        levels = _split_category_path_all(raw_path)
        if not levels:
            continue
        if category_name and not _category_names_match(levels[-1], category_name):
            continue
        candidates.append((len(levels), -priority, levels[:5]))

    if not candidates:
        return []
    return max(candidates, key=lambda candidate: (candidate[0], candidate[1]))[2]


def translate_extra(extra: Any) -> Any:
    """翻译 extra 中明确字段名，不改变任何值、类型或未知技术结构。"""
    if not isinstance(extra, Mapping):
        return extra

    translated: dict[str, Any] = {}
    for key, value in extra.items():
        output_key = _EXTRA_TOP_LEVEL_FIELD_MAP.get(key, key)
        if key == "favorite_data" and isinstance(value, Mapping):
            translated[output_key] = {
                _FAVORITE_DATA_FIELD_MAP.get(fav_key, fav_key): fav_value
                for fav_key, fav_value in value.items()
            }
        else:
            translated[output_key] = value
    return translated


def product_to_output_dict(product: Mapping[str, Any], *, stringify_extra: bool = False) -> dict:
    """在最终导出边界将一个英文内部商品字典转换为中文输出字典。"""
    levels = extract_category_levels(product.get("extra"), str(product.get("category_name") or ""))
    output: dict[str, Any] = {}

    for internal_name, output_name in PRODUCT_OUTPUT_FIELD_MAP.items():
        if internal_name == "extra":
            if internal_name not in product:
                continue
            value = translate_extra(product[internal_name])
            if stringify_extra:
                value = json.dumps(value, ensure_ascii=False) if value else ""
            output[output_name] = value
        elif internal_name in product:
            output[output_name] = product[internal_name]

        if internal_name == "source_page_url":
            for index, level in enumerate(levels):
                output[CATEGORY_LEVEL_FIELDS[index]] = level

    return output


def output_columns(max_category_level: int = 0) -> list[str]:
    """返回稳定中文列顺序；固定格式只加入本批次实际需要的类目列。"""
    level_count = max(0, min(int(max_category_level or 0), len(CATEGORY_LEVEL_FIELDS)))
    columns: list[str] = []
    for internal_name, output_name in PRODUCT_OUTPUT_FIELD_MAP.items():
        columns.append(output_name)
        if internal_name == "source_page_url":
            columns.extend(CATEGORY_LEVEL_FIELDS[:level_count])
    return columns


def output_column_pairs(max_category_level: int = 0) -> list[tuple[str, str]]:
    """兼容 ProductItem 的旧列接口，但字段定义仍来自唯一映射表。"""
    reverse_map = {output_name: internal_name for internal_name, output_name in PRODUCT_OUTPUT_FIELD_MAP.items()}
    return [(name, reverse_map.get(name, name)) for name in output_columns(max_category_level)]


def max_category_level(records: list[Mapping[str, Any]]) -> int:
    """计算一批中文输出记录实际出现的最高类目层级。"""
    highest = 0
    for record in records:
        for index, field in enumerate(CATEGORY_LEVEL_FIELDS, 1):
            if field in record:
                highest = max(highest, index)
    return highest


def _category_names_match(path_final: str, category_name: str) -> bool:
    return _normalize_category_name(path_final) == _normalize_category_name(category_name)


def _normalize_category_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value))
    without_marks = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^0-9a-z]+", "", without_marks.casefold())
