"""通过图形选择器创建或更新 GitHub Release 并上传附件。"""

from __future__ import annotations

import argparse
import getpass
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen


API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "McuStdio-release-uploader"


class GitHubApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status


def run_git(*arguments: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", *arguments],
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        return ""
    return result.stdout.strip()


def infer_repository() -> str:
    remote = run_git("config", "--get", "remote.origin.url")
    match = re.search(r"github\.com[/:]([^/]+)/([^/]+?)(?:\.git)?$", remote)
    if not match:
        return ""
    return f"{match.group(1)}/{match.group(2)}"


def credential_token() -> str:
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        token = os.environ.get(name, "").strip()
        if token:
            return token

    output = run_git(
        "credential",
        "fill",
        input_text="protocol=https\nhost=github.com\n\n",
    )
    values = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values.get("password", "").strip()


def api_request(
    method: str,
    repository: str,
    endpoint: str,
    token: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{API_ROOT}/repos/{repository}{endpoint}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urlopen(request, timeout=60) as response:
            content = response.read()
    except HTTPError as exc:
        content = exc.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(content).get("message", content)
        except json.JSONDecodeError:
            message = content
        raise GitHubApiError(exc.code, str(message)) from exc
    except URLError as exc:
        raise RuntimeError(f"无法连接 GitHub：{exc.reason}") from exc
    if not content:
        return None
    return json.loads(content.decode("utf-8"))


def upload_asset(upload_url: str, path: Path, token: str) -> dict[str, Any]:
    base_url = upload_url.split("{", 1)[0]
    parsed = urlparse(f"{base_url}?{urlencode({'name': path.name})}")
    connection = http.client.HTTPSConnection(parsed.hostname, parsed.port, timeout=120)
    total = path.stat().st_size
    sent = 0
    try:
        connection.putrequest("POST", f"{parsed.path}?{parsed.query}")
        connection.putheader("Authorization", f"Bearer {token}")
        connection.putheader("Accept", "application/vnd.github+json")
        connection.putheader("X-GitHub-Api-Version", API_VERSION)
        connection.putheader("User-Agent", USER_AGENT)
        connection.putheader("Content-Type", "application/octet-stream")
        connection.putheader("Content-Length", str(total))
        connection.endheaders()
        with path.open("rb") as source:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                connection.send(chunk)
                sent += len(chunk)
                percent = 100 if not total else sent * 100 // total
                print(f"\r  {path.name}: {percent:3d}%  ({sent}/{total} bytes)", end="", flush=True)
        print()
        response = connection.getresponse()
        content = response.read().decode("utf-8", errors="replace")
        if response.status != 201:
            try:
                message = json.loads(content).get("message", content)
            except json.JSONDecodeError:
                message = content
            raise GitHubApiError(response.status, str(message))
        return json.loads(content)
    finally:
        connection.close()


def choose_with_gui(args: argparse.Namespace) -> None:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, simpledialog
    except ImportError as exc:
        raise RuntimeError("当前 Python 没有 tkinter，请通过命令行参数指定文件") from exc

    root = tk.Tk()
    root.withdraw()
    root.update()
    try:
        if not args.repository:
            args.repository = simpledialog.askstring(
                "GitHub 仓库", "仓库（所有者/仓库名）：", initialvalue=infer_repository(), parent=root
            )
        if not args.tag:
            args.tag = simpledialog.askstring(
                "Release Tag", "Tag，例如 v2.4.0：", parent=root
            )
        if not args.title:
            args.title = simpledialog.askstring(
                "Release 标题",
                "Release 标题：",
                initialvalue=f"MCU Live Watch Studio {args.tag or ''}".strip(),
                parent=root,
            )
        if not args.notes:
            selected = filedialog.askopenfilename(
                title="选择 Markdown 发布说明",
                filetypes=(("Markdown", "*.md"), ("所有文件", "*.*")),
                parent=root,
            )
            args.notes = selected or ""
        if not args.assets:
            args.assets = list(
                filedialog.askopenfilenames(title="选择要上传的 Release 附件", parent=root)
            )

        summary = (
            f"仓库：{args.repository}\nTag：{args.tag}\n标题：{args.title}\n"
            f"Markdown：{args.notes}\n附件：\n"
            + "\n".join(f"  • {item}" for item in args.assets)
        )
        if not args.yes and not messagebox.askyesno("确认发布", summary, parent=root):
            raise KeyboardInterrupt
    finally:
        root.destroy()


def validate_inputs(args: argparse.Namespace) -> tuple[Path, list[Path]]:
    if not args.repository or not re.fullmatch(r"[^/\s]+/[^/\s]+", args.repository):
        raise ValueError("仓库格式必须是 所有者/仓库名")
    if not args.tag:
        raise ValueError("Tag 不能为空")
    if not args.title:
        raise ValueError("Release 标题不能为空")
    notes = Path(args.notes).expanduser().resolve()
    if not notes.is_file():
        raise FileNotFoundError(f"Markdown 不存在：{notes}")
    assets = [Path(item).expanduser().resolve() for item in args.assets]
    if not assets:
        raise ValueError("至少选择一个附件")
    missing = [str(path) for path in assets if not path.is_file()]
    if missing:
        raise FileNotFoundError("附件不存在：\n" + "\n".join(missing))
    duplicate_names = {path.name for path in assets if sum(other.name == path.name for other in assets) > 1}
    if duplicate_names:
        raise ValueError("附件文件名重复：" + ", ".join(sorted(duplicate_names)))
    return notes, assets


def create_or_update_release(
    args: argparse.Namespace, notes_text: str, token: str
) -> dict[str, Any]:
    try:
        release = api_request(
            "GET", args.repository, f"/releases/tags/{quote(args.tag, safe='')}", token
        )
    except GitHubApiError as exc:
        if exc.status != 404:
            raise
        release = api_request(
            "POST",
            args.repository,
            "/releases",
            token,
            {
                "tag_name": args.tag,
                "target_commitish": args.target,
                "name": args.title,
                "body": notes_text,
                "draft": args.draft,
                "prerelease": args.prerelease,
            },
        )
        print(f"已创建 Release：{release['html_url']}")
        return release

    release = api_request(
        "PATCH",
        args.repository,
        f"/releases/{release['id']}",
        token,
        {
            "name": args.title,
            "body": notes_text,
            "draft": args.draft,
            "prerelease": args.prerelease,
        },
    )
    print(f"已更新 Release：{release['html_url']}")
    return release


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="选择 Markdown 和附件并上传 GitHub Release")
    parser.add_argument("--repo", dest="repository", default=infer_repository(), help="所有者/仓库名")
    parser.add_argument("--tag", help="Release Tag，例如 v2.4.0")
    parser.add_argument("--title", help="Release 标题")
    parser.add_argument("--notes", help="Markdown 发布说明路径")
    parser.add_argument("--asset", dest="assets", action="append", default=[], help="附件路径，可重复")
    parser.add_argument("--target", default="main", help="新建 Tag 时对应的分支或提交，默认 main")
    parser.add_argument("--draft", action="store_true", help="创建为草稿")
    parser.add_argument("--prerelease", action="store_true", help="标记为预发布")
    parser.add_argument("--no-replace", action="store_true", help="同名附件存在时停止，不替换")
    parser.add_argument("--yes", action="store_true", help="跳过图形确认")
    parser.add_argument("--dry-run", action="store_true", help="仅检查选择，不访问 GitHub")
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    if not all((args.repository, args.tag, args.title, args.notes, args.assets)):
        choose_with_gui(args)
    notes, assets = validate_inputs(args)
    print(f"仓库：{args.repository}")
    print(f"Tag：{args.tag}")
    print(f"Markdown：{notes}")
    for asset in assets:
        print(f"附件：{asset} ({asset.stat().st_size} bytes)")
    if args.dry_run:
        print("检查完成：dry-run 未访问 GitHub。")
        return 0

    token = credential_token()
    if not token:
        token = getpass.getpass("GitHub Token（不会保存）：").strip()
    if not token:
        raise RuntimeError("未找到 GitHub 凭据，请设置 GH_TOKEN 或先登录 Git Credential Manager")

    release = create_or_update_release(args, notes.read_text(encoding="utf-8-sig"), token)
    existing = {asset["name"]: asset for asset in release.get("assets", [])}
    for path in assets:
        old_asset = existing.get(path.name)
        if old_asset:
            if args.no_replace:
                raise RuntimeError(f"Release 已存在同名附件：{path.name}")
            print(f"删除同名旧附件：{path.name}")
            api_request("DELETE", args.repository, f"/releases/assets/{old_asset['id']}", token)
        print(f"上传：{path.name}")
        upload_asset(release["upload_url"], path, token)

    print(f"完成：{release['html_url']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("已取消。")
        raise SystemExit(130)
    except (OSError, RuntimeError, ValueError, GitHubApiError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
