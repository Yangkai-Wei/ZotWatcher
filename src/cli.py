"""命令行接口"""
import argparse
import sys
from pathlib import Path

from .profile import ProfileBuilder
from .watcher import LiteratureWatcher
from .utils import setup_logging, load_config


def profile_command(args):
    """构建用户画像"""
    logger = setup_logging()
    logger.info("开始构建用户画像...")
    
    config = load_config()
    builder = ProfileBuilder(config)
    
    if args.full:
        logger.info("执行全量画像构建")
        builder.build_full_profile()
    else:
        logger.info("执行增量画像更新")
        builder.update_profile()
    
    logger.info("用户画像构建完成")


def watch_command(args):
    """监测新文献并生成推荐"""
    logger = setup_logging()
    logger.info("开始监测学术信息源...")
    
    config = load_config()
    watcher = LiteratureWatcher(config)
    
    # 抓取候选文章
    candidates = watcher.fetch_candidates()
    logger.info(f"获取到 {len(candidates)} 篇候选文章")
    
    # 评分排序
    top_n = args.top or 100
    recommendations = watcher.score_and_rank(candidates, top_n=top_n)
    logger.info(f"生成 {len(recommendations)} 篇推荐文章")
    
    # 生成输出
    if args.rss:
        rss_path = Path("reports/feed.xml")
        watcher.generate_rss(recommendations, rss_path)
        logger.info(f"RSS feed 已生成: {rss_path}")
    
    if args.report:
        html_path = Path("reports/index.html")
        watcher.generate_html_report(recommendations, html_path)
        logger.info(f"HTML 报告已生成: {html_path}")
    
    if args.push_to_zotero:
        watcher.push_to_zotero(recommendations)
        logger.info("推荐文章已推送到 Zotero")
    
    logger.info("监测任务完成")


def main():
    """主入口"""
    parser = argparse.ArgumentParser(
        description="ZotWatcher - 基于 Zotero 的学术文献推荐系统"
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")
    
    # profile 子命令
    profile_parser = subparsers.add_parser("profile", help="构建或更新用户画像")
    profile_parser.add_argument(
        "--full",
        action="store_true",
        help="执行全量画像构建（而非增量更新）"
    )
    
    # watch 子命令
    watch_parser = subparsers.add_parser("watch", help="监测新文献并生成推荐")
    watch_parser.add_argument(
        "--top",
        type=int,
        default=100,
        help="推荐文章数量（默认 100）"
    )
    watch_parser.add_argument(
        "--rss",
        action="store_true",
        help="生成 RSS feed"
    )
    watch_parser.add_argument(
        "--report",
        action="store_true",
        help="生成 HTML 报告"
    )
    watch_parser.add_argument(
        "--push-to-zotero",
        action="store_true",
        help="将推荐文章推送到 Zotero"
    )
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    if args.command == "profile":
        profile_command(args)
    elif args.command == "watch":
        watch_command(args)


if __name__ == "__main__":
    main()
