"""本地数字素材目录：素材登记与按标签查询。

仅使用 Python 3 标准库与 SQLite。源文件保持只读，不扫描目录，不解码素材内容。
"""

import argparse
import json
import os
import sqlite3
import sys

SCHEMA = """
CREATE TABLE assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    tags TEXT NOT NULL
)
"""

EXPECTED_COLUMNS = ["id", "path", "type", "tags"]


class CatalogError(Exception):
    """可预期的业务错误：以退出码 2 结束，不输出调用栈。"""


def _canonical_path(path):
    """解析相对路径、. 、.. 以及符号链接，得到规范化的绝对路径。"""
    return os.path.realpath(os.path.abspath(path))


def _open_database(db_path):
    """打开数据库；不存在时创建并建表，已有库结构不符时报错，不覆盖不重建。"""
    if os.path.dirname(os.path.abspath(db_path)) and not os.path.isdir(
        os.path.dirname(os.path.abspath(db_path))
    ):
        raise CatalogError("数据库父目录不存在: %s" % os.path.dirname(os.path.abspath(db_path)))
    existed = os.path.exists(db_path)
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as exc:
        raise CatalogError("无法打开数据库 %s: %s" % (db_path, exc))
    try:
        if not existed:
            conn.execute(SCHEMA)
            conn.commit()
        else:
            rows = conn.execute("PRAGMA table_info(assets)").fetchall()
            columns = [row[1] for row in rows]
            if columns != EXPECTED_COLUMNS:
                raise CatalogError(
                    "已有数据库不是本产品的素材目录结构: %s" % db_path
                )
    except CatalogError:
        conn.close()
        raise
    except sqlite3.Error as exc:
        conn.close()
        raise CatalogError("数据库损坏或无法访问 %s: %s" % (db_path, exc))
    return conn


def _strip_required(value, what):
    stripped = value.strip()
    if not stripped:
        raise CatalogError("%s去除首尾空白后为空" % what)
    return stripped


def cmd_add(conn, args):
    canonical = _canonical_path(args.file)
    if not os.path.exists(canonical):
        raise CatalogError("登记路径不存在: %s" % args.file)
    if not os.path.isfile(canonical):
        raise CatalogError("登记路径不是普通文件: %s" % args.file)

    asset_type = _strip_required(args.type, "素材类型")
    tags = []
    seen = set()
    for raw in args.tag:
        tag = _strip_required(raw, "标签")
        if tag not in seen:
            seen.add(tag)
            tags.append(tag)

    try:
        conn.execute(
            "INSERT INTO assets (path, type, tags) VALUES (?, ?, ?)",
            (canonical, asset_type, json.dumps(tags, ensure_ascii=False)),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        raise CatalogError("该素材已登记，不覆盖原有记录: %s" % canonical)
    except sqlite3.Error as exc:
        conn.rollback()
        raise CatalogError("数据库写入失败: %s" % exc)

    return {"path": canonical, "type": asset_type, "tags": tags}


def cmd_query(conn, args):
    tag = _strip_required(args.tag, "查询标签")
    try:
        rows = conn.execute("SELECT path, type, tags FROM assets ORDER BY id").fetchall()
    except sqlite3.Error as exc:
        raise CatalogError("数据库读取失败: %s" % exc)
    results = []
    for path, asset_type, tags_json in rows:
        tags = json.loads(tags_json)
        if tag in tags:
            results.append({"path": path, "type": asset_type, "tags": tags})
    return results


def build_parser():
    parser = argparse.ArgumentParser(
        prog="asset_catalog", description="本地数字素材目录：登记与按标签查询"
    )
    parser.add_argument("--db", required=True, help="SQLite 数据库文件路径（必填）")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="登记一个素材文件")
    add_parser.add_argument("file", help="素材文件路径")
    add_parser.add_argument("--type", required=True, help="素材类型（必填）")
    add_parser.add_argument(
        "--tag", required=True, action="append", help="标签，可重复传入（至少一个）"
    )

    query_parser = subparsers.add_parser("query", help="按标签查询素材")
    query_parser.add_argument("--tag", required=True, help="要查询的标签（必填）")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        conn = _open_database(args.db)
        try:
            if args.command == "add":
                result = cmd_add(conn, args)
            else:
                result = cmd_query(conn, args)
        finally:
            conn.close()
    except CatalogError as exc:
        print("错误: %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
