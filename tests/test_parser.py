"""
商品解析器单元测试 V2.0.1
"""
import pytest
from parser import (
    parse_product_listing, extract_next_page, page_has_products,
    _map_availability, _extract_main_image,
)


MINI_PAGE = """<html><body>
<div class="card-item card-standard js-product-data"
     data-product-id="3372512" data-offer-id="1053929"
     data-name="Mouse Logitech M171, Wireless, Negru"
     data-url="https://www.emag.ro/mouse-logitech-m171/pd/DZHDC3BBM/"
     data-position="1" data-availability-id="3" data-category-name="Mouse">
  <div class="card-v2">
    <button class="add-to-favorites"
            data-product='{"pnk":"DZHDC3BBM","productid":3372512,"offerid":1053929,"price":45.99,"currency":"RON"}'>
    </button>
    <button class="card-compare-btn"
            data-img="https://s13emagst.akamaized.net/products/3373/3372512/images/res_test.jpg?width=200&height=200&hash=ABC">
    </button>
    <div class="card-v2-content">
      <a class="card-v2-title">Mouse Logitech M171</a>
      <span class="average-rating">4.78</span>
      <span class="star-rating-text">165 de review-uri</span>
      <p class="pricing rrp-lp30d">PRP: 69,99Lei</p>
      <p class="product-new-price">45,99Lei</p>
      <div class="text-availability-in_stock">in stoc</div>
    </div>
  </div>
</div>
</body></html>"""


class TestParser:
    def test_basic_product(self):
        products = parse_product_listing(MINI_PAGE)
        assert len(products) == 1
        p = products[0]
        assert p.product_id == "3372512"
        assert p.pnk == "DZHDC3BBM"
        assert "DZHDC3BBM" in p.product_url
        assert p.position_in_page == 1

    def test_price(self):
        products = parse_product_listing(MINI_PAGE)
        p = products[0]
        assert p.price_current == pytest.approx(45.99)
        assert p.price_current_raw == "45,99Lei"
        assert p.price_old == pytest.approx(69.99)
        assert "PRP" in p.price_old_raw

    def test_rating(self):
        products = parse_product_listing(MINI_PAGE)
        p = products[0]
        assert p.rating == pytest.approx(4.78)
        assert p.review_count == 165

    def test_image(self):
        products = parse_product_listing(MINI_PAGE)
        p = products[0]
        assert "res_test.jpg" in p.main_image_url
        assert "width=200" not in p.main_image_url

    def test_availability_order_epuizat_first(self):
        """库存判断: 先判断缺货"""
        assert _map_availability("3", "stoc epuizat") == "stoc_epuizat"
        assert _map_availability("3", "indisponibil") == "stoc_epuizat"
        assert _map_availability("", "stoc furnizor") == "stoc_furnizor"
        assert _map_availability("", "in stoc") == "in_stoc"
        assert _map_availability("1", "") == "stoc_epuizat"

    def test_brand_empty_when_no_field(self):
        """无明确品牌字段时 brand 为空"""
        products = parse_product_listing(MINI_PAGE)
        assert products[0].brand == ""

    def test_brand_from_field(self):
        """有品牌字段时提取"""
        html = """<html><body>
        <div class="card-item card-standard js-product-data"
             data-product-id="123" data-name="Test">
          <span class="brand">Logitech</span>
        </div></body></html>"""
        products = parse_product_listing(html)
        assert products[0].brand == "Logitech"

    def test_title_fallback(self):
        """标题备用来源: .card-v2-title"""
        html = """<html><body>
        <div class="card-item card-standard js-product-data"
             data-product-id="123">
          <a class="card-v2-title">Fallback Title</a>
        </div></body></html>"""
        products = parse_product_listing(html)
        assert products[0].title == "Fallback Title"

    def test_url_absolute(self):
        """商品 URL 转为绝对 URL"""
        html = """<html><body>
        <div class="card-item card-standard js-product-data"
             data-product-id="123" data-name="T"
             data-url="/mouse-test/pd/ABC123/">
        </div></body></html>"""
        products = parse_product_listing(html, page_url="https://www.emag.ro/mouse/c")
        assert products[0].product_url == "https://www.emag.ro/mouse-test/pd/ABC123/"

    def test_price_selector_only_specific(self):
        """原价选择器只用 .pricing.rrp-lp30d, 不用宽泛 .pricing"""
        html = """<html><body>
        <div class="card-item card-standard js-product-data"
             data-product-id="123" data-name="T">
          <p class="pricing">45,99Lei</p>
          <p class="product-new-price">45,99Lei</p>
        </div></body></html>"""
        products = parse_product_listing(html)
        # .pricing without .rrp-lp30d should NOT be picked up as old price
        assert products[0].price_old is None
        assert products[0].price_current == pytest.approx(45.99)

    def test_position_failure_does_not_drop_product(self):
        """position 转换失败不丢商品"""
        html = """<html><body>
        <div class="card-item card-standard js-product-data"
             data-product-id="123" data-name="T"
             data-position="invalid">
        </div></body></html>"""
        products = parse_product_listing(html)
        assert len(products) == 1
        assert products[0].position_in_page == 0

    def test_price_json_failure_does_not_drop(self):
        """收藏JSON价格异常不丢商品"""
        html = """<html><body>
        <div class="card-item card-standard js-product-data"
             data-product-id="123" data-name="T">
          <button class="add-to-favorites"
                  data-product='{"price": "N/A", "currency": "RON"}'>
          </button>
          <p class="product-new-price">45,99Lei</p>
        </div></body></html>"""
        products = parse_product_listing(html)
        assert len(products) == 1
        assert products[0].price_current == pytest.approx(45.99)

    def test_missing_fields(self):
        html = """<html><body>
        <div class="card-item card-standard js-product-data"
             data-product-id="123" data-name="Test">
        </div></body></html>"""
        products = parse_product_listing(html)
        assert len(products) == 1
        assert products[0].price_current is None

    def test_empty_html(self):
        assert len(parse_product_listing("")) == 0

    def test_next_page(self):
        html = '<html><head><link rel="next" href="/mouse/p2/c"></head><body></body></html>'
        assert extract_next_page(html, "https://www.emag.ro/mouse/c") == "https://www.emag.ro/mouse/p2/c"

    def test_no_next_page(self):
        assert extract_next_page("<html></html>", "https://www.emag.ro/mouse/c") is None

    def test_has_products(self):
        assert page_has_products(MINI_PAGE) is True
        assert page_has_products("<html></html>") is False
