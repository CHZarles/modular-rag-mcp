"""通过 SSH/rsync 从群晖同步 PDF，再交给本地摄取流水线。"""

from __future__ import annotations

import argparse
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ingest import main as ingest_main  # noqa: E402

DEFAULT_CACHE_DIR = PROJECT_ROOT / "data" / "imports" / "synology"
_HOST_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*$")
_USER_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")


def build_rsync_command(args: argparse.Namespace, cache_dir: Path) -> list[str]:
    """构造只同步 PDF 的 rsync 命令。"""
    if not _HOST_PATTERN.fullmatch(args.host):
        raise ValueError("群晖地址只能包含字母、数字、点和连字符")
    if not _USER_PATTERN.fullmatch(args.user):
        raise ValueError("SSH 用户名包含不支持的字符")
    if not args.remote_path.startswith("/") or "\n" in args.remote_path:
        raise ValueError("群晖目录必须是绝对路径，例如 /volume1/documents")
    if not 1 <= args.ssh_port <= 65535:
        raise ValueError("SSH 端口必须在 1 到 65535 之间")

    ssh = ["ssh", "-p", str(args.ssh_port)]
    if args.identity_file:
        ssh.extend(["-i", str(Path(args.identity_file).expanduser())])

    remote_path = f"{args.remote_path.rstrip('/')}/"
    command = [
        "rsync",
        "-rtz",
        "--prune-empty-dirs",
        "--include=*/",
        "--include=*.[pP][dD][fF]",
        "--exclude=*",
    ]
    if args.dry_run:
        command.append("--dry-run")
    command.extend(
        [
            "-e",
            shlex.join(ssh),
            f"{args.user}@{args.host}:{shlex.quote(remote_path)}",
            f"{cache_dir}/",
        ]
    )
    return command


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if shutil.which("rsync") is None:
        print("导入失败: 未找到 rsync，请先安装 rsync", file=sys.stderr)
        return 2

    cache_dir = Path(args.cache_dir).expanduser().resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        command = build_rsync_command(args, cache_dir)
    except ValueError as exc:
        print(f"导入参数错误: {exc}", file=sys.stderr)
        return 2

    print(f"正在从群晖同步 PDF 到 {cache_dir}")
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        print(f"群晖同步失败: rsync 退出码 {result.returncode}", file=sys.stderr)
        return result.returncode
    if args.dry_run:
        print("演练完成，未下载或摄取文件")
        return 0

    ingest_args = ["--path", str(cache_dir), "--collection", args.collection]
    if args.force:
        ingest_args.append("--force")
    return ingest_main(ingest_args, settings_path=args.settings)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从群晖同步 PDF 并导入 RAG 知识库")
    parser.add_argument("--host", required=True, help="群晖主机名或 IPv4 地址")
    parser.add_argument("--user", required=True, help="群晖 SSH 用户名")
    parser.add_argument(
        "--remote-path",
        required=True,
        help="群晖上的绝对目录，例如 /volume1/documents",
    )
    parser.add_argument("--ssh-port", type=int, default=22, help="SSH 端口，默认 22")
    parser.add_argument("--identity-file", help="SSH 私钥路径；未设置时使用 SSH 默认认证")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR, help="PDF 本地缓存目录")
    parser.add_argument("--collection", default="synology", help="目标知识集合名称")
    parser.add_argument("--settings", help="可选的 settings.yaml 路径")
    parser.add_argument("--force", action="store_true", help="强制重新摄取未变化的 PDF")
    parser.add_argument("--dry-run", action="store_true", help="只预览同步，不下载或摄取")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
