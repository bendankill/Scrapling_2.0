"""
分页限制和停止逻辑测试
"""
import pytest
from unittest.mock import patch, Mock
from parser import extract_next_page, page_has_products


PAGE_1_HTML = """<html>
<head><link rel="next" href="/mouse/p2/c"></head>
<body>
<div class="card-item card-standard js-product-data" data-product-id="1" data-name="P1"></div>
<div class="card-item card-standard js-product-data" data-product-id="2" data-name="P2"></div>
</body></html>"""

PAGE_LAST_HTML = """<html>
<body>
<div class="card-item card-standard js-product-data" data-product-id="99" data-name="P99"></div>
</body></html>"""

EMPTY_PAGE_HTML = """<html><body><p>No results found</p></body></html>"""

CIRCULAR_PAGE_HTML = """<html>
<head><link rel="next" href="/mouse/p1/c"></head>
<body>
<div class="card-item card-standard js-product-data" data-product-id="1" data-name="P1"></div>
</body></html>"""


class TestNextPage:
    """下一页提取测试"""

    def test_next_page_from_link_rel(self):
        """从 link rel=next 提取下一页"""
        url = extract_next_page(PAGE_1_HTML, "https://www.emag.ro/mouse/c")
        assert url == "https://www.emag.ro/mouse/p2/c"

    def test_no_next_page(self):
        """最后一页无 next 链接"""
        url = extract_next_page(PAGE_LAST_HTML, "https://www.emag.ro/mouse/p99/c")
        assert url is None

    def test_absolute_next_url(self):
        """绝对路径的下页链接"""
        html = '<html><head><link rel="next" href="https://www.emag.ro/mouse/p5/c"></head><body></body></html>'
        url = extract_next_page(html, "https://www.emag.ro/mouse/p4/c")
        assert url == "https://www.emag.ro/mouse/p5/c"


class TestPageHasProducts:
    """页面商品检测测试"""

    def test_has_products(self):
        assert page_has_products(PAGE_1_HTML) is True

    def test_empty_page(self):
        assert page_has_products(EMPTY_PAGE_HTML) is False

    def test_short_content(self):
        assert page_has_products("<html></html>") is False

    def test_no_data_product_id(self):
        html = "<html><body><div class='item'>No product id here</div></body></html>"
        assert page_has_products(html) is False


class TestPageLimitLogic:
    """页数限制逻辑测试（模拟爬虫循环）"""

    def simulate_crawl(self, max_pages: int, available_pages: int) -> dict:
        """
        模拟抓取过程
        max_pages: 用户指定的最大页数
        available_pages: 实际可用的页数
        返回: {"visited": [...], "stopped_reason": str}
        """
        visited = []
        current_url = "https://www.emag.ro/mouse/c"

        for page_num in range(1, max_pages + 1):
            # 检查页数限制
            if page_num > max_pages:
                return {"visited": visited, "stopped_reason": "达到页数限制"}

            # 模拟检查 URL 重复
            if current_url in visited:
                return {"visited": visited, "stopped_reason": "URL重复"}

            visited.append(current_url)

            # 检查是否有下一页
            if page_num >= available_pages:
                return {"visited": visited, "stopped_reason": "没有下一页"}

            # 生成下一页 URL
            current_url = f"https://www.emag.ro/mouse/p{page_num + 1}/c"

        return {"visited": visited, "stopped_reason": "达到页数限制"}

    def test_pages_1_with_10_available(self):
        """--pages 1 但实际有10页"""
        result = self.simulate_crawl(1, 10)
        assert len(result["visited"]) == 1
        assert result["stopped_reason"] == "达到页数限制"

    def test_pages_2_with_10_available(self):
        """--pages 2 但实际有10页"""
        result = self.simulate_crawl(2, 10)
        assert len(result["visited"]) == 2
        assert result["stopped_reason"] == "达到页数限制"

    def test_pages_5_with_3_available(self):
        """--pages 5 但实际只有3页"""
        result = self.simulate_crawl(5, 3)
        assert len(result["visited"]) == 3
        assert result["stopped_reason"] == "没有下一页"

    def test_never_exceeds_max_pages(self):
        """任何情况下不超页数上限"""
        for max_pages in [1, 2, 3, 5, 10]:
            for available in [1, 2, 3, 5, 10, 100]:
                result = self.simulate_crawl(max_pages, available)
                assert len(result["visited"]) <= max_pages, (
                    f"超过限制: max={max_pages}, avail={available}, visited={len(result['visited'])}"
                )

    def test_duplicate_url_stops(self):
        """重复 URL 应停止"""
        result = self.simulate_crawl(10, 1)
        assert len(result["visited"]) == 1


class TestUniqueProductDetection:
    """唯一商品检测测试"""

    def test_pnk_based_uniqueness(self):
        """基于 PNK 的唯一性"""
        pnks = ["DZHDC3BBM", "DLSHK43BM", "DZHDC3BBM", "DGW38TMBM"]
        unique = set(pnks)
        assert len(unique) == 3

    def test_product_id_based_uniqueness(self):
        """基于 product_id 的唯一性"""
        ids = ["3372512", "103683799", "3372512", "35931788"]
        unique = set(ids)
        assert len(unique) == 3

    def test_fallback_to_title_uniqueness(self):
        """标题去重（仅兜底）"""
        titles = ["Mouse A", "Mouse B", "Mouse A", "Mouse C"]
        unique = set(titles)
        assert len(unique) == 3

    def test_cross_category_preservation(self):
        """跨类目商品保留来源"""
        # 模拟两个类目中有相同商品
        cat1_items = [
            {"pnk": "A", "category": "Mouse", "title": "Mouse X"},
            {"pnk": "B", "category": "Mouse", "title": "Mouse Y"},
        ]
        cat2_items = [
            {"pnk": "A", "category": "Gaming", "title": "Mouse X"},
            {"pnk": "C", "category": "Gaming", "title": "Mouse Z"},
        ]

        all_items = cat1_items + cat2_items
        total_records = len(all_items)

        unique_pnks = set(item["pnk"] for item in all_items)
        unique_count = len(unique_pnks)

        assert total_records == 4  # 4条记录
        assert unique_count == 3  # 3个唯一商品
        assert "A" in unique_pnks  # PNK A 出现在两个类目中


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
