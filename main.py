#!/usr/bin/env python3
"""eMAG 商品列表爬虫 V2.1.2 — 纯 HTTP"""
import argparse, logging, os, signal, sys, threading, time
from utils import (EXIT_SUCCESS, EXIT_CONFIG_ERROR, EXIT_NETWORK_ERROR,
    EXIT_CAPTCHA, EXIT_INTERRUPT, WafBlockError, RunStatus)

def parse_args():
    p = argparse.ArgumentParser(description="eMAG 爬虫 V2.1.2")
    p.add_argument("--config", default=os.path.join(os.path.dirname(__file__), "config", "categories.txt"))
    pg = p.add_mutually_exclusive_group()
    pg.add_argument("--pages", type=int, default=1, help="最大抓取页数")
    pg.add_argument("--all-pages", action="store_true", help="最多20页/类目")
    p.add_argument("--no-images", action="store_true")
    p.add_argument("--category-workers", type=int, default=2)
    p.add_argument("--page-workers", type=int, default=3)
    p.add_argument("--image-workers", type=int, default=8)
    p.add_argument("--max-in-flight", type=int, default=16)
    p.add_argument("--output", default=None)
    p.add_argument("--log-level", default="INFO", choices=["DEBUG","INFO","WARNING","ERROR"])
    p.add_argument("--version", action="version", version="eMAG Crawler V2.1.2")
    return p.parse_args()

def validate_positive(v, n):
    if v <= 0: print(f"[错误] {n} 必须>0: {v}", file=sys.stderr); sys.exit(EXIT_CONFIG_ERROR)

def _fmt_duration(seconds: float) -> str:
    h = int(seconds // 3600); m = int((seconds % 3600) // 60); s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}（{seconds:.3f}秒）"

def print_info(cats, max_pages, dl, args, out):
    print(f"{'='*60}\n  eMAG V2.1.2 (纯HTTP)\n{'='*60}")
    for c in cats: print(f"    {c['name']}: {c['url']}")
    print(f"  页数: {'最多20' if max_pages is None else str(max_pages)}")
    print(f"  图片: {'否' if not dl else '是'}")
    print(f"  并发: cat={args.category_workers} page={args.page_workers} img={args.image_workers} max={args.max_in_flight}")
    print(f"  输出: {out}\n{'='*60}")

def main():
    t_start = time.perf_counter()
    args = parse_args()

    for n, v in [("--pages", args.pages), ("--category-workers", args.category_workers),
                 ("--page-workers", args.page_workers), ("--image-workers", args.image_workers),
                 ("--max-in-flight", args.max_in_flight)]:
        if v is not None: validate_positive(v, n)

    from utils import load_txt_categories, make_output_dir, setup_logging
    try: categories = load_txt_categories(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"[错误] {e}", file=sys.stderr)
        elapsed = time.perf_counter() - t_start
        print(f"  本次任务总耗时：{_fmt_duration(elapsed)}")
        return EXIT_CONFIG_ERROR

    max_pages = None if args.all_pages else args.pages
    out = args.output or make_output_dir(); os.makedirs(out, exist_ok=True)
    logger = setup_logging(os.path.join(out, "logs"), level=args.log_level)
    print_info(categories, max_pages, not args.no_images, args, out)

    from crawler import EmagCrawler; from image_downloader import ImageDownloader
    stop_ev = threading.Event()
    img_dl = ImageDownloader(out, max_workers=args.image_workers, max_in_flight=args.max_in_flight) if not args.no_images else None
    crawler = EmagCrawler(out, image_downloader=img_dl, page_workers=args.page_workers,
        category_workers=args.category_workers, max_in_flight=args.max_in_flight,
        download_images=not args.no_images, all_pages=args.all_pages, stop_event=stop_ev)

    def _on_sigint(s, f): stop_ev.set(); crawler._interrupted = True; print("\n[Ctrl+C] 安全停止...", file=sys.stderr)
    prev = signal.signal(signal.SIGINT, _on_sigint)
    kb_int = False
    try: crawler.crawl_all_categories(categories, max_pages=max_pages)
    except KeyboardInterrupt: kb_int = True; stop_ev.set()
    except Exception as e: logger.error(f"异常: {e}", exc_info=True)
    signal.signal(signal.SIGINT, prev)

    summary = crawler.finalize(interrupted=kb_int)
    if img_dl: img_dl.close()
    ec = crawler.get_exit_code()
    tot = summary.get("totals", {})

    elapsed = time.perf_counter() - t_start
    print(f"\n{'='*60}\n  任务结束 (退出码: {ec})\n{'='*60}")
    print(f"  商品记录/唯一: {tot.get('total_records',0)}/{tot.get('unique_products',0)}")
    print(f"  成功/失败页: {tot.get('success_pages',0)}/{tot.get('failed_pages',0)}")
    print(f"  状态: {summary.get('status','')}")
    print(f"  本次任务总耗时：{_fmt_duration(elapsed)}")
    return ec

if __name__ == "__main__": sys.exit(main())
