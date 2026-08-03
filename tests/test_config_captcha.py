"""
TXT配置加载、验证码检测、参数验证测试 V2.0.1
"""
import json
import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils import (
    load_txt_categories, detect_captcha, CaptchaRequiredError,
    get_product_key, write_atomic_json,
    EXIT_CONFIG_ERROR, EXIT_CAPTCHA, EXIT_NETWORK_ERROR,
)

# ============================================================
# TXT 类目配置测试
# ============================================================
class TestTxtCategories:
    def test_single_url(self, tmp_path):
        f = tmp_path / "categories.txt"
        f.write_text("https://www.emag.ro/mouse/c", encoding="utf-8")
        cats = load_txt_categories(str(f))
        assert len(cats) == 1
        assert cats[0]["name"] == "Mouse"
        assert cats[0]["url"] == "https://www.emag.ro/mouse/c"

    def test_multiple_urls(self, tmp_path):
        f = tmp_path / "categories.txt"
        f.write_text(
            "https://www.emag.ro/mouse/c\n"
            "https://www.emag.ro/tastaturi/c\n",
            encoding="utf-8"
        )
        cats = load_txt_categories(str(f))
        assert len(cats) == 2

    def test_ignore_empty_lines(self, tmp_path):
        f = tmp_path / "categories.txt"
        f.write_text(
            "\n\nhttps://www.emag.ro/mouse/c\n\n\n",
            encoding="utf-8"
        )
        cats = load_txt_categories(str(f))
        assert len(cats) == 1

    def test_ignore_comments(self, tmp_path):
        f = tmp_path / "categories.txt"
        f.write_text(
            "# This is a comment\n"
            "https://www.emag.ro/mouse/c\n"
            "  # indented comment\n",
            encoding="utf-8"
        )
        cats = load_txt_categories(str(f))
        assert len(cats) == 1

    def test_trim_whitespace(self, tmp_path):
        f = tmp_path / "categories.txt"
        f.write_text("  https://www.emag.ro/mouse/c  \n", encoding="utf-8")
        cats = load_txt_categories(str(f))
        assert cats[0]["url"] == "https://www.emag.ro/mouse/c"

    def test_duplicate_url_skip(self, tmp_path):
        f = tmp_path / "categories.txt"
        f.write_text(
            "https://www.emag.ro/mouse/c\n"
            "https://www.emag.ro/mouse/c\n",
            encoding="utf-8"
        )
        cats = load_txt_categories(str(f))
        assert len(cats) == 1

    def test_reject_non_emag_domain(self, tmp_path):
        f = tmp_path / "categories.txt"
        f.write_text("https://www.amazon.com/mouse/c\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_txt_categories(str(f))

    def test_reject_product_detail_url(self, tmp_path):
        f = tmp_path / "categories.txt"
        f.write_text("https://www.emag.ro/mouse/pd/ABC123/\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_txt_categories(str(f))

    def test_reject_invalid_path(self, tmp_path):
        f = tmp_path / "categories.txt"
        f.write_text("https://www.emag.ro/mouse\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_txt_categories(str(f))

    def test_empty_config_raises(self, tmp_path):
        f = tmp_path / "categories.txt"
        f.write_text("# only comments\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_txt_categories(str(f))

    def test_category_name_from_url(self, tmp_path):
        f = tmp_path / "categories.txt"
        f.write_text("https://www.emag.ro/casti-audio/c\n", encoding="utf-8")
        cats = load_txt_categories(str(f))
        assert "casti" in cats[0]["name"].lower() or "Casti" in cats[0]["name"]

    def test_query_params_allowed(self, tmp_path):
        f = tmp_path / "categories.txt"
        f.write_text("https://www.emag.ro/mouse/c?ref=hp_menu\n", encoding="utf-8")
        cats = load_txt_categories(str(f))
        assert len(cats) == 1


# ============================================================
# Captcha / WAF 检测测试
# ============================================================
AWS_WAF_HTML = """<html lang="ro"><head><title>eMAG Captcha</title></head>
<body><div id="b"></div><script>const cs="awswaf-captcha";</script></body></html>"""

AWS_WAF_511 = """<html><head><title>eMAG</title></head>
<body><p>Traffic</p><script>aws-waf-token</script></body></html>"""

NORMAL_PRODUCT_PAGE = """<html><body>
<div class="card-item card-standard js-product-data" data-product-id="123" data-name="Test">
</div></body></html>"""

CLOUDFLARE_CHALLENGE = """<html><head><title>Just a moment...</title></head>
<body>Checking your browser. Enable JavaScript.</body></html>"""


class TestCaptchaDetection:
    def test_aws_waf_captcha_page(self):
        err = detect_captcha(AWS_WAF_HTML, 200, "https://www.emag.ro/mouse/c")
        assert err is not None
        assert "AWS_WAF" in err.captcha_type

    def test_http_511_with_waf_markers(self):
        err = detect_captcha(AWS_WAF_511, 511, "https://www.emag.ro/mouse/c")
        assert err is not None
        assert err.status_code == 511

    def test_normal_page_no_captcha(self):
        err = detect_captcha(NORMAL_PRODUCT_PAGE, 200, "https://www.emag.ro/mouse/c")
        assert err is None

    def test_403_with_captcha(self):
        html = "<html><head><title>Access Denied</title></head><body>captcha verification</body></html>"
        err = detect_captcha(html, 403, "https://www.emag.ro/mouse/c")
        assert err is not None

    def test_403_normal_block_no_captcha(self):
        """普通 403 (非验证码) 不应触实验证码检测"""
        html = "<html><body>Forbidden</body></html>"
        err = detect_captcha(html, 403, "https://www.emag.ro/mouse/c")
        # 403 with "Forbidden" but no captcha keywords
        assert err is None

    def test_200_with_emag_captcha_title(self):
        html = "<html><head><title>eMAG Captcha</title></head><body></body></html>"
        err = detect_captcha(html, 200, "https://www.emag.ro/mouse/c")
        assert err is not None

    def test_empty_html(self):
        assert detect_captcha("", 200, "") is None

    def test_captcha_error_is_exception(self):
        err = CaptchaRequiredError(511, "Mouse", 1, "url", "TEST", "evidence")
        assert isinstance(err, Exception)
        assert err.status_code == 511
        assert err.captcha_type == "TEST"


# ============================================================
# 产品唯一键测试
# ============================================================
class TestProductKey:
    def test_pnk_priority(self):
        p1 = {"pnk": "ABC", "product_id": "123"}
        p2 = {"pnk": "ABC", "product_id": "456"}
        assert get_product_key(p1) == get_product_key(p2)

    def test_product_id_fallback(self):
        p = {"pnk": "", "product_id": "3372512"}
        assert get_product_key(p) == "pid:3372512"

    def test_url_fallback(self):
        p = {"pnk": "", "product_id": "", "product_url": "https://www.emag.ro/test/pd/ABC/"}
        key = get_product_key(p)
        assert key.startswith("url:")

    def test_image_url_fallback(self):
        p = {"pnk": "", "product_id": "", "product_url": "",
             "main_image_url": "https://example.com/img.jpg"}
        key = get_product_key(p)
        assert key.startswith("img:")

    def test_title_fallback_last_resort(self):
        p = {"pnk": "", "product_id": "", "product_url": "", "main_image_url": "",
             "title": "Some Product"}
        key = get_product_key(p)
        assert key.startswith("title:")


# ============================================================
# 参数验证测试
# ============================================================
class TestArgValidation:
    def test_pages_zero_rejected(self, capsys):
        """--pages 0 被拒绝"""
        sys.argv = ["main.py", "--pages", "0"]
        with pytest.raises(SystemExit) as exc:
            from main import parse_args, validate_positive
            args = parse_args()
            validate_positive(args.pages, "--pages")
        assert exc.value.code != 0

    def test_pages_negative_rejected(self, capsys):
        sys.argv = ["main.py", "--pages", "-1"]
        with pytest.raises(SystemExit) as exc:
            from main import parse_args, validate_positive
            args = parse_args()
            validate_positive(args.pages, "--pages")
        assert exc.value.code != 0

    def test_category_workers_zero_rejected(self):
        with pytest.raises(SystemExit) as exc:
            from main import validate_positive
            validate_positive(0, "--category-workers")
        assert exc.value.code != 0

    def test_max_in_flight_negative_rejected(self):
        with pytest.raises(SystemExit) as exc:
            from main import validate_positive
            validate_positive(-1, "--max-in-flight")
        assert exc.value.code != 0


# ============================================================
# JSON 原子写入测试
# ============================================================
class TestAtomicJson:
    def test_write_and_load(self, tmp_path):
        f = tmp_path / "products.json"
        data = [{"name": "test", "price": 45.99}]
        write_atomic_json(str(f), data)
        assert f.exists()
        loaded = json.loads(f.read_text(encoding="utf-8"))
        assert loaded == data

    def test_no_tmp_left(self, tmp_path):
        f = tmp_path / "products.json"
        write_atomic_json(str(f), [{"x": 1}])
        tmp = tmp_path / "products.json.tmp"
        assert not tmp.exists()

    def test_extra_is_object(self, tmp_path):
        f = tmp_path / "products.json"
        data = [{"title": "Test", "extra": {"key": "value"}}]
        write_atomic_json(str(f), data)
        loaded = json.loads(f.read_text(encoding="utf-8"))
        assert isinstance(loaded[0]["extra"], dict)
        assert loaded[0]["extra"]["key"] == "value"

    def test_top_level_is_list(self, tmp_path):
        f = tmp_path / "products.json"
        write_atomic_json(str(f), [{"a": 1}])
        loaded = json.loads(f.read_text(encoding="utf-8"))
        assert isinstance(loaded, list)


# ============================================================
# 退出码测试
# ============================================================
class TestExitCodes:
    def test_exit_codes_defined(self):
        assert EXIT_CONFIG_ERROR == 1
        assert EXIT_NETWORK_ERROR == 2
        assert EXIT_CAPTCHA == 3
        assert EXIT_CAPTCHA != 0

    def test_captcha_not_success(self):
        """验证码退出码不是 0"""
        assert EXIT_CAPTCHA != 0
