"""仅含用户视图的 SQLite 文件必须被拒绝、空库仍正常初始化的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile / sqlite3），
无需第三方依赖，不访问网络。

背景：视图可以独立返回常量而不依赖任何表（例如
``CREATE VIEW source_note AS SELECT 'keep' AS note``），因此 SQLite 文件
中没有用户表并不代表它没有已有业务内容。旧实现只清点用户表，会把这种
文件误判为空库并在其中补建素材表与标签表；本测试固定修复后的约定。

覆盖：
- 固定样例 foreign.sqlite 的唯一业务对象是视图
  ``source_note AS SELECT 'keep' AS note``，样例准备完成后关闭连接：
  add / query / export / retag 四个目录操作在命令参数与素材路径满足
  原有前置条件时，均以退出码 2 结束、标准输出为空，标准错误说明该
  数据库结构不属于本产品并包含指定数据库路径，且不输出调用栈；
- 视图数量和名称不改变结果：含多个视图、视图名为 asset 或 asset_tag
  时同样拒绝；
- 每次失败前后数据库文件字节完全一致，不留下素材表、标签表、
  部分新记录或日志/侧车文件；原视图定义与查询结果（'keep'）保留；
  再次调用仍得到相同错误；
- 参数不合法（query 缺 --tag、export 传不支持的参数）或 add 的源路径
  不存在时，继续遵循既有参数校验规则，且不改动视图库；
- 对照：在已存在的父目录中对尚不存在的 fresh.sqlite 使用同一 export
  入口，仍创建空目录库，退出码 0、标准错误为空、输出 []，之后能正常
  登记并由新进程读取素材；
- 零字节文件与没有用户业务对象的空 SQLite 库（sqlite_master 完全为空，
  或仅含内部表 sqlite_sequence）保持原来的初始化行为；
- 父目录不存在仍报错且不补建目录；
- 含兼容素材表的正常目录即使附带用户视图与触发器，也继续可用。

每个用例使用独立的临时目录与数据库，只创建/清理自己的样例文件。
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 固定样例素材内容；用于 add 前置条件与空库登记对照。
SAMPLE_CONTENT = b"view-database rejection regression sample\n"


def create_foreign_view_database(db_path, statements=None):
    """创建只含用户视图（默认 source_note 返回常量 'keep'）的 SQLite 文件。

    建样例时即提交并关闭连接，与验收约定一致。statements 可覆盖视图定义。
    """
    conn = sqlite3.connect(str(db_path))
    try:
        if statements is None:
            statements = [
                "CREATE VIEW source_note AS SELECT 'keep' AS note",
            ]
        for statement in statements:
            conn.execute(statement)
        conn.commit()
    finally:
        conn.close()


class ViewDatabaseRejectionRegressionTest(unittest.TestCase):
    def setUp(self):
        # 临时目录建在项目根目录下，与既有拒绝类回归测试一致。
        self._tmp = tempfile.TemporaryDirectory(dir=PROJECT_ROOT)
        self.tmp_dir = Path(self._tmp.name)
        # 固定样例名 foreign.sqlite。
        self.db_path = self.tmp_dir / "foreign.sqlite"
        create_foreign_view_database(self.db_path)

        # add 前置条件：存在的普通文件、非空类型与有效标签，
        # 且素材路径与数据库路径不同。
        self.sample = self.tmp_dir / "sample.bin"
        self.sample.write_bytes(SAMPLE_CONTENT)

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args, db=None):
        """以全新进程运行 python -m asset_catalog，返回 CompletedProcess。"""
        target = str(self.db_path if db is None else db)
        cmd = [
            sys.executable,
            "-m",
            "asset_catalog",
            "--db",
            target,
            *args,
        ]
        return subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )

    def assert_command_rejected(self, label, cli_args, db=None):
        """目录操作在视图库上确定失败：退出码 2、标准输出为空、标准错误
        说明结构不属于本产品并包含指定数据库路径、无调用栈；数据库字节
        与视图内容完全保留，不产生日志或侧车文件。"""
        target = self.db_path if db is None else Path(db)
        db_bytes_before = target.read_bytes()

        result = self.run_cli(*cli_args, db=db)
        self.assertEqual(
            result.returncode,
            2,
            f"{label}: 期望退出码 2，实际 {result.returncode}，"
            f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )
        self.assertEqual(
            result.stdout,
            "",
            f"{label}: 期望标准输出为空，实际 {result.stdout!r}",
        )
        self.assertIn(
            "结构不属于本产品",
            result.stderr,
            f"{label}: 标准错误未说明结构不属于本产品: {result.stderr!r}",
        )
        self.assertIn(
            str(target),
            result.stderr,
            f"{label}: 标准错误未包含指定数据库路径 {target}: "
            f"{result.stderr!r}",
        )
        self.assertNotIn(
            "Traceback",
            result.stderr,
            f"{label}: 标准错误含调用栈: {result.stderr!r}",
        )

        # 文件字节完全一致，且不留下日志/WAL 等侧车文件。
        self.assertTrue(target.exists(), f"{label}: 数据库文件被删除")
        self.assertEqual(
            target.read_bytes(),
            db_bytes_before,
            f"{label}: 失败后数据库文件的字节内容发生变化",
        )
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = target.with_name(target.name + suffix)
            self.assertFalse(
                sidecar.exists(),
                f"{label}: 失败后留下侧车文件 {sidecar.name}",
            )

        # 没有被补建素材表或标签表（视图可以占用 asset/asset_tag 之名，
        # 但不能出现同名的用户表），视图仍是该文件唯一的业务对象。
        conn = sqlite3.connect(str(target))
        try:
            objects = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name IN ('asset', 'asset_tag')"
            ).fetchall()
            self.assertEqual(
                objects,
                [],
                f"{label}: 失败后库中出现素材表或标签表: {objects!r}",
            )
            # 原视图定义保留，查询结果仍为常量 keep。
            self.assertEqual(
                conn.execute("SELECT note FROM source_note").fetchall(),
                [("keep",)],
                f"{label}: 原视图查询结果不再是 keep",
            )
        finally:
            conn.close()
        return result

    def test_export_rejects_view_only_database(self):
        """验收命令：export 对只含 source_note 视图的 foreign.sqlite 确定失败。"""
        self.assert_command_rejected("export 视图库", ["export"])

    def test_query_rejects_view_only_database(self):
        """有效 query（标签等前置条件均满足）同样拒绝视图库。"""
        self.assert_command_rejected("query 视图库", ["query", "--tag", "demo"])

    def test_add_rejects_view_only_database(self):
        """有效 add（源文件存在、类型与标签合法、路径不与数据库相同）拒绝。"""
        self.assert_command_rejected(
            "add 视图库",
            [
                "add",
                str(self.sample),
                "--type",
                "image",
                "--tag",
                "demo",
                "--tag",
                "ui",
            ],
        )
        # 被拒绝的登记既没有素材记录，也没有部分标签可查。
        self.assertEqual(self.sample.read_bytes(), SAMPLE_CONTENT)

    def test_retag_rejects_view_only_database(self):
        """有效 retag（路径与标签参数合法）同样拒绝视图库。"""
        self.assert_command_rejected(
            "retag 视图库",
            ["retag", str(self.sample), "--tag", "replacement"],
        )

    def test_repeated_calls_fail_identically_and_keep_bytes(self):
        """再次调用仍得到相同错误，数据库字节始终一致。"""
        db_bytes_before = self.db_path.read_bytes()
        first = self.assert_command_rejected("首次 export", ["export"])
        second = self.run_cli("export")
        self.assertEqual(second.returncode, 2)
        self.assertEqual(second.stdout, "")
        self.assertEqual(
            second.stderr,
            first.stderr,
            f"重复调用错误信息不一致: 首次={first.stderr!r} "
            f"重复={second.stderr!r}",
        )
        self.assertEqual(self.db_path.read_bytes(), db_bytes_before)

    def test_view_count_and_names_do_not_change_result(self):
        """视图数量和名称不改变结果：多个视图、视图名为 asset 或 asset_tag，
        都按同一规则拒绝；全部视图定义与查询结果保留。"""
        db_path = self.tmp_dir / "foreign_named.sqlite"
        create_foreign_view_database(
            db_path,
            statements=[
                "CREATE VIEW source_note AS SELECT 'keep' AS note",
                # 视图占用素材表/标签表的名字也不改变判定：它们不是用户表。
                "CREATE VIEW asset AS SELECT 'a' AS x",
                "CREATE VIEW asset_tag AS SELECT 'b' AS y",
                "CREATE VIEW third_view AS SELECT 3 AS z",
            ],
        )
        self.assert_command_rejected(
            "含多个视图且视图名为 asset/asset_tag", ["export"], db=db_path
        )

        conn = sqlite3.connect(str(db_path))
        try:
            self.assertEqual(
                conn.execute("SELECT note FROM source_note").fetchall(),
                [("keep",)],
            )
            self.assertEqual(
                conn.execute("SELECT x FROM asset").fetchall(),
                [("a",)],
            )
            self.assertEqual(
                conn.execute("SELECT y FROM asset_tag").fetchall(),
                [("b",)],
            )
            self.assertEqual(
                conn.execute("SELECT z FROM third_view").fetchall(),
                [(3,)],
            )
            view_names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'view'"
                ).fetchall()
            }
            self.assertEqual(
                view_names, {"source_note", "asset", "asset_tag", "third_view"}
            )
        finally:
            conn.close()

    def test_invalid_arguments_still_follow_existing_validation(self):
        """参数不合法或 add 源路径不合要求时继续走既有校验规则，
        不打开/初始化视图库，文件字节不变。"""
        db_bytes_before = self.db_path.read_bytes()

        # query 缺少必填的 --tag：参数错误，退出码 2，无调用栈。
        result = self.run_cli("query")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

        # export 传入不支持的参数：参数错误。
        result = self.run_cli("export", "extra-positional")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

        # add 的源路径不存在：沿用既有源路径校验，错误先于数据库打开。
        missing_source = self.tmp_dir / "missing_sample.bin"
        result = self.run_cli(
            "add",
            str(missing_source),
            "--type",
            "image",
            "--tag",
            "demo",
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("登记路径不存在", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

        # 视图库未被任何一次参数错误初始化或修改。
        self.assertEqual(self.db_path.read_bytes(), db_bytes_before)
        conn = sqlite3.connect(str(self.db_path))
        try:
            self.assertEqual(
                conn.execute("SELECT note FROM source_note").fetchall(),
                [("keep",)],
            )
        finally:
            conn.close()


class EmptyDatabaseInitializationRegressionTest(unittest.TestCase):
    """空库初始化与正常库附带视图/触发器的对照测试。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(dir=PROJECT_ROOT)
        self.tmp_dir = Path(self._tmp.name)
        self.sample = self.tmp_dir / "sample.bin"
        self.sample.write_bytes(SAMPLE_CONTENT)
        self.canonical_sample = os.path.realpath(str(self.sample))

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args, db):
        cmd = [
            sys.executable,
            "-m",
            "asset_catalog",
            "--db",
            str(db),
            *args,
        ]
        return subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )

    def assert_initialized_empty_catalog(self, db_path):
        """export 退出码 0、标准错误为空、输出 []，且库已含素材表。"""
        result = self.run_cli("export", db=db_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.strip(), "[]")

        conn = sqlite3.connect(str(db_path))
        try:
            table_names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            self.assertIn("asset", table_names)
            self.assertIn("asset_tag", table_names)
        finally:
            conn.close()

    def test_fresh_sqlite_export_initializes_then_register_and_read(self):
        """对照：尚不存在的 fresh.sqlite 由 export 创建空目录库并输出 []，
        之后能正常登记素材，并由新进程读取。"""
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())

        result = self.run_cli("export", db=fresh)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.strip(), "[]")
        self.assertTrue(fresh.exists())

        # 新进程再次读取同一空库，结论一致。
        result = self.run_cli("export", db=fresh)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "[]")

        # 空库初始化后能正常登记素材。
        result = self.run_cli(
            "add",
            str(self.sample),
            "--type",
            "image",
            "--tag",
            "demo",
            db=fresh,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        expected = {
            "path": self.canonical_sample,
            "type": "image",
            "tags": ["demo"],
        }
        self.assertEqual(json.loads(result.stdout), expected)

        # 由新启动的 export 与 query 进程读回同一条登记记录。
        result = self.run_cli("export", db=fresh)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [expected])
        result = self.run_cli("query", "--tag", "demo", db=fresh)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [expected])

    def test_zero_byte_file_is_initialized(self):
        """已有零字节文件保持原来的初始化行为。"""
        zero = self.tmp_dir / "zero.sqlite"
        zero.write_bytes(b"")
        self.assertEqual(zero.stat().st_size, 0)
        self.assert_initialized_empty_catalog(zero)

    def test_empty_sqlite_without_user_objects_is_initialized(self):
        """没有用户业务对象的空 SQLite 库保持初始化行为：
        sqlite_master 完全为空、或仅含内部表 sqlite_sequence。"""
        # 形态一：有效的 SQLite 文件头，sqlite_master 中没有任何对象。
        fully_empty = self.tmp_dir / "fully_empty.sqlite"
        conn = sqlite3.connect(str(fully_empty))
        try:
            # VACUUM 强制写出有效的数据库页，而不定义任何对象。
            conn.execute("VACUUM")
            conn.commit()
        finally:
            conn.close()
        self.assert_initialized_empty_catalog(fully_empty)

        # 形态二：只含内部表 sqlite_sequence（曾建过 AUTOINCREMENT 表并删除），
        # 内部表不算用户业务对象。
        internal_only = self.tmp_dir / "internal_only.sqlite"
        conn = sqlite3.connect(str(internal_only))
        try:
            conn.execute(
                "CREATE TABLE temp_auto (id INTEGER PRIMARY KEY AUTOINCREMENT)"
            )
            conn.execute("INSERT INTO temp_auto DEFAULT VALUES")
            conn.execute("DROP TABLE temp_auto")
            conn.commit()
            leftovers = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        finally:
            conn.close()
        # 前置确认：文件中确实只剩内部表，没有任何用户业务对象。
        self.assertEqual(leftovers, [("sqlite_sequence",)])
        self.assert_initialized_empty_catalog(internal_only)

    def test_missing_parent_directory_is_not_created(self):
        """父目录不存在时仍报错且不补建目录。"""
        missing = self.tmp_dir / "no" / "such" / "fresh.sqlite"
        result = self.run_cli("export", db=missing)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse((self.tmp_dir / "no").exists())

    def test_normal_catalog_with_view_and_trigger_remains_usable(self):
        """含兼容素材表的正常目录即使附带用户视图与触发器，也继续可用：
        登记、查询、导出、改标签行为均不变。"""
        db_path = self.tmp_dir / "catalog_with_view.sqlite"

        # 先用产品入口正常登记一条素材，建立兼容库。
        result = self.run_cli(
            "add",
            str(self.sample),
            "--type",
            "image",
            "--tag",
            "demo",
            "--tag",
            "ui",
            db=db_path,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")

        # 直接在库上附带一个用户视图与一个触发器（不改变既有记录）。
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("CREATE VIEW catalog_extra AS SELECT 1 AS v")
            conn.execute(
                "CREATE TRIGGER trg_asset_tag_noop AFTER INSERT ON asset_tag "
                "BEGIN "
                "UPDATE asset SET type = type WHERE 0; "
                "END"
            )
            conn.commit()
        finally:
            conn.close()

        expected = {
            "path": self.canonical_sample,
            "type": "image",
            "tags": ["demo", "ui"],
        }

        # 附带视图与触发器后 export / query 正常。
        result = self.run_cli("export", db=db_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [expected])

        result = self.run_cli("query", "--tag", "ui", db=db_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [expected])

        # 附带的用户视图仍可独立查询。
        conn = sqlite3.connect(str(db_path))
        try:
            self.assertEqual(
                conn.execute("SELECT v FROM catalog_extra").fetchall(),
                [(1,)],
            )
        finally:
            conn.close()

        # retag 完整替换标签，触发器不影响正常写入。
        result = self.run_cli(
            "retag", str(self.sample), "--tag", "project", db=db_path
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        expected["tags"] = ["project"]
        self.assertEqual(json.loads(result.stdout), expected)

        result = self.run_cli("query", "--tag", "project", db=db_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [expected])
        result = self.run_cli("query", "--tag", "demo", db=db_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

        # 源文件在全部操作后保持只读不变。
        self.assertEqual(self.sample.read_bytes(), SAMPLE_CONTENT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
