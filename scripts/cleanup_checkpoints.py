"""Checkpoint 清理：SQLite 兜底路径（未配 Redis 时）会无限膨胀，按大小/天数清理。

用法:
    python scripts/cleanup_checkpoints.py              # 预览（只打印，不删）
    python scripts/cleanup_checkpoints.py --force      # 执行删除
    python scripts/cleanup_checkpoints.py --max-mb 100 # 自定义阈值（默认 200MB）
"""

import argparse
import time
from pathlib import Path

CHECKPOINT_DB = Path(__file__).resolve().parent.parent / "db" / "agent_state.db"


def main() -> None:
    parser = argparse.ArgumentParser(description="清理 SQLite checkpoint 兜底文件")
    parser.add_argument("--force", action="store_true", help="实际删除（默认只预览）")
    parser.add_argument("--max-mb", type=int, default=200, help="超过该大小（MB）触发清理")
    parser.add_argument("--max-days", type=int, default=7, help="超过该天数（天）触发清理")
    args = parser.parse_args()

    if not CHECKPOINT_DB.exists():
        print("没有 checkpoint 文件，无需清理")
        return

    size_mb = CHECKPOINT_DB.stat().st_size / 1e6
    age_days = (time.time() - CHECKPOINT_DB.stat().st_mtime) / 86400
    too_big = size_mb > args.max_mb
    too_old = age_days > args.max_days

    if not (too_big or too_old):
        print(f"无需清理: {size_mb:.0f}MB / {age_days:.0f}天（阈值 {args.max_mb}MB / {args.max_days}天）")
        return

    action = "删除" if args.force else "将删除（加 --force 执行）"
    print(f"{action}: db/agent_state.db（{size_mb:.0f}MB / {age_days:.0f}天）")
    print("提示: 删掉后旧会话从头开始；配置了 REDIS_URL 时 checkpoint 走 Redis，不受影响。")
    if args.force:
        CHECKPOINT_DB.unlink(missing_ok=True)
        print("已删除 ✓")


if __name__ == "__main__":
    main()
