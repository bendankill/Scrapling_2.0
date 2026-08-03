"""
罗马尼亚价格解析单元测试
"""
import pytest
from utils import parse_romanian_price


class TestRomanianPrices:
    """罗马尼亚价格格式解析"""

    def test_simple_price(self):
        """简单价格: 45,99Lei"""
        assert parse_romanian_price("45,99Lei") == pytest.approx(45.99)

    def test_price_no_currency(self):
        """无货币符号: 45,99"""
        assert parse_romanian_price("45,99") == pytest.approx(45.99)

    def test_price_with_thousands(self):
        """千位分隔: 1.234,56 Lei"""
        assert parse_romanian_price("1.234,56 Lei") == pytest.approx(1234.56)

    def test_price_with_prp(self):
        """PRP 前缀: PRP: 69,99Lei"""
        assert parse_romanian_price("PRP: 69,99Lei") == pytest.approx(69.99)

    def test_integer_price(self):
        """整数价格: 100"""
        assert parse_romanian_price("100") == pytest.approx(100.0)

    def test_large_price(self):
        """大额价格: 12.345,67 Lei"""
        assert parse_romanian_price("12.345,67 Lei") == pytest.approx(12345.67)

    def test_single_digit_decimal(self):
        """一位小数"""
        assert parse_romanian_price("45,9Lei") == pytest.approx(45.9)

    def test_zero_price(self):
        """零价格"""
        assert parse_romanian_price("0,00Lei") == pytest.approx(0.0)

    def test_empty_string(self):
        """空字符串"""
        assert parse_romanian_price("") is None

    def test_none(self):
        """None 输入"""
        assert parse_romanian_price(None) is None

    def test_no_digits(self):
        """无数字"""
        assert parse_romanian_price("PRP: ") is None

    def test_decimal_only(self):
        """只有小数部分: ,99"""
        # 不标准的格式，应安全处理
        result = parse_romanian_price(",99")
        # 可能无法解析，返回 None
        assert result is None or result >= 0

    def test_multiple_dots(self):
        """多个点号: 1.234.567,89"""
        result = parse_romanian_price("1.234.567,89")
        assert result == pytest.approx(1234567.89)

    def test_ron_currency(self):
        """RON 货币: 45,99 RON"""
        assert parse_romanian_price("45,99 RON") == pytest.approx(45.99)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
