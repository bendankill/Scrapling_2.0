"""
页面解析器：从 eMAG 列表页 HTML 中提取商品数据

数据来源优先级:
1. data- 属性（最稳定）
2. 收藏按钮内嵌 JSON（含精确价格数值）
3. HTML 元素（价格文本、库存、评分）
4. 对比按钮内嵌数据（图片 URL）
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from models import ProductItem
from utils import parse_romanian_price

logger = logging.getLogger("emag_crawler.parser")


def parse_product_listing(
    html: str,
    category_name: str = "",
    category_url: str = "",
    page_url: str = "",
    page_number: int = 1,
) -> list[ProductItem]:
    """
    解析 eMAG 商品列表页 HTML，提取所有商品数据

    Args:
        html: 页面 HTML 内容
        category_name: 类目名称
        category_url: 类目 URL
        page_url: 当前页面 URL
        page_number: 当前页码

    Returns:
        商品数据列表
    """
    if not html or len(html) < 100:
        logger.warning(f"页面内容过短 ({len(html)} 字节)，可能不是有效商品页")
        return []

    soup = BeautifulSoup(html, "lxml")
    products = []

    # 查找所有商品卡片
    cards = soup.select(".card-item.card-standard.js-product-data")
    if not cards:
        # 尝试备用选择器
        cards = soup.select("[data-product-id]")
        cards = [c for c in cards if c.get("data-product-id")]

    logger.debug(f"页面找到 {len(cards)} 个商品卡片")

    for card in cards:
        try:
            product = _parse_product_card(
                card, category_name, category_url, page_url, page_number
            )
            if product:
                products.append(product)
        except Exception as e:
            logger.warning(f"解析商品卡片时出错: {e}", exc_info=True)

    return products


def _parse_product_card(
    card: Tag,
    category_name: str,
    category_url: str,
    page_url: str,
    page_number: int,
) -> Optional[ProductItem]:
    """解析单个商品卡片"""

    # === 1. 提取 data- 属性（最稳定的数据源） ===
    product_id = card.get("data-product-id", "")
    title = card.get("data-name", "")
    product_url = card.get("data-url", "")
    position = int(card.get("data-position", 0))
    offer_id = card.get("data-offer-id", "")
    availability_id = card.get("data-availability-id", "")

    # 从 URL 提取 PNK（URL 格式: /pd/PNK/）
    pnk = ""
    if product_url:
        import re
        pnk_match = re.search(r'/pd/([A-Za-z0-9]+)/?', product_url)
        if pnk_match:
            pnk = pnk_match.group(1)

    # === 2. 提取收藏按钮内嵌 JSON（含精确价格） ===
    fav_btn = card.select_one(".add-to-favorites")
    fav_data = {}
    if fav_btn and fav_btn.get("data-product"):
        try:
            fav_data = json.loads(fav_btn.get("data-product"))
        except json.JSONDecodeError:
            pass

    # 如果 data- 属性没有 PNK，从收藏按钮获取
    if not pnk:
        pnk = fav_data.get("pnk", "")

    # 价格优先使用收藏按钮 JSON 中的数值
    price_numeric = fav_data.get("price")
    currency = fav_data.get("currency", "RON")

    # === 3. 提取价格 HTML 元素 ===
    # 当前价
    current_price_el = card.select_one(".product-new-price")
    price_current_raw = current_price_el.get_text(strip=True) if current_price_el else ""
    price_current = None
    if price_numeric is not None:
        price_current = round(float(price_numeric), 2)
    elif price_current_raw:
        price_current = parse_romanian_price(price_current_raw)

    # 原价 (PRP)
    old_price_el = card.select_one(".pricing.rrp-lp30d, .pricing")
    price_old_raw = ""
    price_old = None
    if old_price_el:
        old_text = old_price_el.get_text(strip=True)
        # 提取 "PRP: 69,99Lei" 中的数字部分
        price_old_raw = old_text
        price_old = parse_romanian_price(old_text)

    # 活动价（如果存在独立的活动价标签）
    promo_el = card.select_one("[class*='promo-price'], [class*='promotion-price']")
    price_promo_raw = promo_el.get_text(strip=True) if promo_el else ""
    price_promo = parse_romanian_price(price_promo_raw) if price_promo_raw else None

    # 折扣百分比
    discount_el = card.select_one("[class*='discount'], [class*='badge-discount']")
    discount_percent = None
    if discount_el:
        disc_text = discount_el.get_text(strip=True)
        disc_match = __import__('re').search(r'(\d+)', disc_text)
        if disc_match:
            discount_percent = int(disc_match.group(1))
    elif price_old and price_current and price_old > 0:
        discount_percent = round((1 - price_current / price_old) * 100)

    # === 4. 提取图片 ===
    main_image_url = _extract_main_image(card)

    # === 5. 提取评分 ===
    rating_el = card.select_one(".average-rating")
    rating = None
    if rating_el:
        try:
            rating = float(rating_el.get_text(strip=True).replace(",", "."))
        except (ValueError, TypeError):
            pass

    # 评论数
    review_count = None
    review_text_el = card.select_one(".star-rating-text")
    if review_text_el:
        review_text = review_text_el.get_text(strip=True)
        import re
        rev_match = re.search(r'(\d+)', review_text)
        if rev_match:
            review_count = int(rev_match.group(1))

    # === 6. 库存状态 ===
    stock_el = card.select_one("[class*='text-availability']")
    stock_text = stock_el.get_text(strip=True) if stock_el else ""
    availability = _map_availability(availability_id, stock_text)

    # === 7. 卖家信息 ===
    seller_el = card.select_one("[class*='badge-partner'], [class*='vendor-name'], [class*='seller']")
    seller = seller_el.get_text(strip=True) if seller_el else ""

    # === 8. 标签和活动 ===
    badges = []
    badge_els = card.select(".card-v2-badge, .badge, .commercial-badge")
    for b in badge_els:
        text = b.get_text(strip=True)
        if text and len(text) > 1:
            badges.append(text)

    # 去重
    badges = list(dict.fromkeys(badges))
    badge_str = ", ".join(badges)

    # 识别活动名称（通常是 commercial-badge）
    campaign_el = card.select_one(".commercial-badge")
    campaign_name = campaign_el.get_text(strip=True) if campaign_el else ""

    # === 9. 配送信息 ===
    shipping_el = card.select_one("[class*='shipping'], [class*='delivery']")
    shipping_text = shipping_el.get_text(strip=True) if shipping_el else ""

    # === 10. 品牌（从标题中提取第一个词作为品牌） ===
    brand = ""
    if title:
        # 常见品牌模式: "Mouse Logitech M171..." → Logitech
        parts = title.split()
        if len(parts) >= 2:
            # 如果第一部分是类目名，第二部分可能是品牌
            brand = parts[1] if parts[0].lower() == category_name.lower() else parts[0]

    # === 11. 构建额外字段（保留原始数据） ===
    extra = {}
    if fav_data:
        extra["favorite_data"] = fav_data
    if availability_id:
        extra["availability_id"] = availability_id

    # 将所有 data- 属性保存为额外字段
    for attr, value in card.attrs.items():
        if attr.startswith("data-") and attr not in [
            "data-product-id", "data-name", "data-url", "data-position",
            "data-offer-id", "data-availability-id", "data-zone",
        ]:
            extra[attr] = value

    # === 12. 构建商品对象 ===
    collected_at = datetime.now(timezone.utc).isoformat()

    product = ProductItem(
        category_name=category_name,
        category_url=category_url,
        source_page_url=page_url,
        page_number=page_number,
        position_in_page=position,
        product_id=product_id,
        pnk=pnk,
        sku="",  # eMAG 列表页没有 SKU
        offer_id=offer_id,
        title=title,
        product_url=product_url,
        price_current=price_current,
        price_current_raw=price_current_raw,
        price_old=price_old,
        price_old_raw=price_old_raw,
        price_promo=price_promo,
        price_promo_raw=price_promo_raw,
        discount_percent=discount_percent,
        currency=currency,
        availability=availability,
        stock_text=stock_text,
        seller=seller,
        brand=brand,
        badges=badge_str,
        campaign_name=campaign_name,
        shipping_text=shipping_text,
        rating=rating,
        review_count=review_count,
        main_image_url=main_image_url,
        collected_at=collected_at,
        http_status=200,
        parse_source="html+embedded_json",
        extra=extra,
    )

    return product


def _extract_main_image(card: Tag) -> str:
    """
    提取主图 URL，优先级:
    1. 对比按钮 data-img（原始大图 URL，去掉尺寸参数）
    2. <img> src（页面渲染图）
    """
    # 优先使用对比按钮中的图片（质量更高）
    compare_btn = card.select_one(".card-compare-btn")
    if compare_btn and compare_btn.get("data-img"):
        img_url = compare_btn.get("data-img")
        # 去掉尺寸限制参数，获取原图
        import re
        img_url = re.sub(r'\?width=\d+&height=\d+&hash=.*$', '', img_url)
        return img_url

    # 其次使用 img 标签
    img_el = card.select_one("img")
    if img_el:
        src = img_el.get("src", "")
        # 过滤占位图
        if src and not src.startswith("data:"):
            # 去掉尺寸限制参数
            import re
            src = re.sub(r'\?width=\d+&height=\d+&hash=.*$', '', src)
            return src

    return ""


def _map_availability(availability_id: str, stock_text: str) -> str:
    """根据 availability_id 和文本判断库存状态"""
    stock_lower = stock_text.lower()

    # 已知的 availability_id 映射
    avail_map = {
        "0": "stoc_limitat",   # 库存有限/未知
        "1": "stoc_epuizat",   # 缺货
        "2": "stoc_furnizor",  # 供应商库存
        "3": "in_stoc",        # 有库存
    }

    if availability_id in avail_map:
        return avail_map[availability_id]

    # 无 availability_id 时，根据文本判断
    if "stoc" in stock_lower:
        return "in_stoc"
    if "epuizat" in stock_lower or "indisponibil" in stock_lower:
        return "stoc_epuizat"
    if "furnizor" in stock_lower:
        return "stoc_furnizor"

    return "unknown"


def extract_next_page(html: str, current_url: str) -> Optional[str]:
    """
    从页面中提取下一页链接
    优先级: link[rel=next] > 分页组件 > URL 模式推断
    """
    soup = BeautifulSoup(html, "lxml")

    # 方法1: <link rel="next"> 标签
    next_link = soup.select_one('link[rel="next"]')
    if next_link and next_link.get("href"):
        href = next_link.get("href")
        return urljoin(current_url, href)

    # 方法2: 分页组件中的 "下一页" 链接
    pagination_next = soup.select_one('[class*="pagination"] a[href*="/p"]')
    # 找文本为"Pagina urmatoare"的链接
    for a in soup.select('[class*="pagination"] a'):
        if "urmatoare" in a.get_text(strip=True).lower():
            href = a.get("href")
            if href and href != "javascript:void(0)":
                return urljoin(current_url, href)

    # 方法3: URL 模式推断
    import re
    match = re.match(r'(https?://[^/]+/[^/]+)/c(\?.*)?$', current_url)
    if not match:
        match = re.match(r'(https?://[^/]+/[^/]+)/p(\d+)/c(\?.*)?$', current_url)

    return None


def extract_total_pages(html: str) -> Optional[int]:
    """从页面提取总页数"""
    soup = BeautifulSoup(html, "lxml")

    # 找分页组件中的最大页码
    max_page = 0
    for item in soup.select('[class*="pagination"] a'):
        try:
            page_num = int(item.get_text(strip=True))
            if page_num > max_page:
                max_page = page_num
        except ValueError:
            # 提取 "2 din 100" 格式
            text = item.get_text(strip=True)
            import re
            match = re.search(r'(\d+)\s*din\s*(\d+)', text)
            if match:
                return int(match.group(2))

    return max_page if max_page > 0 else None


def page_has_products(html: str) -> bool:
    """检查页面是否包含商品"""
    if not html or len(html) < 100:
        return False
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select('[data-product-id]')
    return len(cards) > 0
