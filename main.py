#!/usr/bin/env python3
"""
eMAG 商品列表爬虫 V2.0.3
基于 Scrapling 纯 HTTP Fetcher, 支持多类目并发抓取

使用方法:
  python main.py --pages 1 --no-images
  python main.py --pages 3 --category-workers 2
  python main.py --all-pages

退出码:
  0   成功且抓到商品
  1   配置或参数错误
  2   网络、解析或全部页面失败
  3   检测到验证码/WAF, 需要人工处理
  130 用户中断 (Ctrl+C)
"""
import argparse
import logging
import os
import sys

from utils import (
    EXIT_SUCCESS, EXIT_CONFIG_ERROR, EXIT_NETWORK_ERROR,
    EXIT_CAPTCHA, EXIT_INTERRUPT, WafBlockError,
)


def parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="eMAG 商品列表爬虫 V2.0.3 — 纯 HTTP 模式",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --pages 1 --no-images
  %(prog)s --pages 2 --no-images
  %(prog)s --pages 3
  %(prog)s --all-pages
  %(prog)s --pages 10 --category-workers 2 --image-workers 8 --max-in-flight 16
        """,
    )

    parser.add_argument(
        "--config",
        default=os.path.join(os.path.dirname(__file__), "config", "categories.txt"),
        help="类目配置文件路径 (默认: config/categories.txt)",
    )

    page_group = parser.add_mutually_exclusive_group()
    page_group.add_argument("--pages", type=int, default=1,
                          help="每个类目最大抓取页数 (默认: 1, 必须 > 0)")
    page_group.add_argument("--all-pages", action="store_true",
                          help="抓取每个类目最多20页 (与 --pages 互斥)")

    parser.add_argument("--no-images", action="store_true", help="不下载商品主图")
    parser.add_argument("--category-workers", type=int, default=2, help="类目并发数 (默认: 2, 必须 > 0)")
    parser.add_argument("--page-workers", type=int, default=3, help="页面并发数 (默认: 3, 必须 > 0)")
    parser.add_argument("--image-workers", type=int, default=8, help="图片下载并发数 (默认: 8, 必须 > 0)")
    parser.add_argument("--max-in-flight", type=int, default=16, help="全局最大并发请求数 (默认: 16, 必须 > 0)")
    parser.add_argument("--output", default=None, help="输出目录 (默认: output/YYYYMMDD_HHMMSS)")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                      help="日志级别 (默认: INFO)")
    parser.add_argument("--version", action="version", version="eMAG Crawler V2.0.3")

    return parser.parse_args()


def validate_positive(value: int, name: str) -> None:
    """验证参数为正整数, 否则退出"""
    if value <= 0:
        print(f"[错误] {name} 必须是大于 0 的整数, 当前值: {value}", file=sys.stderr)
        sys.exit(EXIT_CONFIG_ERROR)


def print_startup_info(categories: list[dict], max_pages: int | None,
                       download_images: bool, args: argparse.Namespace, output_dir: str):
    """打印启动信息"""
    print("=" * 60)
    print("  eMAG 商品列表爬虫 V2.0.3 (纯 HTTP)")
    print("=" * 60)
    print(f"  已启用类目数量: {len(categories)}")
    for cat in categories:
        print(f"    - {cat['name']}: {cat['url']}")
    print(f"  抓取页数模式: {'最多20页/类目' if max_pages is None else f'最多 {max_pages} 页/类目'}")
    print(f"  下载商品主图: {'否' if not download_images else '是'}")
    print(f"  并发配置: 类目={args.category_workers}, 页面={args.page_workers}, 图片={args.image_workers}, 全局={args.max_in_flight}")
    print(f"  输出目录: {output_dir}")
    print(f"  日志级别: {args.log_level}")
    print("=" * 60)


def main() -> int:
    """主入口"""
    args = parse_args()

    # --- 参数验证 ---
    if args.pages is not None:
        validate_positive(args.pages, "--pages")
    validate_positive(args.category_workers, "--category-workers")
    validate_positive(args.page_workers, "--page-workers")
    validate_positive(args.image_workers, "--image-workers")
    validate_positive(args.max_in_flight, "--max-in-flight")

    # --- 加载 TXT 配置 ---
    from utils import load_txt_categories, make_output_dir, setup_logging

    try:
        categories = load_txt_categories(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"[错误] {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    if not categories:
        print("[错误] 配置文件中没有有效的类目URL", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    # --- 输出目录 ---
    max_pages = None if args.all_pages else args.pages
    output_dir = args.output or make_output_dir()
    os.makedirs(output_dir, exist_ok=True)

    # --- 日志 ---
    log_dir = os.path.join(output_dir, "logs")
    logger = setup_logging(log_dir, level=args.log_level)
    print_startup_info(categories, max_pages, not args.no_images, args, output_dir)

    # --- 创建爬虫 ---
    from crawler import EmagCrawler
    from image_downloader import ImageDownloader

    img_downloader = None
    if not args.no_images:
        img_downloader = ImageDownloader(
            output_dir=output_dir,
            max_workers=args.image_workers,
            max_in_flight=args.max_in_flight,
        )

    crawler = EmagCrawler(
        output_dir=output_dir,
        image_downloader=img_downloader,
        page_workers=args.page_workers,
        category_workers=args.category_workers,
        max_in_flight=args.max_in_flight,
        download_images=not args.no_images,
        log_level=args.log_level,
        all_pages=args.all_pages,
    )

    # --- 执行抓取 ---
    waf_detected = False
    keyboard_interrupt = False
    top_level_error = False

    try:
        crawler.crawl_all_categories(categories, max_pages=max_pages)
    except WafBlockError as e:
        waf_detected = True
        logger.error(f"WAF阻断: {e}")
    except KeyboardInterrupt:
        keyboard_interrupt = True
        logger.warning("用户中断抓取")
    except Exception as e:
        top_level_error = True
        logger.error(f"抓取过程异常: {e}", exc_info=True)

    # --- 完成导出 ---
    summary = crawler.finalize()
    if img_downloader:
        img_downloader.close()

    # --- 判断退出码 ---
    tot = summary.get("totals", {})
    is_waf_block = summary.get("status") in ("waf_blocked", "captcha_required") or waf_detected or crawler._waf_stop.is_set()

    if keyboard_interrupt:
        exit_code = EXIT_INTERRUPT
    elif is_waf_block:
        exit_code = EXIT_CAPTCHA
    elif top_level_error:
        exit_code = EXIT_NETWORK_ERROR
    elif tot.get("success_pages", 0) == 0 and tot.get("failed_pages", 0) > 0:
        exit_code = EXIT_NETWORK_ERROR
    elif tot.get("total_records", 0) == 0:
        exit_code = EXIT_NETWORK_ERROR
    else:
        exit_code = EXIT_SUCCESS

    # --- 打印最终结果 ---
    print("\n" + "=" * 60)
    print(f"  抓取完成! (退出码: {exit_code})")
    print("=" * 60)
    print(f"  总商品记录: {tot.get('total_records', 0)}")
    print(f"  唯一商品数: {tot.get('unique_products', 0)}")
    print(f"  成功页数: {tot.get('success_pages', 0)}")
    print(f"  失败页数: {tot.get('failed_pages', 0)}")
    print(f"  图片成功/失败: {tot.get('image_download_success', 0)}/{tot.get('image_download_failed', 0)}")
    print(f"  总耗时: {summary.get('elapsed_seconds', 0):.1f}s")

    print(f"\n  输出文件:")
    for fname in ["products.xlsx", "products.csv", "products.json", "run_summary.json", "errors.csv",
                   *([os.path.join("diagnostics", "captcha_diagnostic.json")] if is_waf_block else [])]:
        fpath = os.path.join(output_dir, fname)
        if os.path.exists(fpath):
            size_kb = os.path.getsize(fpath) / 1024
            print(f"    {fpath} ({size_kb:.1f} KB)")
    print(f"    {os.path.join(output_dir, 'logs/run.log')}")

    for cat in summary.get("categories", []):
        print(f"\n    [{cat['name']}]")
        print(f"      请求/成功/失败: {cat['requested_pages']}/{cat['success_pages']}/{cat['failed_pages']}")
        print(f"      商品记录/唯一: {cat['total_records']}/{cat['unique_products']}")
        print(f"      耗时: {cat.get('elapsed_seconds', 0):.1f}s")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
