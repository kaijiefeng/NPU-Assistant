#!/usr/bin/env python3
"""Release notes helper for NPU-Assistant.

The GitHub Actions release workflow uses this script to:

1. verify that a release tag (``v0.2.1``) matches ``APP_VERSION`` in
   ``backend/app/config.py``;
2. extract the matching section from ``CHANGELOG.md`` (application release) or
   a dataset card under ``docs/npu-handbook/release/`` (dataset release), so the
   GitHub Release body stays in sync with the repository.

Exit codes:
    0  success
    1  tag / APP_VERSION mismatch, or an unsupported tag
    2  unexpected I/O or parsing error
    3  release-notes section not found (the caller may fall back to
       ``gh release create --generate-notes``)
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG_PATH = ROOT / "CHANGELOG.md"
CONFIG_PATH = ROOT / "backend" / "app" / "config.py"
DATASET_RELEASE_DIR = ROOT / "docs" / "npu-handbook" / "release"

VERSION_PATTERN = r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?"
# ``## v0.2.0`` followed by a space, end of line, an em dash or a parenthesis.
HEADING_RE = re.compile(
    rf"^##\s+(?P<version>v{VERSION_PATTERN})(?=\s|$|—|-|（|\()",
    re.MULTILINE,
)
NEXT_SECTION_RE = re.compile(r"^##\s+", re.MULTILINE)


class NotesError(Exception):
    """Raised for malformed changelog / configuration input."""


def read_app_version(config_path: Path = CONFIG_PATH) -> str:
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise NotesError(f"无法读取 {config_path}：{exc}") from exc
    match = re.search(r'(?m)^APP_VERSION\s*=\s*"([^"]+)"', text)
    if not match:
        raise NotesError(f"{config_path} 中没有找到 APP_VERSION")
    return match.group(1).strip()


def normalize_app_tag(tag: str) -> str:
    value = (tag or "").strip()
    if not re.fullmatch(rf"v?{VERSION_PATTERN}", value):
        raise NotesError(f"不是合法的应用版本 tag：{tag!r}（应形如 v0.2.1）")
    return value[1:] if value.startswith("v") else value


def versions_match(tag_version: str, app_version: str) -> bool:
    """Allow ``v0.2.1-rc1`` to be used with ``APP_VERSION = "0.2.1"``."""
    if tag_version == app_version:
        return True
    base = tag_version.split("-", 1)[0]
    return "-" in tag_version and base == app_version


def _heading_line(text: str, position: int) -> str:
    end = text.find("\n", position)
    return text[position:] if end == -1 else text[position:end]


def find_app_section(text: str, version: str) -> tuple[str, str] | None:
    """Return ``(heading, body)`` for ``version``.

    Dataset entries in CHANGELOG.md use headings such as
    ``## v0.2.0 数据集 — ...``; skip those when looking for the application
    section of the same version.
    """
    wanted = version.lstrip("v")
    for match in HEADING_RE.finditer(text):
        if match.group("version").lstrip("v") != wanted:
            continue
        heading = _heading_line(text, match.start()).strip()
        remainder = heading[3:].strip()  # drop "## "
        if re.match(rf"v{re.escape(wanted)}\s*数据集", remainder):
            continue
        body_start = text.find("\n", match.end())
        if body_start == -1:
            return heading, ""
        body_start += 1
        next_match = NEXT_SECTION_RE.search(text, body_start)
        body_end = next_match.start() if next_match else len(text)
        body = text[body_start:body_end].strip()
        return heading, body
    return None


def find_dataset_section(tag: str) -> tuple[str, str] | None:
    version = (tag or "").strip()
    prefix = "dataset-v"
    if not version.startswith(prefix):
        raise NotesError(f"不是合法的数据集 tag：{tag!r}（应形如 dataset-v2026.21）")
    number = version[len(prefix) :]
    if not re.fullmatch(r"\d{4}\.\d{1,2}", number):
        raise NotesError(f"数据集版本号不合法：{number!r}（应形如 2026.21）")
    card = DATASET_RELEASE_DIR / f"v{number}" / "dataset-card.md"
    if not card.is_file():
        return None
    body = card.read_text(encoding="utf-8").strip()
    if body.startswith("---"):
        # Drop YAML front matter if a card adds it later.
        parts = body.split("---", 2)
        if len(parts) == 3:
            body = parts[2].strip()
    return f"dataset-v{number}", body


def write_output(text: str, output: str | None) -> None:
    if not output:
        sys.stdout.write(text)
        if text and not text.endswith("\n"):
            sys.stdout.write("\n")
        return
    target = Path(output)
    target.write_text(text.rstrip() + "\n", encoding="utf-8")


def run_self_test() -> int:
    sample = """# 变更记录

## v0.2.0 — 2026-11-09（服务闭环发布版）

主题：app-body
- 变更一

## v0.2.0 数据集 — 2026-10-27（应用版本 v0.1.5）

- dataset-body

## v0.1.9 — 2026-11-01（旧版本）

- old-body
"""
    section = find_app_section(sample, "0.2.0")
    assert section is not None, "v0.2.0 section was not found"
    heading, body = section
    assert "app-body" in body, body
    assert "dataset-body" not in body, body
    assert "old-body" not in body, body
    assert heading.startswith("## v0.2.0"), heading

    section = find_app_section(sample, "0.2.0")
    assert section is not None

    assert versions_match("0.2.1", "0.2.1")
    assert versions_match("0.2.1-rc1", "0.2.1")
    assert not versions_match("0.2.1", "0.2.0")

    # 边界：章节在文件末尾且没有结尾换行。
    eof_section = find_app_section("## v1.2.3\n\n正文无结尾换行", "1.2.3")
    assert eof_section == ("## v1.2.3", "正文无结尾换行"), eof_section
    # 边界：hybrid/rc 标题、缺少标题。
    assert find_app_section("## v0.2.1-rc1 — 预发布\n\nrc-body\n", "0.2.1-rc1") is not None
    assert find_app_section("## v0.2.0 — 2026-11-09\n\nbody\n", "0.2.9") is None

    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "config.py"
        config.write_text('APP_VERSION = "1.2.3"\n', encoding="utf-8")
        assert read_app_version(config) == "1.2.3"

        target = Path(tmp) / "release-notes.md"
        write_output("## v1.2.3\n\nbody", str(target))
        assert target.read_text(encoding="utf-8") == "## v1.2.3\n\nbody\n"

    # dataset card：目录命名、YAML front matter、缺失回退。
    original_dataset_dir = DATASET_RELEASE_DIR
    try:
        with tempfile.TemporaryDirectory() as tmp:
            card_dir = Path(tmp) / "v2099.1"
            card_dir.mkdir()
            (card_dir / "dataset-card.md").write_text(
                "---\ntitle: 测试\n---\n数据集正文\n", encoding="utf-8"
            )
            globals()["DATASET_RELEASE_DIR"] = Path(tmp)
            dataset_section = find_dataset_section("dataset-v2099.1")
            assert dataset_section == ("dataset-v2099.1", "数据集正文"), dataset_section
            assert find_dataset_section("dataset-v2099.2") is None
    finally:
        globals()["DATASET_RELEASE_DIR"] = original_dataset_dir

    print("release_notes.py self-test: OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认可能是 cp1252/GBK；CI 与本地都统一按 UTF-8 输出。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="NPU-Assistant release notes helper")
    parser.add_argument("--version", metavar="TAG", help="应用 tag，如 v0.2.1")
    parser.add_argument("--dataset-version", metavar="TAG", help="数据集 tag，如 dataset-v2026.21")
    parser.add_argument("--verify-version", metavar="TAG", help="校验 tag 与 APP_VERSION 是否一致")
    parser.add_argument("--output", metavar="FILE", help="写入文件；缺省输出到 stdout")
    parser.add_argument("--check", action="store_true", help="只检查章节是否存在")
    parser.add_argument("--self-test", action="store_true", help="运行解析自检")
    args = parser.parse_args(argv)

    try:
        if args.self_test:
            return run_self_test()

        if args.verify_version:
            version = normalize_app_tag(args.verify_version)
            app_version = read_app_version()
            if not versions_match(version, app_version):
                print(
                    f"::error::tag {args.verify_version} 与 backend/app/config.py 的 "
                    f"APP_VERSION={app_version} 不一致；请先统一版本号再打 tag",
                    file=sys.stderr,
                )
                return 1
            print(f"OK: tag {args.verify_version} 对应 APP_VERSION {app_version}")
            return 0

        if args.version and args.dataset_version:
            parser.error("--version 与 --dataset-version 只能二选一")

        if args.version:
            version = normalize_app_tag(args.version)
            try:
                text = CHANGELOG_PATH.read_text(encoding="utf-8")
            except OSError as exc:
                raise NotesError(f"无法读取 {CHANGELOG_PATH}：{exc}") from exc
            section = find_app_section(text, version)
            if not section:
                message = f"CHANGELOG.md 中没有 v{version} 章节（请先补写发布说明）"
                print(message, file=sys.stderr)
                return 3
            heading, body = section
            if args.check:
                print(f"OK: 找到 {heading}")
                return 0
            write_output(f"## {heading.removeprefix('## ').strip()}\n\n{body}", args.output)
            return 0

        if args.dataset_version:
            section = find_dataset_section(args.dataset_version)
            if not section:
                print(
                    f"没有找到 {args.dataset_version} 的 dataset card："
                    f"{DATASET_RELEASE_DIR / ('v' + args.dataset_version.removeprefix('dataset-v')) / 'dataset-card.md'}",
                    file=sys.stderr,
                )
                return 3
            _heading, body = section
            if args.check:
                print(f"OK: 找到 {args.dataset_version} dataset card")
                return 0
            write_output(body, args.output)
            return 0

        parser.error("需要 --version、--dataset-version 或 --verify-version")
    except NotesError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
