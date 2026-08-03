"""
商品解析器单元测试
"""
import pytest
import os
from parser import (
    parse_product_listing,
    extract_next_page,
    page_has_products,
    _map_availability,
    _extract_main_image,
)


# 测试用的最小 HTML
MINI_PAGE = """<html><body>
<div class="card-item card-standard js-product-data"
     data-product-id="3372512"
     data-offer-id="1053929"
     data-name="Mouse Logitech M171, Wireless, Negru"
     data-url="https://www.emag.ro/mouse-logitech-m171/pd/DZHDC3BBM/"
     data-position="1"
     data-availability-id="3"
     data-category-name="Mouse">
  <div class="card-v2">
    <div class="card-v2-badges">
      <div class="card-v2-badge badge-genius"></div>
      <div class="card-v2-badge badge commercial-badge">Super Pret</div>
    </div>
    <button class="add-to-favorites"
            data-product='{"pnk":"DZHDC3BBM","productid":3372512,"offerid":1053929,"price":45.99,"currency":"RON","product_name":"Mouse Logitech M171"}'>
    </button>
    <button class="card-compare-btn"
            data-img="https://s13emagst.akamaized.net/products/3373/3372512/images/res_test.jpg?width=200&height=200&hash=ABC">
    </button>
    <div class="card-v2-content">
      <a class="card-v2-title">Mouse Logitech M171</a>
      <div class="star-rating-container">
        <span class="average-rating">4.78</span>
        <span class="star-rating-text">165 de review-uri</span>
      </div>
      <div class="card-v2-pricing">
        <p class="pricing rrp-lp30d">PRP: 69,99Lei</p>
        <p class="product-new-price">45,99Lei</p>
      </div>
      <div class="text-availability-in_stock">in stoc</div>
    </div>
  </div>
</div>
</body></html>"""


class TestParser:
    """商品解析测试"""

    def test_parse_basic_product(self):
        """测试基本商品解析"""
        products = parse_product_listing(MINI_PAGE)
        assert len(products) == 1
        p = products[0]

        assert p.product_id == "3372512"
        assert p.pnk == "DZHDC3BBM"
        assert p.title == "Mouse Logitech M171, Wireless, Negru"
        assert "DZHDC3BBM" in p.product_url
        assert p.position_in_page == 1

    def test_parse_price(self):
        """测试价格解析"""
        products = parse_product_listing(MINI_PAGE)
        p = products[0]

        assert p.price_current == pytest.approx(45.99)
        assert p.price_current_raw == "45,99Lei"
        assert p.price_old == pytest.approx(69.99)
        assert "PRP" in p.price_old_raw
        assert p.currency == "RON"

    def test_parse_rating(self):
        """测试评分解析"""
        products = parse_product_listing(MINI_PAGE)
        p = products[0]

        assert p.rating == pytest.approx(4.78)
        assert p.review_count == 165

    def test_parse_image(self):
        """测试主图提取"""
        products = parse_product_listing(MINI_PAGE)
        p = products[0]

        assert "res_test.jpg" in p.main_image_url
        assert "width=200" not in p.main_image_url  # 参数应被移除

    def test_parse_availability(self):
        """测试库存状态"""
        products = parse_product_listing(MINI_PAGE)
        p = products[0]

        assert p.availability == "in_stoc"
        assert "stoc" in p.stock_text.lower()

    def test_parse_badges(self):
        """测试标签提取"""
        products = parse_product_listing(MINI_PAGE)
        p = products[0]

        assert "Super Pret" in p.badges
        assert p.campaign_name == "Super Pret"

    def test_empty_html(self):
        """测试空 HTML"""
        products = parse_product_listing("")
        assert len(products) == 0

    def test_no_products(self):
        """测试无商品页面"""
        html = "<html><body><p>No products here</p></body></html>"
        products = parse_product_listing(html)
        assert len(products) == 0

    def test_extract_next_page_link_rel(self):
        """测试提取下一页 (link rel)"""
        html = '<html><head><link rel="next" href="/mouse/p2/c"></head><body></body></html>'
        next_url = extract_next_page(html, "https://www.emag.ro/mouse/c")
        assert next_url == "https://www.emag.ro/mouse/p2/c"

    def test_extract_next_page_none(self):
        """测试无下一页"""
        html = "<html><body></body></html>"
        next_url = extract_next_page(html, "https://www.emag.ro/mouse/c")
        assert next_url is None

    def test_page_has_products_true(self):
        """测试检测有商品"""
        assert page_has_products(MINI_PAGE) is True

    def test_page_has_products_false(self):
        """测试检测无商品"""
        assert page_has_products("<html></html>") is False

    def test_map_availability(self):
        """测试库存映射"""
        assert _map_availability("3", "in stoc") == "in_stoc"
        assert _map_availability("1", "") == "stoc_epuizat"
        assert _map_availability("0", "") == "stoc_limitat"
        assert _map_availability("99", "") == "unknown"

    def test_missing_fields(self):
        """测试字段缺失不丢商品"""
        html = """<html><body>
        <div class="card-item card-standard js-product-data"
             data-product-id="123"
             data-name="Test Product">
        </div>
        </body></html>"""
        products = parse_product_listing(html)
        assert len(products) == 1
        p = products[0]
        assert p.product_id == "123"
        assert p.title == "Test Product"
        assert p.price_current is None
        assert p.price_current_raw == ""

    def test_multiple_products(self):
        """测试多商品页面"""
        html = """<html><body>"""
        for i in range(3):
            html += f"""
            <div class="card-item card-standard js-product-data"
                 data-product-id="{i}"
                 data-name="Product {i}">
            </div>"""
        html += "</body></html>"

        products = parse_product_listing(html)
        assert len(products) == 3
        assert products[0].product_id == "0"

    def test_category_info_in_product(self):
        """测试类目信息传递"""
        products = parse_product_listing(
            MINI_PAGE,
            category_name="Mouse",
            category_url="https://www.emag.ro/mouse/c",
            page_url="https://www.emag.ro/mouse/c",
            page_number=2,
        )
        p = products[0]
        assert p.category_name == "Mouse"
        assert p.category_url == "https://www.emag.ro/mouse/c"
        assert p.page_number == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
