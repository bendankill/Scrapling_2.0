"""V2.2.1 产品导出模式：统一中文字段与旁路真实类目层级。"""

from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any, Mapping, Sequence

from category_hierarchy import (
    CategoryPathEvidence,
    select_product_category_levels,
    split_category_path_all,
)


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
    ("campaign_name", "链接打标"),
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

FAVORITE_DATA_OUTPUT_FIELD_MAP = {
    "pnk": "PNK码",
    "productid": "产品ID",
    "offerid": "报价ID",
    "has_family": "是否存在系列商品",
    "is_family": "是否为系列商品",
    "product_name": "产品名称",
    "currency": "货币",
    "price": "价格",
    "category_trail": "类目路径",
}

EXTRA_TOP_LEVEL_OUTPUT_FIELD_MAP = {
    "favorite_data": "收藏数据",
    "availability_id": "库存状态ID",
    "data-category-id": "类目ID",
    "data-department-id": "部门ID",
    "data-category-trail": "类目路径",
    "data-category-name": "类目名称",
    "data-referrer": "来源路径",
}

# 保留公开的完整映射视图，实际转换严格按上面两个数据层级分别执行。
EXTRA_OUTPUT_FIELD_MAP = {
    **FAVORITE_DATA_OUTPUT_FIELD_MAP,
    **EXTRA_TOP_LEVEL_OUTPUT_FIELD_MAP,
}

CATEGORY_LEVEL_FIELDS = ("一级类", "二级类", "三级类", "四级类", "五级类")

def split_category_path(path: Any) -> list[str]:
    """只按 / 拆分真实路径，去除空白和空段，最多返回五级。"""
    return split_category_path_all(path)[:5]


def _split_category_path_all(path: Any) -> list[str]:
    """解析完整真实路径，供末级一致性校验和完整度比较。"""
    return split_category_path_all(path)


def extract_category_levels(
    extra: Any,
    category_name: str = "",
    verified_levels: Sequence[str] | None = None,
    category_evidence: CategoryPathEvidence | None = None,
) -> list[str]:
    """页面旁路证据优先；缺失时回退商品已有 extra 路径。"""
    return select_product_category_levels(
        extra, category_name, verified_levels, category_evidence)


def translate_extra(extra: Any) -> Any:
    """分层翻译 extra 的已确认字段，并在键冲突时完整保留原始键。"""
    if not isinstance(extra, Mapping):
        return extra

    prepared: dict[str, Any] = {}
    for key, value in extra.items():
        if key == "favorite_data" and isinstance(value, Mapping):
            prepared[key] = _translate_mapping_without_loss(
                value,
                FAVORITE_DATA_OUTPUT_FIELD_MAP,
            )
        else:
            prepared[key] = value
    return _translate_mapping_without_loss(prepared, EXTRA_TOP_LEVEL_OUTPUT_FIELD_MAP)


def _translate_mapping_without_loss(
    source: Mapping[str, Any],
    field_map: Mapping[str, str],
) -> dict[str, Any]:
    """安全翻译同一层字典；任何目标键冲突组都整体保留原始键名。"""
    items = list(source.items())
    proposed_targets = {
        key: field_map.get(key, key)
        for key, _value in items
    }
    target_groups: dict[str, list[str]] = {}
    for key, target in proposed_targets.items():
        target_groups.setdefault(target, []).append(key)

    conflicting_keys = {
        key
        for keys in target_groups.values()
        if len(keys) > 1
        for key in keys
    }
    for key, target in proposed_targets.items():
        if target != key and target in source:
            conflicting_keys.add(key)
            conflicting_keys.add(target)

    translated: dict[str, Any] = {}
    for key, value in items:
        output_key = key if key in conflicting_keys else proposed_targets[key]
        translated[output_key] = value

    if len(translated) != len(items):
        raise ValueError("字段名转换发生未处理的键冲突")
    return translated


def product_to_output_dict(
    product: Mapping[str, Any],
    *,
    stringify_extra: bool = False,
    category_levels: Sequence[str] | None = None,
    category_evidence: CategoryPathEvidence | None = None,
) -> dict:
    """在最终导出边界将一个英文内部商品字典转换为中文输出字典。"""
    levels = extract_category_levels(
        product.get("extra"),
        str(product.get("category_name") or ""),
        category_levels,
        category_evidence,
    )
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
