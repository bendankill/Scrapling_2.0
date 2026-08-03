#!/usr/bin/env python3
"""
eMAG 商品列表爬虫 V2.1.1
支持断点续抓 (--resume), Ctrl+C 安全中断, PageResult 准确统计
"""
import argparse
import logging
import os
import signal
import sys
import threading

from utils import (
    EXIT_SUCCESS, EXIT_CONFIG_ERROR, EXIT_NETWORK_ERROR,
    EXIT_CAPTCHA, EXIT_INTERRUPT, WafBlockError,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="eMAG 商品列表爬虫 V2.1.1 — 纯 HTTP + 断点续抓",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --pages 1 --no-images
  %(prog)s --pages 3 --no-images
  %(prog)s --all-pages --no-images
  %(prog)s --pages 10 --category-workers 2 --image-workers 8 --max-in-flight 16
  %(prog)s --resume "output/20260803_120000/checkpoint.json"
        """,
    )
    parser.add_argument("--config",
        default=os.path.join(os.path.dirname(__file__), "config", "categories.txt"),
        help="类目配置文件 (默认: config/categories.txt)")
    parser.add_argument("--resume", default=None,
        help="从指定 checkpoint.json 恢复任务")

    page_group = parser.add_mutually_exclusive_group()
    page_group.add_argument("--pages", type=int, default=1,
        help="每个类目最大抓取页数 (默认: 1, 必须 > 0)")
    page_group.add_argument("--all-pages", action="store_true",
        help="每个类目最多抓取 20 页 (与 --pages 互斥)")

    parser.add_argument("--no-images", action="store_true", help="不下载商品主图")
    parser.add_argument("--category-workers", type=int, default=2)
    parser.add_argument("--page-workers", type=int, default=3)
    parser.add_argument("--image-workers", type=int, default=8)
    parser.add_argument("--max-in-flight", type=int, default=16)
    parser.add_argument("--output", default=None, help="输出目录")
    parser.add_argument("--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--version", action="version", version="eMAG Crawler V2.1.1")
    return parser.parse_args()


def validate_positive(value: int, name: str) -> None:
    if value <= 0:
        print(f"[错误] {name} 必须是大于 0 的整数, 当前值: {value}", file=sys.stderr)
        sys.exit(EXIT_CONFIG_ERROR)


def print_startup_info(categories, max_pages, download_images, args, output_dir, is_resume=False):
    print("=" * 60)
    print(f"  eMAG 商品列表爬虫 V2.1.1 (纯 HTTP){' [恢复模式]' if is_resume else ''}")
    print("=" * 60)
    print(f"  已启用类目数量: {len(categories)}")
    for cat in categories:
        print(f"    - {cat['name']}: {cat['url']}")
    print(f"  抓取页数模式: {'最多20页/类目' if max_pages is None else f'最多 {max_pages} 页/类目'}")
    print(f"  下载商品主图: {'否' if not download_images else '是'}")
    print(f"  并发: 类目={args.category_workers}, 页面={args.page_workers}, 图片={args.image_workers}, 全局={args.max_in_flight}")
    print(f"  输出目录: {output_dir}")
    print("=" * 60)


def main() -> int:
    args = parse_args()

    # --- 恢复模式 ---
    if args.resume:
        return _do_resume(args)

    # --- 参数验证 ---
    if args.pages is not None:
        validate_positive(args.pages, "--pages")
    for name, val in [("--category-workers", args.category_workers),
                      ("--page-workers", args.page_workers),
                      ("--image-workers", args.image_workers),
                      ("--max-in-flight", args.max_in_flight)]:
        validate_positive(val, name)

    # --- 加载配置 ---
    from utils import load_txt_categories, make_output_dir, setup_logging
    try:
        categories = load_txt_categories(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"[错误] {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    # --- 输出目录 ---
    max_pages = None if args.all_pages else args.pages
    output_dir = args.output or make_output_dir()
    os.makedirs(output_dir, exist_ok=True)

    # --- 日志 ---
    logger = setup_logging(os.path.join(output_dir, "logs"), level=args.log_level)
    print_startup_info(categories, max_pages, not args.no_images, args, output_dir)

    # --- 创建爬虫 ---
    from crawler import EmagCrawler
    from image_downloader import ImageDownloader
    from checkpoint import CheckpointManager

    stop_event = threading.Event()
    img_dl = None
    if not args.no_images:
        img_dl = ImageDownloader(output_dir=output_dir, max_workers=args.image_workers,
                                 max_in_flight=args.max_in_flight)

    cp = CheckpointManager(output_dir, config_file=args.config, arguments={
        "pages": args.pages, "all_pages": args.all_pages,
        "download_images": not args.no_images,
        "category_workers": args.category_workers,
        "page_workers": args.page_workers,
        "image_workers": args.image_workers,
    })

    crawler = EmagCrawler(output_dir=output_dir, image_downloader=img_dl,
                          page_workers=args.page_workers,
                          category_workers=args.category_workers,
                          max_in_flight=args.max_in_flight,
                          download_images=not args.no_images,
                          all_pages=args.all_pages,
                          checkpoint=cp, stop_event=stop_event)

    # --- Signal handler ---
    def _on_sigint(signum, frame):
        stop_event.set()
        crawler._interrupted = True
        print("\n[Ctrl+C] 正在安全停止...", file=sys.stderr)
    prev = signal.signal(signal.SIGINT, _on_sigint)

    # --- 执行 ---
    keyboard_interrupt = False
    waf_detected = False
    top_error = False
    try:
        crawler.crawl_all_categories(categories, max_pages=max_pages)
    except KeyboardInterrupt:
        keyboard_interrupt = True
        stop_event.set()
        logger.warning("用户中断 (KeyboardInterrupt)")
    except WafBlockError as e:
        waf_detected = True
    except Exception as e:
        top_error = True
        logger.error(f"异常: {e}", exc_info=True)

    signal.signal(signal.SIGINT, prev)

    # --- 完成 ---
    summary = crawler.finalize(
        interrupted=keyboard_interrupt,
        paused_reason="network_error" if top_error else "")
    if img_dl:
        img_dl.close()

    # --- 退出码 ---
    tot = summary.get("totals", {})
    status = summary.get("status", "")
    is_waf = "waf" in status or waf_detected

    if keyboard_interrupt:
        exit_code = EXIT_INTERRUPT
    elif is_waf:
        exit_code = EXIT_CAPTCHA
        crawler._write_resume_files()
    elif top_error:
        exit_code = EXIT_NETWORK_ERROR
        crawler._write_resume_files()
    elif tot.get("success_pages", 0) == 0 and tot.get("failed_pages", 0) > 0:
        exit_code = EXIT_NETWORK_ERROR
        crawler._write_resume_files()
    elif tot.get("total_records", 0) == 0:
        exit_code = EXIT_NETWORK_ERROR
    else:
        exit_code = EXIT_SUCCESS

    # --- 打印结果 ---
    print(f"\n{'='*60}\n  任务结束 (退出码: {exit_code})\n{'='*60}")
    print(f"  总商品记录: {tot.get('total_records', 0)}")
    print(f"  唯一商品数: {tot.get('unique_products', 0)}")
    print(f"  成功/失败页: {tot.get('success_pages', 0)}/{tot.get('failed_pages', 0)}")
    print(f"  图片成功/失败: {tot.get('image_download_success', 0)}/{tot.get('image_download_failed', 0)}")
    print(f"  总耗时: {summary.get('elapsed_seconds', 0):.1f}s")
    cp_path = os.path.join(output_dir, "checkpoint.json")
    if os.path.exists(cp_path):
        print(f"  checkpoint: {cp_path}")
    if exit_code in (2, 3, 130):
        crawler._print_resume_help()
    return exit_code


def _do_resume(args) -> int:
    """从 checkpoint 恢复任务"""
    from checkpoint import CheckpointManager
    from utils import setup_logging, load_txt_categories

    cp_path = os.path.abspath(args.resume)
    if not os.path.exists(cp_path):
        print(f"[错误] checkpoint 文件不存在: {cp_path}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    try:
        cp = CheckpointManager.load(cp_path)
    except (ValueError, FileNotFoundError) as e:
        print(f"[错误] checkpoint 无效: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    if cp.is_completed():
        print("[提示] 任务已经完成，不需要继续。")
        print(f"  输出目录: {cp.data.get('output_dir', '')}")
        return EXIT_SUCCESS

    # 校验配置
    config_file = cp.data.get("config_file", args.config)
    if not cp.validate_config():
        print("[错误] 配置文件已变更，与 checkpoint 不匹配。")
        print(f"  请使用原配置文件: {config_file}")
        return EXIT_CONFIG_ERROR

    # 加载类目
    try:
        all_cats = load_txt_categories(config_file)
    except (FileNotFoundError, ValueError) as e:
        print(f"[错误] {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    # 过滤已完成类目
    completed_urls = set()
    for c in cp.data.get("categories", []):
        if c.get("status") == "completed":
            completed_urls.add(c["url"])
    categories = [c for c in all_cats if c["url"] not in completed_urls]

    output_dir = cp.data.get("output_dir", os.path.dirname(cp_path))
    logger = setup_logging(os.path.join(output_dir, "logs"))

    args_dict = cp.data.get("arguments", {})
    max_pages = None if args_dict.get("all_pages") else args_dict.get("pages", 1)
    dl_images = args_dict.get("download_images", True)
    print_startup_info(categories, max_pages, dl_images,
                       argparse.Namespace(
                           category_workers=args_dict.get("category_workers", 2),
                           page_workers=args_dict.get("page_workers", 3),
                           image_workers=args_dict.get("image_workers", 8),
                           max_in_flight=args_dict.get("max_in_flight", 16),
                       ), output_dir, is_resume=True)

    # 加载已完成商品到去重集合
    for c in cp.data.get("categories", []):
        for pg in c.get("completed_pages", []):
            snap = cp.load_page_snapshot(c["url"], pg)
            if snap:
                for item in snap:
                    key = item.get("pnk") and f"pnk:{item['pnk']}" or \
                          item.get("product_id") and f"pid:{item['product_id']}" or ""
                    if key:
                        cp.add_product_keys([key])

    # 恢复抓取
    from crawler import EmagCrawler
    from image_downloader import ImageDownloader
    stop_event = threading.Event()
    img_dl = None
    if dl_images:
        img_dl = ImageDownloader(output_dir=output_dir, max_in_flight=args_dict.get("max_in_flight", 16))

    crawler = EmagCrawler(output_dir=output_dir, image_downloader=img_dl,
                          page_workers=args_dict.get("page_workers", 3),
                          category_workers=args_dict.get("category_workers", 2),
                          max_in_flight=args_dict.get("max_in_flight", 16),
                          download_images=dl_images,
                          all_pages=args_dict.get("all_pages", False),
                          checkpoint=cp, stop_event=stop_event)

    def _on_sigint(signum, frame):
        stop_event.set()
        crawler._interrupted = True
        print("\n[Ctrl+C] 正在安全停止...", file=sys.stderr)
    prev = signal.signal(signal.SIGINT, _on_sigint)

    kb_int = False
    try:
        crawler.crawl_all_categories(categories, max_pages=max_pages)
    except KeyboardInterrupt:
        kb_int = True
        stop_event.set()
    except Exception as e:
        logger.error(f"异常: {e}", exc_info=True)

    signal.signal(signal.SIGINT, prev)
    summary = crawler.finalize(interrupted=kb_int)
    if img_dl:
        img_dl.close()

    tot = summary.get("totals", {})
    print(f"\n{'='*60}\n  恢复任务结束 (退出码: {0 if not kb_int else 130})\n{'='*60}")
    print(f"  总商品记录: {tot.get('total_records', 0)}")
    return EXIT_INTERRUPT if kb_int else EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
