"""命令行入口：素材登记（add）、按标签查询（query）与完整目录导出（export）。

仅使用 Python 3 标准库。所有可预期的错误均以退出码 2 结束，
标准输出为空，标准错误给出单行原因，不输出调用栈。
"""

import argparse
import json
import os
import sqlite3
import stat
import sys

EXIT_OK = 0
EXIT_ERROR = 2

ASSET_COLUMNS = {"id", "path", "type"}
TAG_COLUMNS = {"asset_id", "tag", "position"}


class CliError(Exception):
    """可预期的命令行或数据错误；消息直接展示给用户。"""


class _Parser(argparse.ArgumentParser):
    """将参数错误收敛为单行说明，保持退出码 2、标准输出为空。"""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_ERROR, f"{self.prog}: 错误: {message}\n")


def build_parser():
    parser = _Parser(prog="asset_catalog", description="本地数字素材目录")
    parser.add_argument(
        "--db",
        required=True,
        metavar="DB_FILE",
        help="SQLite 数据库文件路径（必填，不存在时自动创建文件）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="登记一个素材文件")
    add_parser.add_argument("path", help="待登记的素材文件路径")
    add_parser.add_argument("--type", required=True, help="素材类型（必填）")
    add_parser.add_argument(
        "--tag",
        action="append",
        required=True,
        help="素材标签，可重复传入且至少一个",
    )
    add_parser.set_defaults(handler=handle_add)

    query_parser = subparsers.add_parser("query", help="按完整标签查询素材")
    query_parser.add_argument(
        "--tag",
        action="append",
        required=True,
        help="要精确匹配的标签（必填，可重复传入；素材需同时具备全部标签）",
    )
    query_parser.add_argument(
        "--type",
        help="可选素材类型，去除首尾空白后与登记类型完整匹配（区分大小写）",
    )
    query_parser.add_argument(
        "--check-files",
        action="store_true",
        help="为每条结果追加 file_status 字段，报告登记路径当前的文件状态",
    )
    query_parser.set_defaults(handler=handle_query)

    export_parser = subparsers.add_parser(
        "export", help="导出完整目录中全部素材的登记元数据"
    )
    export_parser.set_defaults(handler=handle_export)

    return parser


def open_database(db_path):
    """打开（必要时创建）数据库并校验结构。

    不创建缺失的父目录；数据库损坏或结构不属于本产品时抛出 CliError。
    """
    if not db_path:
        raise CliError("数据库路径不能为空")

    try:
        # 缺父目录、路径为目录、无权限等情况在此处或首次 I/O 时报错。
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    except sqlite3.Error as exc:
        raise CliError(f"无法打开数据库 {db_path}: {exc}")

    table_names = {row[0] for row in rows}
    internal = {"sqlite_sequence"}
    user_tables = table_names - internal

    try:
        if not user_tables:
            create_schema(conn)
        elif "asset" in table_names and "asset_tag" in table_names:
            verify_schema(conn)
        else:
            raise CliError(
                f"数据库 {db_path} 结构不属于本产品，拒绝覆盖或重建"
            )
    except sqlite3.Error as exc:
        conn.close()
        raise CliError(f"数据库 {db_path} 已损坏或无法读写: {exc}")

    return conn


def create_schema(conn):
    conn.executescript(
        """
        CREATE TABLE asset (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT    NOT NULL UNIQUE,
            type TEXT    NOT NULL
        );
        CREATE TABLE asset_tag (
            asset_id INTEGER NOT NULL REFERENCES asset(id),
            tag      TEXT    NOT NULL,
            position INTEGER NOT NULL,
            PRIMARY KEY (asset_id, tag)
        );
        CREATE INDEX idx_asset_tag_tag ON asset_tag(tag);
        """
    )
    conn.commit()


def verify_schema(conn):
    asset_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(asset)").fetchall()
    }
    tag_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(asset_tag)").fetchall()
    }
    if asset_cols != ASSET_COLUMNS or tag_cols != TAG_COLUMNS:
        raise CliError("数据库表结构与本产品不兼容，拒绝覆盖或重建")


def normalize_tags(raw_tags):
    """去除首尾空白、按首次出现顺序去重；空标签报错。大小写保留。"""
    tags = []
    seen = set()
    for raw in raw_tags:
        tag = raw.strip()
        if not tag:
            raise CliError("标签 --tag 去除首尾空白后不能为空")
        if tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags


def handle_add(args):
    asset_type = args.type.strip()
    if not asset_type:
        raise CliError("素材类型 --type 去除首尾空白后不能为空")

    # realpath 解析相对路径、. / .. 与符号链接，得到规范绝对路径。
    canonical_path = os.path.realpath(args.path)
    if not os.path.exists(canonical_path):
        raise CliError(f"登记路径不存在: {args.path}")
    if not os.path.isfile(canonical_path):
        raise CliError(f"登记路径不是普通文件: {args.path}")

    tags = normalize_tags(args.tag)

    # 目录数据库不能作为素材登记到自身：两侧路径按同一规则解析为规范
    # 绝对路径后相同即拒绝。检查在打开数据库之前进行，已有空文件不会
    # 因此被初始化，非 SQLite 或不兼容 SQLite 文件也保持原内容。
    canonical_db_path = os.path.realpath(args.db)
    if canonical_path == canonical_db_path:
        raise CliError(f"目录数据库不能作为本次素材登记: {canonical_path}")

    conn = open_database(args.db)
    try:
        existing = conn.execute(
            "SELECT id FROM asset WHERE path = ?", (canonical_path,)
        ).fetchone()
        if existing is not None:
            raise CliError(f"素材已登记，拒绝重复登记: {canonical_path}")

        cur = conn.execute(
            "INSERT INTO asset(path, type) VALUES (?, ?)",
            (canonical_path, asset_type),
        )
        asset_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO asset_tag(asset_id, tag, position) VALUES (?, ?, ?)",
            [(asset_id, tag, position) for position, tag in enumerate(tags)],
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        raise CliError(f"素材已登记，拒绝重复登记: {canonical_path}")
    except sqlite3.Error as exc:
        conn.rollback()
        raise CliError(f"数据库写入失败: {exc}")
    finally:
        conn.close()

    print(
        json.dumps(
            {"path": canonical_path, "type": asset_type, "tags": tags},
            ensure_ascii=False,
        )
    )
    return EXIT_OK


def check_file_status(path):
    """返回登记路径当前的文件状态：present / missing / not_file。

    只读取文件状态，不读取文件内容。符号链接按其指向的对象判断，
    断开的链接归为 missing。权限不足或其他系统错误抛出 CliError，
    由上层保证整次命令失败且不输出部分结果。
    """
    try:
        st = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        # 路径本身或中间目录不存在（含断开的符号链接）。
        return "missing"
    except OSError as exc:
        raise CliError(f"无法确定文件状态: {path}: {exc.strerror or exc}")
    if stat.S_ISREG(st.st_mode):
        return "present"
    return "not_file"


def assemble_records(conn, rows):
    """把 (id, path, type) 行整理为输出记录列表。

    每条记录含 path、type、tags；tags 为该素材的完整标签集，按登记时的
    position 排序。记录按 rows 的顺序（即首次登记顺序）各出现一次。
    """
    records = {
        asset_id: {"path": path, "type": asset_type, "tags": []}
        for asset_id, path, asset_type in rows
    }
    if records:
        asset_ids = list(records)
        all_tags = conn.execute(
            f"""
            SELECT asset_id, tag FROM asset_tag
            WHERE asset_id IN ({",".join("?" for _ in asset_ids)})
            ORDER BY asset_id ASC, position ASC
            """,
            asset_ids,
        ).fetchall()
        tags_by_asset = {}
        for asset_id, tag_name in all_tags:
            tags_by_asset.setdefault(asset_id, []).append(tag_name)
        for asset_id in asset_ids:
            records[asset_id]["tags"] = tags_by_asset.get(asset_id, [])
    return [records[asset_id] for asset_id in records]


def handle_query(args):
    # 每个标签分别去除首尾空白并按首次出现顺序去重；素材需同时具备
    # 全部不同标签才入选。大小写保留，匹配区分大小写且不做子串匹配。
    tags = normalize_tags(args.tag)

    asset_type = None
    if args.type is not None:
        asset_type = args.type.strip()
        if not asset_type:
            raise CliError("素材类型 --type 去除首尾空白后不能为空")

    conn = open_database(args.db)
    try:
        type_clause = "AND a.type = ?" if asset_type is not None else ""
        # 素材在命中标签集合中的不同标签数等于条件标签数时，
        # 才同时具备全部标签；每个素材只入选一次。
        params = [*tags]
        if asset_type is not None:
            params.append(asset_type)
        params.append(len(tags))
        rows = conn.execute(
            f"""
            SELECT a.id, a.path, a.type
            FROM asset_tag t
            JOIN asset a ON a.id = t.asset_id
            WHERE t.tag IN ({",".join("?" for _ in tags)})
            {type_clause}
            GROUP BY a.id
            HAVING COUNT(DISTINCT t.tag) = ?
            ORDER BY a.id ASC
            """,
            params,
        ).fetchall()

        result = assemble_records(conn, rows)
    except sqlite3.Error as exc:
        raise CliError(f"数据库读取失败: {exc}")
    finally:
        conn.close()

    if args.check_files:
        # 先完成全部状态检查再输出：任一失败则整次命令报错，不输出部分结果。
        for record in result:
            record["file_status"] = check_file_status(record["path"])
    print(json.dumps(result, ensure_ascii=False))
    return EXIT_OK


def handle_export(args):
    # 直接反映目录中的登记记录：不读取素材内容，也不检查登记路径当前的
    # 文件状态，因此源文件已删除或变为目录都不会漏掉素材。
    conn = open_database(args.db)
    try:
        rows = conn.execute(
            """
            SELECT id, path, type FROM asset
            ORDER BY id ASC
            """
        ).fetchall()

        # 按首次登记顺序（id 升序）每条素材输出一次；空目录输出 []。
        result = assemble_records(conn, rows)
    except sqlite3.Error as exc:
        raise CliError(f"数据库读取失败: {exc}")
    finally:
        conn.close()

    print(json.dumps(result, ensure_ascii=False))
    return EXIT_OK


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except CliError as exc:
        print(f"asset_catalog: 错误: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
