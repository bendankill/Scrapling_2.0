#!/usr/bin/env python3
"""eMAG 商品列表爬虫 V2.1.3 — 纯 HTTP + TXT配置"""
import argparse, logging, os, signal, sys, threading, time
from utils import (EXIT_SUCCESS, EXIT_CONFIG_ERROR, EXIT_NETWORK_ERROR,
    EXIT_CAPTCHA, EXIT_INTERRUPT, WafBlockError, RunStatus)
from config import (DEFAULT_PAGE_WORKERS, DEFAULT_CATEGORY_WORKERS,
    DEFAULT_MAX_IN_FLIGHT, DEFAULT_IMAGE_WORKERS, DEFAULT_IMAGE_MAX_IN_FLIGHT,
    DEFAULT_IMAGES_PER_PRODUCT, load_config)

def parse_args():
    p = argparse.ArgumentParser(description="eMAG 爬虫 V2.1.3")
    p.add_argument("--config", default=os.path.join(os.path.dirname(__file__), "config", "categories.txt"))
    pg = p.add_mutually_exclusive_group()
    pg.add_argument("--pages", type=int, default=1, help="最大抓取页数")
    pg.add_argument("--all-pages", action="store_true", help="最多20页/类目")
    p.add_argument("--no-images", action="store_true")
    # 以下参数默认None表示"未显式传入", 最终值从TXT配置读取
    p.add_argument("--category-workers", type=int, default=None)
    p.add_argument("--page-workers", type=int, default=None)
    p.add_argument("--max-in-flight", type=int, default=None)
    p.add_argument("--image-workers", type=int, default=None)
    p.add_argument("--image-max-in-flight", type=int, default=None)
    p.add_argument("--images-per-product", type=int, default=None, choices=[0,1])
    p.add_argument("--output", default=None)
    p.add_argument("--log-level", default="INFO", choices=["DEBUG","INFO","WARNING","ERROR"])
    p.add_argument("--version", action="version", version="eMAG Crawler V2.1.3")
    return p.parse_args()

def validate_positive(v, n):
    if v <= 0: print(f"[错误] {n} 必须>0: {v}", file=sys.stderr); sys.exit(EXIT_CONFIG_ERROR)

def _fmt_duration(seconds: float) -> str:
    h = int(seconds // 3600); m = int((seconds % 3600) // 60); s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}（{seconds:.3f}秒）"

def _merge_config(cli_args, txt_config: dict, default_config: dict) -> dict:
    """合并配置: CLI > TXT > 兜底"""
    keys = ["page_workers","category_workers","max_in_flight","image_workers","image_max_in_flight","images_per_product"]
    result = {}
    for k in keys:
        cli_val = getattr(cli_args, k.replace("_", "_"), None)
        # argparse对int参数用None表示未传
        if k == "images_per_product":
            cli_val = cli_args.images_per_product
        elif k == "image_max_in_flight":
            cli_val = cli_args.image_max_in_flight
        elif k == "image_workers":
            cli_val = cli_args.image_workers
        elif k == "page_workers":
            cli_val = cli_args.page_workers
        elif k == "category_workers":
            cli_val = cli_args.category_workers
        elif k == "max_in_flight":
            cli_val = cli_args.max_in_flight
        src = "default"  # 兜底
        if cli_val is not None:
            result[k] = cli_val; src = "CLI"
        elif k in txt_config:
            result[k] = txt_config[k]; src = "categories.txt"
        else:
            result[k] = default_config[k]
        result[f"{k}_source"] = src
    return result

def print_config(final: dict):
    print("  实际运行配置：")
    for k in ["page_workers","category_workers","max_in_flight","image_workers","image_max_in_flight","images_per_product"]:
        src = final.get(f"{k}_source","?")
        mark = "（命令行覆盖）" if src == "CLI" else ("（categories.txt）" if src == "categories.txt" else "（兜底值）")
        print(f"    {k}={final[k]} {mark}")

def main():
    t_start = time.perf_counter()
    args = parse_args()

    # 加载TXT配置
    try: txt_cfg, category_urls = load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"[错误] {e}", file=sys.stderr)
        print(f"  本次任务总耗时：{_fmt_duration(time.perf_counter() - t_start)}")
        return EXIT_CONFIG_ERROR

    # 合并配置
    defaults = {"page_workers": DEFAULT_PAGE_WORKERS, "category_workers": DEFAULT_CATEGORY_WORKERS,
                "max_in_flight": DEFAULT_MAX_IN_FLIGHT, "image_workers": DEFAULT_IMAGE_WORKERS,
                "image_max_in_flight": DEFAULT_IMAGE_MAX_IN_FLIGHT,
                "images_per_product": DEFAULT_IMAGES_PER_PRODUCT}
    final = _merge_config(args, txt_cfg, defaults)

    # 验证
    for k in ["page_workers","category_workers","max_in_flight","image_workers","image_max_in_flight"]:
        validate_positive(final[k], k)

    # 检查页面参数
    if args.pages is not None: validate_positive(args.pages, "--pages")

    # 构建类目列表
    from utils import make_output_dir, setup_logging
    categories = []
    for u in category_urls:
        name = _cat_name(u, len(categories)+1)
        categories.append({"name": name, "url": u, "enabled": True})

    max_pages = None if args.all_pages else args.pages
    out = args.output or make_output_dir(); os.makedirs(out, exist_ok=True)
    logger = setup_logging(os.path.join(out, "logs"), level=args.log_level)

    # 启动信息
    dl_images = (not args.no_images) and (final["images_per_product"] >= 1)
    print(f"{'='*60}\n  eMAG V2.1.3 (纯HTTP)\n{'='*60}")
    for c in categories: print(f"    {c['name']}: {c['url']}")
    print(f"  页数: {'最多20' if max_pages is None else str(max_pages)}")
    print(f"  图片: {'否' if not dl_images else '是'}")
    print_config(final)
    print(f"  输出: {out}\n{'='*60}")

    from crawler import EmagCrawler; from image_downloader import ImageDownloader
    stop_ev = threading.Event()
    img_dl = None
    if dl_images:
        img_dl = ImageDownloader(out, max_workers=final["image_workers"],
                                 max_in_flight=final["image_max_in_flight"])
    crawler = EmagCrawler(out, image_downloader=img_dl,
        page_workers=final["page_workers"], category_workers=final["category_workers"],
        max_in_flight=final["max_in_flight"], download_images=dl_images,
        all_pages=args.all_pages, stop_event=stop_ev)

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

def _cat_name(u, idx):
    p = urlparse(u).path.rstrip("/").split("/")
    for i, part in enumerate(p):
        if part == "c" and i > 0: return p[i-1].replace("-"," ").title()
    return f"Category_{idx:03d}"
from urllib.parse import urlparse

if __name__ == "__main__": sys.exit(main())
