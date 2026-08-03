#!/usr/bin/env python3
"""
eMAG 商品列表爬虫 V2.0 MVP
基于 Scrapling + StealthyFetcher 实现，支持多类目并发抓取

使用方法:
  python main.py --pages 1 --no-images
  python main.py --pages 3 --category-workers 2
  python main.py --all-pages
"""
import argparse
import logging
import os
import sys
from datetime import datetime


def parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="eMAG 商品列表爬虫 V2.0 — 基于 Scrapling",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --pages 1 --no-images          测试抓取1页，不下载图片
  %(prog)s --pages 2 --no-images          测试抓取2页，不下载图片
  %(prog)s --pages 3                       抓取3页，下载主图
  %(prog)s --all-pages                    抓取全部页面
  %(prog)s --pages 10 --category-workers 4 --page-workers 4 --image-workers 8 --max-in-flight 16
        """,
    )

    # 配置
    parser.add_argument(
        "--config",
        default=os.path.join(os.path.dirname(__file__), "config", "categories.json"),
        help="类目配置文件路径 (默认: config/categories.json)",
    )

    # 页数控制（互斥组）
    page_group = parser.add_mutually_exclusive_group()
    page_group.add_argument(
        "--pages", type=int, default=1,
        help="每个类目最大抓取页数 (默认: 1)",
    )
    page_group.add_argument(
        "--all-pages", action="store_true",
        help="抓取每个类目的全部页面 (与 --pages 互斥)",
    )

    # 图片
    parser.add_argument(
        "--no-images", action="store_true",
        help="不下载商品主图",
    )

    # 并发控制
    parser.add_argument(
        "--category-workers", type=int, default=2,
        help="类目并发数 (默认: 2)",
    )
    parser.add_argument(
        "--page-workers", type=int, default=4,
        help="页面并发数 (默认: 4)",
    )
    parser.add_argument(
        "--image-workers", type=int, default=8,
        help="图片下载并发数 (默认: 8)",
    )
    parser.add_argument(
        "--max-in-flight", type=int, default=16,
        help="全局最大并发请求数 (默认: 16)",
    )

    # 输出
    parser.add_argument(
        "--output", default=None,
        help="输出目录 (默认: output/YYYYMMDD_HHMMSS)",
    )

    # 日志
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别 (默认: INFO)",
    )

    # 其他
    parser.add_argument(
        "--reset", action="store_true",
        help="忽略已有缓存，重新抓取",
    )
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="重试上次失败的 URL",
    )
    parser.add_argument(
        "--version", action="version",
        version="eMAG Crawler V2.0.0",
    )

    return parser.parse_args()


def validate_config(config: dict) -> list[dict]:
    """验证并返回启用的类目配置"""
    categories = config.get("categories", [])
    if not categories:
        print("[错误] 配置文件中没有类目定义（categories 为空）", file=sys.stderr)
        sys.exit(1)

    enabled = []
    for i, cat in enumerate(categories):
        name = cat.get("name", f"类目{i+1}")
        url = cat.get("url", "")
        is_enabled = cat.get("enabled", True)

        if not url:
            print(f"[警告] 类目 [{name}] 缺少 URL，已跳过", file=sys.stderr)
            continue

        if not url.startswith("http"):
            print(f"[错误] 类目 [{name}] URL 格式无效: {url}", file=sys.stderr)
            sys.exit(1)

        if is_enabled:
            enabled.append(cat)

    if not enabled:
        print("[错误] 没有启用的类目。请检查配置文件中的 enabled 字段", file=sys.stderr)
        sys.exit(1)

    return enabled


def print_startup_info(
    categories: list[dict],
    max_pages: int | None,
    download_images: bool,
    args: argparse.Namespace,
    output_dir: str,
) -> None:
    """打印启动信息"""
    print("=" * 60)
    print("  eMAG 商品列表爬虫 V2.0 MVP")
    print("=" * 60)
    print(f"  已启用类目数量: {len(categories)}")
    for cat in categories:
        print(f"    - {cat['name']}: {cat['url']}")
    print(f"  本次抓取页数模式: {'全部页面' if max_pages is None else f'最多 {max_pages} 页/类目'}")
    print(f"  下载商品主图: {'否' if not download_images else '是'}")
    print(f"  并发配置: 类目={args.category_workers}, 页面={args.page_workers}, 图片={args.image_workers}, 全局={args.max_in_flight}")
    print(f"  输出目录: {output_dir}")
    print(f"  日志级别: {args.log_level}")
    print("=" * 60)


def main() -> int:
    """主入口"""
    args = parse_args()

    # 加载配置
    from utils import load_json_config, make_output_dir, setup_logging, get_version

    try:
        config = load_json_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 1

    # 验证配置
    enabled_categories = validate_config(config)

    # 确定页数
    max_pages = None if args.all_pages else args.pages

    # 创建输出目录
    output_dir = args.output or make_output_dir()
    os.makedirs(output_dir, exist_ok=True)

    # 设置日志
    log_dir = os.path.join(output_dir, "logs")
    logger = setup_logging(log_dir, level=args.log_level)

    # 打印启动信息
    print_startup_info(enabled_categories, max_pages, not args.no_images, args, output_dir)

    # 创建爬虫
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
    )

    # 执行抓取
    try:
        crawler.crawl_all_categories(enabled_categories, max_pages=max_pages)
    except KeyboardInterrupt:
        logger.warning("用户中断抓取")
    except Exception as e:
        logger.error(f"抓取过程异常: {e}", exc_info=True)

    # 完成导出
    summary = crawler.finalize()

    # 关闭图片下载器
    if img_downloader:
        img_downloader.close()

    # 打印最终结果
    print("\n" + "=" * 60)
    print("  抓取完成!")
    print("=" * 60)
    tot = summary["totals"]
    print(f"  总商品记录: {tot['total_records']}")
    print(f"  唯一商品数: {tot['unique_products']}")
    print(f"  成功页数: {tot['success_pages']}")
    print(f"  失败页数: {tot['failed_pages']}")
    print(f"  图片成功/失败: {tot['image_download_success']}/{tot['image_download_failed']}")
    print(f"  总耗时: {summary['elapsed_seconds']:.1f}s")

    print(f"\n  输出文件:")
    for fname in ["products.xlsx", "products.csv", "products.jsonl", "run_summary.json", "errors.csv"]:
        fpath = os.path.join(output_dir, fname)
        if os.path.exists(fpath):
            size_kb = os.path.getsize(fpath) / 1024
            print(f"    {fpath} ({size_kb:.1f} KB)")
    print(f"    {os.path.join(output_dir, 'logs/run.log')}")

    # 按类目显示统计
    print(f"\n  各类目统计:")
    for cat in summary["categories"]:
        print(f"    [{cat['name']}]")
        print(f"      请求/成功/失败: {cat['requested_pages']}/{cat['success_pages']}/{cat['failed_pages']}")
        print(f"      商品记录/唯一: {cat['total_records']}/{cat['unique_products']}")
        print(f"      图片成功/失败: {cat['image_success']}/{cat['image_failed']}")
        print(f"      耗时: {cat['elapsed_seconds']:.1f}s")

    # 检查是否有致命错误
    if tot["success_pages"] == 0 and tot["failed_pages"] > 0:
        print(f"\n  [警告] 所有页面均抓取失败，请检查网络连接和 eMAG WAF 状态")
        return 2

    if tot["total_records"] == 0:
        print(f"\n  [警告] 未抓到任何商品，请检查页面结构是否发生变化")
        return 0  # 不算致命错误

    return 0


if __name__ == "__main__":
    sys.exit(main())
