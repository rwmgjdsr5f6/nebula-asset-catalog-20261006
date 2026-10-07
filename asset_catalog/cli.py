"""命令行入口：素材登记（add）、按标签查询（query）、完整目录导出（export）、
按路径查看单条记录（show）、标签替换、追加或移除（retag）、类型修改（retype）
与已用标签统计（tags）。

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
        "--tag-mode",
        choices=("all", "any"),
        default="all",
        help=(
            "多标签命中方式：all（默认）要求素材同时具备全部不同标签；"
            "any 只要具备任一标签即入选。只接受小写 all 或 any"
        ),
    )
    query_parser.add_argument(
        "--tag",
        action="append",
        required=True,
        help="要精确匹配的标签（必填，可重复传入；命中方式由 --tag-mode 决定）",
    )
    query_parser.add_argument(
        "--exclude-tag",
        action="append",
        default=None,
        help=(
            "可选排除条件，可重复传入：先按 --tag 与 --tag-mode、--type "
            "确定范围，再排除具备任一排除标签的素材（区分大小写、完整匹配）"
        ),
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
    query_parser.add_argument(
        "--file-status",
        choices=("present", "missing", "not_file"),
        help=(
            "只返回当前文件状态为指定值的素材：present（普通文件）、"
            "missing（路径不存在）、not_file（目录等非普通文件）。"
            "只接受小写 present、missing 或 not_file"
        ),
    )
    query_parser.set_defaults(handler=handle_query)

    export_parser = subparsers.add_parser(
        "export", help="导出完整目录中全部素材的登记元数据"
    )
    export_parser.add_argument(
        "--check-files",
        action="store_true",
        help="为每条记录追加 file_status 字段，报告登记路径当前的文件状态",
    )
    export_parser.set_defaults(handler=handle_export)

    tags_parser = subparsers.add_parser(
        "tags", help="列出全部已用标签及使用各标签的已登记素材数"
    )
    tags_parser.add_argument(
        "--type",
        help=(
            "可选素材类型，去除首尾空白后与登记类型完整匹配（区分大小写），"
            "只统计该类型的素材；不传时统计全目录"
        ),
    )
    tags_parser.set_defaults(handler=handle_tags)

    show_parser = subparsers.add_parser(
        "show", help="按素材路径查看单条已登记记录"
    )
    show_parser.add_argument("path", help="已登记素材的路径")
    show_parser.add_argument(
        "--check-files",
        action="store_true",
        help="为该条记录追加 file_status 字段，报告登记路径当前的文件状态",
    )
    show_parser.set_defaults(handler=handle_show)

    retag_parser = subparsers.add_parser(
        "retag", help="替换、追加或移除一条已登记素材的标签"
    )
    retag_parser.add_argument("path", help="已登记素材的路径")
    retag_parser.add_argument(
        "--tag",
        action="append",
        required=True,
        help="新标签或待移除标签，可重复传入且至少一个；默认完整替换旧标签",
    )
    mode_group = retag_parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--append",
        action="store_true",
        help="保留旧标签并把新标签追加到末尾；不传时完整替换旧标签",
    )
    mode_group.add_argument(
        "--remove",
        action="store_true",
        help="从旧标签中移除指定标签，其余标签保持原顺序；与 --append 互斥",
    )
    retag_parser.set_defaults(handler=handle_retag)

    retype_parser = subparsers.add_parser(
        "retype", help="修改一条已登记素材的类型"
    )
    retype_parser.add_argument("path", help="已登记素材的路径")
    retype_parser.add_argument(
        "--type", required=True, help="新的素材类型（必填）"
    )
    retype_parser.set_defaults(handler=handle_retype)

    return parser


def open_database(db_path):
    """打开（必要时创建）数据库并校验结构。

    不创建缺失的父目录；数据库损坏或结构不属于本产品时抛出 CliError。
    """
    if not db_path:
        raise CliError("数据库路径不能为空")

    conn = None
    try:
        # 缺父目录、路径为目录、无权限等情况在此处或首次 I/O 时报错。
        conn = sqlite3.connect(db_path)
        # 同时清点用户表与用户视图：视图可以只由常量表达式构成而不依赖任何
        # 表，因此“没有用户表”不等于“没有已有业务内容”。只含用户视图
        # （即使视图名为 asset 或 asset_tag）的 SQLite 文件同样不属于本产品
        # 结构，必须原样拒绝，不能在其中补建素材表与标签表。
        objects = conn.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE type IN ('table', 'view')"
        ).fetchall()
    except sqlite3.Error as exc:
        if conn is not None:
            conn.close()
        raise CliError(f"无法打开数据库 {db_path}: {exc}")

    table_names = {name for kind, name in objects if kind == "table"}
    view_names = {name for kind, name in objects if kind == "view"}
    internal = {"sqlite_sequence"}
    user_tables = table_names - internal

    try:
        if "asset" in table_names and "asset_tag" in table_names:
            verify_schema(conn)
        elif user_tables or view_names:
            # 有任意用户业务对象（用户表或用户视图）却不具备本产品的两张
            # 兼容表：属于其他产品/用途的数据库，拒绝覆盖或重建。
            raise CliError(
                f"数据库 {db_path} 结构不属于本产品，拒绝覆盖或重建"
            )
        else:
            # 既无用户表也无用户视图：零字节文件或完全空白的 SQLite 库，
            # 按既有规则初始化为空目录库。
            create_schema(conn)
    except sqlite3.Error as exc:
        conn.close()
        raise CliError(f"数据库 {db_path} 已损坏或无法读写: {exc}")
    except CliError:
        # 结构不属于本产品：不做任何写入，关闭只读连接后原样抛出，
        # 保证被拒绝的文件不留下日志或页缓存改动。
        conn.close()
        raise

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


def normalize_tags(raw_tags, option="--tag"):
    """去除首尾空白、按首次出现顺序去重；空标签报错。大小写保留。

    option 仅用于错误信息，指出触发错误的具体选项（--tag 或
    --exclude-tag）。
    """
    tags = []
    seen = set()
    for raw in raw_tags:
        tag = raw.strip()
        if not tag:
            raise CliError(f"标签 {option} 去除首尾空白后不能为空")
        if tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags


def _path_registered(conn, canonical_path):
    """登记表中是否已存在该规范路径；查询本身失败时按未登记处理。"""
    try:
        row = conn.execute(
            "SELECT 1 FROM asset WHERE path = ?", (canonical_path,)
        ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


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

        try:
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
        except sqlite3.Error as exc:
            conn.rollback()
            # 完整性错误不一定来自重复路径：触发器或其他约束同样会拒绝
            # 写入。回滚后按登记表的实际内容归类——该规范路径已存在才算
            # 重复登记，否则说明数据库写入失败并保留数据库给出的原因。
            if isinstance(exc, sqlite3.IntegrityError) and _path_registered(
                conn, canonical_path
            ):
                raise CliError(f"素材已登记，拒绝重复登记: {canonical_path}")
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


def build_records(conn, rows):
    """把 (id, path, type) 行组装为输出记录列表，保持输入行顺序。

    每条记录含规范绝对路径 path、登记类型 type 和按登记顺序排列的完整
    标签 tags；路径、类型与标签的整理规则在 query 与 export 间只维护
    这一处。rows 为空时返回 []，不访问标签表。
    """
    records = {
        asset_id: {"path": path, "type": asset_type, "tags": []}
        for asset_id, path, asset_type in rows
    }

    # 完整标签集按登记时的 position 排序，保留每个素材的标签顺序。
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

    # 每个素材只出现一次，顺序与输入行一致（调用方按 id 升序给行，
    # 即首次登记顺序）。
    return [records[asset_id] for asset_id in records]


def apply_file_status_rules(records, *, check_files, status_filter):
    """对候选记录统一执行文件状态规则，供 --check-files 与 --file-status
    共用同一套处理逻辑，组合语义可直接对照下表核对：

    check_files | status_filter | 结果
    ------------+---------------+------------------------------------------
    否          | None          | 不检查文件；原样返回候选（仅 path、type、
                |               | tags）
    是          | None          | 保留全部候选，逐条追加 file_status
    否          | 给定状态      | 只保留状态相符的候选以缩小结果，不增加字段
    是          | 给定状态      | 只保留状态相符的候选，并给入选记录追加
                |               | file_status，其值必与筛选值一致

    候选的确定（标签命中、--type、--exclude-tag）在调用前完成：未命中或
    被排除的素材不会出现在 records 中，因此其路径不会被检查状态。

    先读取完全部候选的状态再组装输出：任一候选因权限或其他系统错误无法
    判断状态时，check_file_status 抛出 CliError，本函数不修改任何记录，
    由上层保证整次命令退出码 2、标准输出为空、不输出部分结果。

    记录的首次登记顺序、每条素材只出现一次以及完整标签及顺序均由候选
    本身保证：只附加状态时保持原顺序与条数，筛选时保持入选候选的相对
    顺序，本函数不重排、不去重标签。
    """
    if not check_files and status_filter is None:
        # 两组选项均未启用：完全不访问文件系统。
        return records

    # 先逐条读取全部候选的当前状态再决定取舍与输出：此处任一失败都会
    # 整体抛出，此前读到的状态只存在于本地列表，不会产生部分输出。
    statuses = [check_file_status(record["path"]) for record in records]

    if status_filter is not None:
        # 只要求状态筛选：缩小结果；入选记录的状态必与筛选值一致。
        selected = [
            (record, status)
            for record, status in zip(records, statuses)
            if status == status_filter
        ]
    else:
        # 只要求附加状态：保留全部候选及其相对顺序。
        selected = list(zip(records, statuses))

    if check_files:
        # 同用时仅入选记录带状态字段，且字段值与筛选值一致；
        # 单独使用 --check-files 时全部候选带各自的当前状态。
        for record, status in selected:
            record["file_status"] = status

    return [record for record, _ in selected]


def handle_query(args):
    # 每个标签分别去除首尾空白并按首次出现顺序去重；大小写保留，匹配区分
    # 大小写且不做子串匹配。tag_mode 为 all（默认）时素材需同时具备全部
    # 不同标签才入选，为 any 时具备任一标签即入选。
    tags = normalize_tags(args.tag)
    tag_mode = args.tag_mode

    # 排除标签同样逐个去除首尾空白、按首次出现顺序去重；任一为空或只有
    # 空白即按参数错误拒绝整次查询。校验在打开数据库之前完成，参数错误
    # 不创建数据库。
    exclude_tags = (
        normalize_tags(args.exclude_tag, option="--exclude-tag")
        if args.exclude_tag is not None
        else []
    )

    asset_type = None
    if args.type is not None:
        asset_type = args.type.strip()
        if not asset_type:
            raise CliError("素材类型 --type 去除首尾空白后不能为空")

    conn = open_database(args.db)
    try:
        type_clause = "AND a.type = ?" if asset_type is not None else ""
        # 排除只作用于本次查询：在既有命中范围上排除具备任一排除标签的
        # 素材（完整匹配、区分大小写）。不存在的排除标签不影响结果；
        # 同一标签同时用于命中与排除时仍执行排除。被排除素材不进入候选，
        # 因此后续文件状态检查也不会触及其路径。
        exclude_clause = ""
        if exclude_tags:
            exclude_clause = (
                "AND NOT EXISTS ("
                "SELECT 1 FROM asset_tag et "
                "WHERE et.asset_id = a.id AND et.tag IN "
                f"({','.join('?' for _ in exclude_tags)})"
                ")"
            )
        params = [*tags]
        if asset_type is not None:
            params.append(asset_type)
        params.extend(exclude_tags)
        if tag_mode == "all":
            # 素材在命中标签集合中的不同标签数等于条件标签数时，
            # 才同时具备全部标签；每个素材只入选一次。
            having_clause = "HAVING COUNT(DISTINCT t.tag) = ?"
            params.append(len(tags))
        else:
            # any：行本身即来自命中任一条件标签的素材，分组后每个素材
            # 只入选一次，重复条件与条件顺序都不影响结果。
            having_clause = ""
        rows = conn.execute(
            f"""
            SELECT a.id, a.path, a.type
            FROM asset_tag t
            JOIN asset a ON a.id = t.asset_id
            WHERE t.tag IN ({",".join("?" for _ in tags)})
            {type_clause}
            {exclude_clause}
            GROUP BY a.id
            {having_clause}
            ORDER BY a.id ASC
            """,
            params,
        ).fetchall()

        result = build_records(conn, rows)
    except sqlite3.Error as exc:
        raise CliError(f"数据库读取失败: {exc}")
    finally:
        conn.close()

    # 标签命中方式、可选 --type 与 --exclude-tag 已确定候选（未命中或被
    # 排除的素材不在其中，其路径不检查状态）；--check-files 与
    # --file-status 的全部组合由同一套规则处理。任一候选状态读取失败时
    # apply_file_status_rules 抛出 CliError，此前不输出任何内容，整次
    # 命令按退出码 2 失败、不输出部分结果。
    result = apply_file_status_rules(
        result,
        check_files=args.check_files,
        status_filter=args.file_status,
    )
    print(json.dumps(result, ensure_ascii=False))
    return EXIT_OK


def handle_export(args):
    # 直接反映目录中的登记记录：不读取素材内容；不传 --check-files 时也
    # 不检查登记路径当前的文件状态，因此源文件已删除或变为目录都不会漏掉
    # 素材。
    conn = open_database(args.db)
    try:
        rows = conn.execute(
            """
            SELECT id, path, type FROM asset
            ORDER BY id ASC
            """
        ).fetchall()

        result = build_records(conn, rows)
    except sqlite3.Error as exc:
        raise CliError(f"数据库读取失败: {exc}")
    finally:
        conn.close()

    # --check-files 时复用与 query 相同的规则为每条记录追加 file_status：
    # 保留全部记录及其首次登记顺序，缺失或已变成目录的记录照常输出。
    # 任一记录因权限或其他系统错误无法判断状态时抛出 CliError，此前不
    # 输出任何内容，整次导出按退出码 2 失败、不输出部分结果。状态只反映
    # 当次检查，不写回数据库。
    result = apply_file_status_rules(
        result,
        check_files=args.check_files,
        status_filter=None,
    )

    # 按首次登记顺序（id 升序）每条素材输出一次；空目录输出 []。
    print(json.dumps(result, ensure_ascii=False))
    return EXIT_OK


def handle_tags(args):
    # 只统计数据库中当前保存的标签：不读取素材内容、不检查登记路径当前的
    # 文件状态，也不扫描目录，因此源文件已删除或原路径变成目录的素材仍
    # 参与统计；不改动数据库或源文件。
    #
    # 可选 --type 与 query 的同一规则处理：去除首尾空白后与登记类型完整
    # 匹配（区分大小写），接受任意非空文本，不按扩展名推断；校验在打开
    # 数据库之前完成，参数错误不创建数据库。不传 --type 时统计全目录。
    asset_type = None
    if args.type is not None:
        asset_type = args.type.strip()
        if not asset_type:
            raise CliError("素材类型 --type 去除首尾空白后不能为空")

    conn = open_database(args.db)
    try:
        # 标签按完整文本分组，区分大小写、不改写标签文本；同一素材的同一
        # 标签只计一次（asset_tag 主键已保证唯一，DISTINCT 使语义显式）。
        # 没有素材使用的标签不在 asset_tag 中，自然不会输出零计数项。
        # 传入 --type 时只统计该类型素材的标签，无匹配类型时结果为空。
        if asset_type is None:
            rows = conn.execute(
                """
                SELECT tag, COUNT(DISTINCT asset_id) AS asset_count
                FROM asset_tag
                GROUP BY tag
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT t.tag, COUNT(DISTINCT t.asset_id) AS asset_count
                FROM asset_tag t
                JOIN asset a ON a.id = t.asset_id
                WHERE a.type = ?
                GROUP BY t.tag
                """,
                (asset_type,),
            ).fetchall()
    except sqlite3.Error as exc:
        raise CliError(f"数据库读取失败: {exc}")
    finally:
        conn.close()

    # 每个标签只出现一次，按标签文本的 Unicode 码点字典序升序排列；
    # 空目录输出 []。
    result = [
        {"tag": tag, "asset_count": asset_count}
        for tag, asset_count in sorted(rows, key=lambda row: row[0])
    ]
    print(json.dumps(result, ensure_ascii=False))
    return EXIT_OK


def handle_show(args):
    # 路径按 add 的同一规则解析为规范绝对路径：相对路径、. / .. 与解析后
    # 指向同一登记路径的符号链接都定位同一记录。不传 --check-files 时只
    # 反映数据库中的登记内容：不读取素材内容，也不检查登记路径当前的文件
    # 状态，因此源文件已删除或原登记路径变成目录时，通过原路径仍返回该记录。
    if not args.path:
        raise CliError("素材路径不能为空")
    canonical_path = os.path.realpath(args.path)

    conn = open_database(args.db)
    try:
        row = conn.execute(
            "SELECT id, path, type FROM asset WHERE path = ?",
            (canonical_path,),
        ).fetchone()
        if row is None:
            raise CliError(f"素材未登记: {canonical_path}")
        # 复用 query/export 的同一处记录组装：规范路径、当前类型与按
        # position 升序的完整标签，标签顺序保持不变。
        result = build_records(conn, [row])[0]
    except sqlite3.Error as exc:
        raise CliError(f"数据库读取失败: {exc}")
    finally:
        conn.close()

    if args.check_files:
        # 先定位登记记录再检查记录保存路径的当前状态，状态定义与 query 一致。
        # 未登记路径在此前即以“素材未登记”失败，不会走到状态检查；权限或
        # 其他系统错误由 check_file_status 抛出 CliError，唯一的 print 在
        # 检查之后，因此不会先输出半截 JSON。
        result["file_status"] = check_file_status(result["path"])

    # 标准输出仅一行 JSON 对象：默认只含 path、type、tags；传入
    # --check-files 时额外含 file_status；只读，不改动记录。
    print(json.dumps(result, ensure_ascii=False))
    return EXIT_OK


# retag 的三种标签编辑模式：默认完整替换，--append 追加，--remove 移除。
RETAG_MODE_REPLACE = "replace"
RETAG_MODE_APPEND = "append"
RETAG_MODE_REMOVE = "remove"


def resolve_retag_mode(args):
    """把互斥的 --append / --remove 开关归并为单个模式字符串。

    argparse 的 mutually exclusive group 已保证两个开关不会同时为真
    （同时出现时在参数解析阶段即以退出码 2 拒绝），这里只做归并，
    不再处理冲突。
    """
    if args.append:
        return RETAG_MODE_APPEND
    if args.remove:
        return RETAG_MODE_REMOVE
    return RETAG_MODE_REPLACE


def compute_retag_tags(mode, existing_tags, new_tags, canonical_path):
    """按模式由旧标签与规范化新标签算出最终标签列表。

    纯函数：不触碰数据库，三种模式的标签结果可脱离数据库单独核对。
    new_tags 已由 normalize_tags 去空白、按首次出现顺序去重并保留大小写。

    - replace：最终标签即 new_tags（existing_tags 不参与，调用方传 None）。
    - append：旧标签及顺序保留，已存在的标签（区分大小写）不移动，
      只把尚不存在的新标签接在末尾；重复追加同一批标签结果不变。
    - remove：按完整文本区分大小写移除，不存在的标签忽略，其余标签
      保持原顺序；若会移除全部标签则抛出 CliError，此时调用方尚未
      写入任何内容，既有记录原样保留。

    返回列表中每个标签唯一、顺序确定，可直接作为写入与输出的最终顺序。
    """
    if mode == RETAG_MODE_REPLACE:
        return list(new_tags)
    if mode == RETAG_MODE_APPEND:
        seen = set(existing_tags)
        merged = list(existing_tags)
        for tag in new_tags:
            if tag not in seen:
                seen.add(tag)
                merged.append(tag)
        return merged
    # RETAG_MODE_REMOVE：待移除标签已去重，重复条件只生效一次。
    remove_set = set(new_tags)
    remaining = [tag for tag in existing_tags if tag not in remove_set]
    if not remaining:
        # 素材至少保留一个标签：整次拒绝，不改动任何记录。
        raise CliError(f"不能移除素材的全部标签: {canonical_path}")
    return remaining


def fetch_current_tags(conn, asset_id):
    """按登记顺序（position 升序）读取素材当前的完整标签列表。"""
    return [
        tag
        for (tag,) in conn.execute(
            "SELECT tag FROM asset_tag WHERE asset_id = ? "
            "ORDER BY position ASC",
            (asset_id,),
        ).fetchall()
    ]


def rewrite_tags(conn, asset_id, tags):
    """删除素材的旧标签并按顺序写入新标签；不提交事务。

    与调用方的 commit 构成同一事务：写入中途失败时由调用方整体回滚，
    不新增素材、不改变已有记录，也不留下部分新标签。
    """
    conn.execute("DELETE FROM asset_tag WHERE asset_id = ?", (asset_id,))
    conn.executemany(
        "INSERT INTO asset_tag(asset_id, tag, position) VALUES (?, ?, ?)",
        [(asset_id, tag, position) for position, tag in enumerate(tags)],
    )


def handle_retag(args):
    # 路径按 add 的同一规则解析为规范绝对路径，等价写法定位同一记录；
    # 不要求源文件当前存在或仍是普通文件，已删除或变成目录也可改标签。
    if not args.path:
        raise CliError("素材路径不能为空")
    canonical_path = os.path.realpath(args.path)

    # 新标签去除首尾空白、按首次出现顺序去重、保留大小写；
    # 校验在打开数据库之前完成，参数错误不创建数据库。
    new_tags = normalize_tags(args.tag)
    mode = resolve_retag_mode(args)

    conn = open_database(args.db)
    try:
        row = conn.execute(
            "SELECT id, type FROM asset WHERE path = ?", (canonical_path,)
        ).fetchone()
        if row is None:
            raise CliError(f"素材未登记: {canonical_path}")
        asset_id, asset_type = row

        # 追加与移除以当前标签为输入；替换模式不读取旧标签。
        existing_tags = (
            fetch_current_tags(conn, asset_id)
            if mode != RETAG_MODE_REPLACE
            else None
        )
        tags = compute_retag_tags(mode, existing_tags, new_tags, canonical_path)

        # 删除旧标签与写入新标签在同一事务中提交：失败时整体回滚，
        # 不新增素材、不改变已有记录，也不留下部分新标签。
        rewrite_tags(conn, asset_id, tags)
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        raise CliError(f"数据库读写失败: {exc}")
    finally:
        conn.close()

    # 只改标签：路径、类型、首次登记顺序与其他素材的记录保持不变。
    print(
        json.dumps(
            {"path": canonical_path, "type": asset_type, "tags": tags},
            ensure_ascii=False,
        )
    )
    return EXIT_OK


def handle_retype(args):
    # 路径按 add 的同一规则解析为规范绝对路径，等价写法定位同一记录；
    # 不要求源文件当前存在或仍是普通文件，已删除或变成目录也可改类型。
    if not args.path:
        raise CliError("素材路径不能为空")
    canonical_path = os.path.realpath(args.path)

    # 新类型去除首尾空白、保留大小写，接受任意非空文本，不按扩展名猜测；
    # 校验在打开数据库之前完成，参数错误不创建数据库。
    asset_type = args.type.strip()
    if not asset_type:
        raise CliError("素材类型 --type 去除首尾空白后不能为空")

    conn = open_database(args.db)
    try:
        row = conn.execute(
            "SELECT id FROM asset WHERE path = ?", (canonical_path,)
        ).fetchone()
        if row is None:
            raise CliError(f"素材未登记: {canonical_path}")
        (asset_id,) = row

        # 完整标签按登记顺序读取，用于输出修改后的完整记录。
        tags = [
            tag
            for (tag,) in conn.execute(
                "SELECT tag FROM asset_tag WHERE asset_id = ? "
                "ORDER BY position ASC",
                (asset_id,),
            ).fetchall()
        ]

        # 更新类型在同一事务中提交：失败时整体回滚，不新增素材、不改变
        # 已有记录，也不留下部分修改。提交与当前类型相同的值同样成功。
        conn.execute(
            "UPDATE asset SET type = ? WHERE id = ?",
            (asset_type, asset_id),
        )
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        raise CliError(f"数据库读写失败: {exc}")
    finally:
        conn.close()

    # 只改类型：路径、完整标签及其顺序、首次登记顺序与其他素材的记录
    # 保持不变；操作不读取素材内容，也不创建、移动或改写源文件。
    print(
        json.dumps(
            {"path": canonical_path, "type": asset_type, "tags": tags},
            ensure_ascii=False,
        )
    )
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
