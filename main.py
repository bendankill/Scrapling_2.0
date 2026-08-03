#!/usr/bin/env python3
"""eMAG 商品列表爬虫 V2.1.1-fix"""
import argparse, logging, os, signal, sys, threading
from utils import (EXIT_SUCCESS, EXIT_CONFIG_ERROR, EXIT_NETWORK_ERROR,
    EXIT_CAPTCHA, EXIT_INTERRUPT, WafBlockError)
from checkpoint import RunStatus

def parse_args():
    p = argparse.ArgumentParser(description="eMAG 爬虫 V2.1.1")
    p.add_argument("--config", default=os.path.join(os.path.dirname(__file__), "config", "categories.txt"))
    p.add_argument("--resume", default=None, help="从 checkpoint.json 恢复")
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
    p.add_argument("--version", action="version", version="eMAG Crawler V2.1.1")
    return p.parse_args()

def validate_positive(v, n):
    if v <= 0: print(f"[错误] {n} 必须>0: {v}", file=sys.stderr); sys.exit(EXIT_CONFIG_ERROR)

def print_info(cats, max_pages, dl, args, out, resume=False):
    print(f"{'='*60}\n  eMAG V2.1.1 (纯HTTP){' [恢复]' if resume else ''}\n{'='*60}")
    for c in cats: print(f"    {c['name']}: {c['url']}")
    print(f"  页数: {'最多20' if max_pages is None else str(max_pages)}")
    print(f"  图片: {'否' if not dl else '是'}")
    print(f"  并发: cat={args.category_workers} page={args.page_workers} img={args.image_workers} max={args.max_in_flight}")
    print(f"  输出: {out}\n{'='*60}")

def main():
    args = parse_args()
    if args.resume: return _do_resume(args)

    for n, v in [("--pages", args.pages), ("--category-workers", args.category_workers),
                 ("--page-workers", args.page_workers), ("--image-workers", args.image_workers),
                 ("--max-in-flight", args.max_in_flight)]:
        if v is not None: validate_positive(v, n)

    from utils import load_txt_categories, make_output_dir, setup_logging
    try: categories = load_txt_categories(args.config)
    except (FileNotFoundError, ValueError) as e: print(f"[错误] {e}", file=sys.stderr); return EXIT_CONFIG_ERROR

    max_pages = None if args.all_pages else args.pages
    out = args.output or make_output_dir(); os.makedirs(out, exist_ok=True)
    logger = setup_logging(os.path.join(out, "logs"), level=args.log_level)
    print_info(categories, max_pages, not args.no_images, args, out)

    from crawler import EmagCrawler; from image_downloader import ImageDownloader
    from checkpoint import CheckpointManager
    stop_ev = threading.Event()
    img_dl = ImageDownloader(out, max_workers=args.image_workers, max_in_flight=args.max_in_flight) if not args.no_images else None
    cp = CheckpointManager(out, config_file=args.config, arguments={
        "pages": args.pages, "all_pages": args.all_pages, "download_images": not args.no_images,
        "category_workers": args.category_workers, "page_workers": args.page_workers,
        "image_workers": args.image_workers})
    crawler = EmagCrawler(out, image_downloader=img_dl, page_workers=args.page_workers,
        category_workers=args.category_workers, max_in_flight=args.max_in_flight,
        download_images=not args.no_images, all_pages=args.all_pages, checkpoint=cp, stop_event=stop_ev)

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
    print(f"\n{'='*60}\n  任务结束 (退出码: {ec})\n{'='*60}")
    print(f"  商品记录/唯一: {tot.get('total_records',0)}/{tot.get('unique_products',0)}")
    print(f"  成功/失败页: {tot.get('success_pages',0)}/{tot.get('failed_pages',0)}")
    print(f"  状态: {summary.get('status','')}")

    cp_path = os.path.join(out, "checkpoint.json")
    if os.path.exists(cp_path): print(f"  checkpoint: {cp_path}")
    if ec in (2, 3, 130): crawler._print_resume_help()
    return ec

def _do_resume(args):
    from checkpoint import CheckpointManager; from utils import setup_logging, load_txt_categories
    cp_path = os.path.abspath(args.resume)
    if not os.path.exists(cp_path): print(f"[错误] 文件不存在: {cp_path}", file=sys.stderr); return EXIT_CONFIG_ERROR
    try: cp = CheckpointManager.load(cp_path)
    except (ValueError, FileNotFoundError) as e: print(f"[错误] {e}", file=sys.stderr); return EXIT_CONFIG_ERROR

    if cp.is_completed(): print("[提示] 任务已完成"); return 0
    if not cp.validate_config(): print("[错误] 配置文件已变更"); return EXIT_CONFIG_ERROR

    try: all_cats = load_txt_categories(cp.data.get("config_file", args.config))
    except (FileNotFoundError, ValueError) as e: print(f"[错误] {e}", file=sys.stderr); return EXIT_CONFIG_ERROR

    completed_urls = {c["url"] for c in cp.data.get("categories", []) if c.get("status") == "completed"}
    cats = [c for c in all_cats if c["url"] not in completed_urls]
    out = cp.data.get("output_dir", os.path.dirname(cp_path))
    logger = setup_logging(os.path.join(out, "logs"))
    ad = cp.data.get("arguments", {})
    print_info(cats, None if ad.get("all_pages") else ad.get("pages", 1), ad.get("download_images", True),
               argparse.Namespace(category_workers=ad.get("category_workers",2),
                   page_workers=ad.get("page_workers",3), image_workers=ad.get("image_workers",8),
                   max_in_flight=ad.get("max_in_flight",16)), out, resume=True)

    for c in cp.data.get("categories", []):
        for pg in c.get("completed_pages", []):
            snap = cp.load_page_snapshot(c["url"], pg)
            if snap:
                for item in snap:
                    k = (f"pnk:{item['pnk']}" if item.get("pnk") else
                         f"pid:{item['product_id']}" if item.get("product_id") else "")
                    if k: cp.add_product_keys([k])

    from crawler import EmagCrawler; from image_downloader import ImageDownloader
    stop_ev = threading.Event(); img_dl = None
    if ad.get("download_images", True):
        img_dl = ImageDownloader(out, max_in_flight=ad.get("max_in_flight", 16))
    crawler = EmagCrawler(out, image_downloader=img_dl, page_workers=ad.get("page_workers",3),
        category_workers=ad.get("category_workers",2), max_in_flight=ad.get("max_in_flight",16),
        download_images=ad.get("download_images",True), all_pages=ad.get("all_pages",False),
        checkpoint=cp, stop_event=stop_ev)

    def _on_sigint(s, f): stop_ev.set(); crawler._interrupted = True; print("\n[Ctrl+C] 停止...", file=sys.stderr)
    prev = signal.signal(signal.SIGINT, _on_sigint)
    kb_int = False
    try: crawler.crawl_all_categories(cats, max_pages=None if ad.get("all_pages") else ad.get("pages",1))
    except KeyboardInterrupt: kb_int = True; stop_ev.set()
    except Exception as e: logger.error(f"异常: {e}", exc_info=True)
    signal.signal(signal.SIGINT, prev)
    crawler.finalize(interrupted=kb_int)
    if img_dl: img_dl.close()
    ec = crawler.get_exit_code()
    print(f"  恢复任务结束 (退出码: {ec})")
    return ec

if __name__ == "__main__": sys.exit(main())
