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
import re as _re
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
    """解析 eMAG 商品列表页 HTML, 提取所有商品数据"""
    if not html or len(html) < 100:
        logger.warning(f"页面内容过短 ({len(html)} 字节)")
        return []

    soup = BeautifulSoup(html, "lxml")
    products = []

    cards = soup.select(".card-item.card-standard.js-product-data")
    if not cards:
        cards = soup.select("[data-product-id]")
        cards = [c for c in cards if c.get("data-product-id")]

    logger.debug(f"页面找到 {len(cards)} 个商品卡片")

    for idx, card in enumerate(cards):
        try:
            product = _parse_product_card(
                card, category_name, category_url, page_url, page_number
            )
            if product:
                products.append(product)
        except Exception as e:
            pos_text = card.get("data-position", str(idx + 1))
            logger.warning(
                f"解析商品卡片失败 [类目={category_name}, 页={page_number}, 位置={pos_text}]: {e}",
                exc_info=True,
            )

    return products


def _parse_product_card(
    card: Tag,
    category_name: str,
    category_url: str,
    page_url: str,
    page_number: int,
) -> Optional[ProductItem]:
    """解析单个商品卡片。单个字段失败不影响整件商品。"""

    # === 1. data- 属性 ===
    product_id = card.get("data-product-id", "")
    title = card.get("data-name", "")
    product_url = card.get("data-url", "")
    offer_id = card.get("data-offer-id", "")
    availability_id = card.get("data-availability-id", "")

    # position 转换失败不能丢弃商品
    position = 0
    try:
        pos_str = card.get("data-position", "0")
        position = int(pos_str) if pos_str else 0
    except (ValueError, TypeError):
        position = 0

    # 标题备用来源
    if not title:
        title_el = card.select_one(".card-v2-title")
        if title_el:
            title = title_el.get_text(strip=True)

    # 商品 URL 转为绝对 URL
    product_url = _make_absolute(product_url, page_url)

    # 从 URL 提取 PNK
    pnk = ""
    if product_url:
        pnk_match = _re.search(r'/pd/([A-Za-z0-9]+)/?', product_url)
        if pnk_match:
            pnk = pnk_match.group(1)

    # === 2. 收藏按钮内嵌 JSON ===
    fav_btn = card.select_one(".add-to-favorites")
    fav_data = {}
    if fav_btn and fav_btn.get("data-product"):
        try:
            fav_data = json.loads(fav_btn.get("data-product"))
        except json.JSONDecodeError:
            pass

    if not pnk:
        pnk = fav_data.get("pnk", "")

    currency = fav_data.get("currency", "RON")

    # 价格优先使用收藏JSON中的数值, 但异常时不能丢弃商品
    price_numeric = None
    try:
        raw_price = fav_data.get("price")
        if raw_price is not None:
            price_numeric = round(float(raw_price), 2)
    except (ValueError, TypeError):
        price_numeric = None

    # === 3. 价格 HTML 元素 ===
    # 当前价: 只用精确选择器 .product-new-price
    current_price_el = card.select_one(".product-new-price")
    price_current_raw = current_price_el.get_text(strip=True) if current_price_el else ""
    price_current = price_numeric
    if price_current is None and price_current_raw:
        price_current = parse_romanian_price(price_current_raw)

    # 原价 (PRP): 只用精确选择器 .pricing.rrp-lp30d, 不用宽泛的 .pricing
    old_price_el = card.select_one(".pricing.rrp-lp30d")
    price_old_raw = ""
    price_old = None
    if old_price_el:
        old_text = old_price_el.get_text(strip=True)
        price_old_raw = old_text
        price_old = parse_romanian_price(old_text)

    # 活动价 (使用精确选择器)
    promo_el = card.select_one("[class*='promo-price'], [class*='promotion-price']")
    price_promo_raw = promo_el.get_text(strip=True) if promo_el else ""
    price_promo = parse_romanian_price(price_promo_raw) if price_promo_raw else None

    # 折扣百分比 (不猜测, 只在有明确折扣徽章时提取)
    discount_percent = None
    discount_el = card.select_one("[class*='discount'], [class*='badge-discount']")
    if discount_el:
        disc_text = discount_el.get_text(strip=True)
        disc_match = _re.search(r'(\d+)', disc_text)
        if disc_match:
            discount_percent = int(disc_match.group(1))
    elif price_old and price_current and price_old > 0:
        discount_percent = round((1 - price_current / price_old) * 100)

    # === 4. 图片 ===
    main_image_url = _extract_main_image(card)

    # === 5. 评分 ===
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
        rev_match = _re.search(r'(\d+)', review_text)
        if rev_match:
            review_count = int(rev_match.group(1))

    # === 6. 库存状态 (修正判断顺序: 先判断缺货) ===
    stock_el = card.select_one("[class*='text-availability']")
    stock_text = stock_el.get_text(strip=True) if stock_el else ""
    availability = _map_availability(availability_id, stock_text)

    # === 7. 卖家 (只有明确字段时才填写) ===
    seller_el = card.select_one("[class*='badge-partner'], [class*='vendor-name'], [class*='seller']")
    seller = seller_el.get_text(strip=True) if seller_el else ""

    # === 8. 标签 ===
    badges = []
    badge_els = card.select(".card-v2-badge, .badge, .commercial-badge")
    for b in badge_els:
        text = b.get_text(strip=True)
        if text and len(text) > 1:
            badges.append(text)
    badge_str = ", ".join(list(dict.fromkeys(badges)))

    campaign_el = card.select_one(".commercial-badge")
    campaign_name = campaign_el.get_text(strip=True) if campaign_el else ""

    # === 9. 配送 ===
    shipping_el = card.select_one("[class*='shipping'], [class*='delivery']")
    shipping_text = shipping_el.get_text(strip=True) if shipping_el else ""

    # === 10. 品牌 (只有页面存在明确品牌字段时才填写, 禁止从标题猜测) ===
    brand = ""
    brand_el = card.select_one("[class*='brand'], [itemprop='brand'], [data-brand]")
    if brand_el:
        brand = brand_el.get_text(strip=True)

    # === 11. 额外字段 ===
    extra = {}
    if fav_data:
        extra["favorite_data"] = fav_data
    if availability_id:
        extra["availability_id"] = availability_id
    for attr, value in card.attrs.items():
        if attr.startswith("data-") and attr not in [
            "data-product-id", "data-name", "data-url", "data-position",
            "data-offer-id", "data-availability-id", "data-zone",
        ]:
            extra[attr] = value

    # === 12. 构建商品对象 ===
    collected_at = datetime.now(timezone.utc).isoformat()

    return ProductItem(
        category_name=category_name,
        category_url=category_url,
        source_page_url=page_url,
        page_number=page_number,
        position_in_page=position,
        product_id=product_id,
        pnk=pnk,
        sku="",
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


def _make_absolute(url: str, base_url: str) -> str:
    """将相对URL转为绝对URL"""
    if not url:
        return ""
    if url.startswith("http"):
        return url
    if base_url:
        return urljoin(base_url, url)
    return url


def _extract_main_image(card: Tag) -> str:
    """提取主图 URL, 优先级: 对比按钮 data-img > img src"""
    compare_btn = card.select_one(".card-compare-btn")
    if compare_btn and compare_btn.get("data-img"):
        img_url = compare_btn.get("data-img")
        img_url = _re.sub(r'\?width=\d+&height=\d+&hash=.*$', '', img_url)
        return img_url

    img_el = card.select_one("img")
    if img_el:
        src = img_el.get("src", "")
        if src and not src.startswith("data:"):
            src = _re.sub(r'\?width=\d+&height=\d+&hash=.*$', '', src)
            return src

    return ""


def _map_availability(availability_id: str, stock_text: str) -> str:
    """
    库存状态映射。判断顺序:
    1. epuizat/indisponibil (缺货)
    2. furnizor (供应商)
    3. in stoc (有库存)
    """
    stock_lower = stock_text.lower()

    # 先判断缺货
    if "epuizat" in stock_lower or "indisponibil" in stock_lower:
        return "stoc_epuizat"

    # 再判断供应商
    if "furnizor" in stock_lower:
        return "stoc_furnizor"

    # 最后判断有库存
    if "stoc" in stock_lower:
        return "in_stoc"

    # availability_id 映射
    avail_map = {
        "0": "stoc_limitat",
        "1": "stoc_epuizat",
        "2": "stoc_furnizor",
        "3": "in_stoc",
    }
    if availability_id in avail_map:
        return avail_map[availability_id]

    return "unknown"


def extract_next_page(html: str, current_url: str) -> Optional[str]:
    """从页面中提取下一页链接"""
    soup = BeautifulSoup(html, "lxml")

    # <link rel="next">
    next_link = soup.select_one('link[rel="next"]')
    if next_link and next_link.get("href"):
        return urljoin(current_url, next_link.get("href"))

    # 分页组件 "Pagina urmatoare"
    for a in soup.select('[class*="pagination"] a'):
        if "urmatoare" in a.get_text(strip=True).lower():
            href = a.get("href")
            if href and href != "javascript:void(0)":
                return urljoin(current_url, href)

    return None


def page_has_products(html: str) -> bool:
    """检查页面是否包含商品"""
    if not html or len(html) < 100:
        return False
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select('[data-product-id]')
    return len(cards) > 0
