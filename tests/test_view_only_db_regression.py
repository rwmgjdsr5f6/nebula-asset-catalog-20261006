"""只含用户视图的 SQLite 文件被拒绝、空库正常初始化的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile / sqlite3），
无需第三方依赖，不访问网络。

覆盖：
- 数据库文件没有用户表、但含有一个或多个用户视图（含名为 asset 或
  asset_tag 的视图）：add / query / export / retag 均以退出码 2 结束，
  标准输出为空，标准错误说明数据库结构不属于本产品并包含数据库路径，
  不输出调用栈；失败前后数据库文件字节完全一致，原有视图定义与查询
  结果保留，不留下素材表、标签表或部分新记录；重复调用得到相同错误；
- 对照样例：已存在的父目录中尚不存在的 fresh.sqlite 经同一 export
  入口创建空目录库，退出码 0、标准错误为空、输出 []，之后能正常
  登记和读取素材；
- 已有零字节文件与没有用户业务对象的空 SQLite 库保持原来的初始化
  行为；父目录不存在仍报错且不补建目录；
- 含兼容素材表的正常目录即使附带视图或触发器也继续可用。

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

# 固定样例视图：不依赖任何表，独立返回常量。
VIEW_DEFINITION = "CREATE VIEW source_note AS SELECT 'keep' AS note"

# 样例素材的固定内容；登记前后必须保持一致。
SAMPLE_CONTENT = b"view-only db regression sample\n"


class ViewOnlyDatabaseRegressionTest(unittest.TestCase):
    def setUp(self):
        # 临时目录建在项目根目录下，与既有回归测试一致；
        # 目录、样例与数据库均由本用例自建，tearDown 时整体清理。
        self._tmp = tempfile.TemporaryDirectory(dir=PROJECT_ROOT)
        self.tmp_dir = Path(self._tmp.name)
        self.db_path = self.tmp_dir / "foreign.sqlite"
        self.sample = self.tmp_dir / "sample.bin"
        self.sample.write_bytes(SAMPLE_CONTENT)

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args, db=None):
        """以全新进程运行 python -m asset_catalog，返回 CompletedProcess。"""
        cmd = [
            sys.executable,
            "-m",
            "asset_catalog",
            "--db",
            str(self.db_path if db is None else db),
            *args,
        ]
        return subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )

    def make_view_db(self, *view_statements, db=None):
        """创建只含指定视图的 SQLite 文件并关闭连接，返回数据库路径。"""
        path = self.db_path if db is None else db
        conn = sqlite3.connect(str(path))
        for statement in view_statements:
            conn.execute(statement)
        conn.commit()
        conn.close()
        return path

    def valid_commands(self):
        """参数与素材路径均满足原有前置条件的四个目录操作。"""
        return [
            ("add", [str(self.sample), "--type", "image", "--tag", "demo"]),
            ("query", ["--tag", "demo"]),
            ("export", []),
            ("retag", [str(self.sample), "--tag", "demo"]),
        ]

    def assert_rejected(self, result, label):
        """退出码 2、标准输出为空、标准错误说明数据库结构不属于本产品
        并包含数据库路径、无调用栈。"""
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
            "数据库",
            result.stderr,
            f"{label}: 标准错误未说明数据库结构不属于本产品: "
            f"{result.stderr!r}",
        )
        self.assertIn(
            "结构不属于本产品",
            result.stderr,
            f"{label}: 标准错误未说明数据库结构不属于本产品: "
            f"{result.stderr!r}",
        )
        self.assertIn(
            str(self.db_path),
            result.stderr,
            f"{label}: 标准错误未包含数据库路径: {result.stderr!r}",
        )
        self.assertNotIn(
            "Traceback",
            result.stderr,
            f"{label}: 标准错误含调用栈: {result.stderr!r}",
        )

    def assert_all_commands_rejected_and_db_untouched(self, label):
        """四个目录操作均被拒绝；每次失败前后数据库文件字节完全一致，
        不留下素材表、标签表或部分新记录；重复调用得到相同错误。"""
        self.assertTrue(
            self.db_path.exists(), f"{label}: 前置数据库文件不存在"
        )
        db_bytes_before = self.db_path.read_bytes()

        for command, args in self.valid_commands():
            result = self.run_cli(command, *args)
            self.assert_rejected(result, f"{label}: {command}")
            self.assertEqual(
                self.db_path.read_bytes(),
                db_bytes_before,
                f"{label}: {command} 失败后数据库文件的字节内容发生变化",
            )
            # 重复调用同一命令：相同拒绝结果，原内容继续保留。
            result_again = self.run_cli(command, *args)
            self.assert_rejected(result_again, f"{label}: {command}（重复）")
            self.assertEqual(
                result_again.stderr,
                result.stderr,
                f"{label}: {command} 重复调用的拒绝结果与首次不一致: "
                f"首次 stderr={result.stderr!r} "
                f"重复 stderr={result_again.stderr!r}",
            )
            self.assertEqual(
                self.db_path.read_bytes(),
                db_bytes_before,
                f"{label}: {command} 重复调用后数据库文件的字节内容发生变化",
            )

        # 不留下素材表、标签表或部分新记录：数据库中仍没有任何用户表。
        conn = sqlite3.connect(str(self.db_path))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        conn.close()
        self.assertEqual(
            tables,
            [],
            f"{label}: 拒绝后数据库中出现用户表: {tables!r}",
        )

    def test_rejects_view_only_database_all_commands(self):
        """唯一业务对象为 source_note 视图的文件：四个目录操作均拒绝。"""
        self.make_view_db(VIEW_DEFINITION)
        self.assert_all_commands_rejected_and_db_untouched("只含一个视图的 SQLite 文件")

        # 原有视图定义与查询结果保留。
        conn = sqlite3.connect(str(self.db_path))
        rows = conn.execute("SELECT note FROM source_note").fetchall()
        definition = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'view' AND name = 'source_note'"
        ).fetchone()
        conn.close()
        self.assertEqual(
            rows,
            [("keep",)],
            "只含一个视图的 SQLite 文件: 原有视图查询结果无法重新读出",
        )
        self.assertIsNotNone(definition, "原有视图定义丢失")
        self.assertIn("source_note", definition[0])

    def test_rejects_database_with_multiple_views(self):
        """视图数量和名称不改变结果：多个视图同样被拒绝。"""
        self.make_view_db(
            VIEW_DEFINITION,
            "CREATE VIEW extra_note AS SELECT 'extra' AS note",
            "CREATE VIEW third_note AS SELECT 1 AS n, 'x' AS s",
        )
        self.assert_all_commands_rejected_and_db_untouched("含多个视图的 SQLite 文件")

        conn = sqlite3.connect(str(self.db_path))
        rows = conn.execute("SELECT note FROM source_note").fetchall()
        conn.close()
        self.assertEqual(rows, [("keep",)])

    def test_rejects_views_named_like_product_tables(self):
        """名为 asset 或 asset_tag 的视图也按同一规则拒绝。"""
        for view_name in ("asset", "asset_tag"):
            with self.subTest(view_name=view_name):
                db = self.tmp_dir / f"foreign_{view_name}.sqlite"
                self.db_path = db
                self.make_view_db(
                    f"CREATE VIEW {view_name} AS SELECT 'keep' AS note"
                )
                self.assert_all_commands_rejected_and_db_untouched(
                    f"视图名为 {view_name} 的 SQLite 文件"
                )

    def test_fresh_database_export_creates_empty_catalog(self):
        """对照样例：父目录存在但 fresh.sqlite 不存在时，export 创建空
        目录库，退出码 0、标准错误为空、输出 []，之后能正常登记和读取。"""
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())

        result = self.run_cli("export", db=fresh)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [])
        self.assertTrue(fresh.exists())

        # 之后能正常登记素材。
        canonical = os.path.realpath(str(self.sample))
        result_add = self.run_cli(
            "add", str(self.sample), "--type", "image", "--tag", "demo",
            db=fresh,
        )
        self.assertEqual(result_add.returncode, 0, result_add.stderr)
        self.assertEqual(result_add.stderr, "")
        self.assertEqual(
            json.loads(result_add.stdout),
            {"path": canonical, "type": "image", "tags": ["demo"]},
        )

        # 并能正常读取：export 与 query 均返回已登记素材。
        result_export = self.run_cli("export", db=fresh)
        self.assertEqual(result_export.returncode, 0, result_export.stderr)
        self.assertEqual(
            json.loads(result_export.stdout),
            [{"path": canonical, "type": "image", "tags": ["demo"]}],
        )
        result_query = self.run_cli("query", "--tag", "demo", db=fresh)
        self.assertEqual(result_query.returncode, 0, result_query.stderr)
        self.assertEqual(
            json.loads(result_query.stdout),
            [{"path": canonical, "type": "image", "tags": ["demo"]}],
        )

    def test_zero_byte_file_still_initialized(self):
        """已有零字节文件保持原来的初始化行为：export 输出 []。"""
        zero = self.tmp_dir / "zero.sqlite"
        zero.write_bytes(b"")

        result = self.run_cli("export", db=zero)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [])

    def test_empty_sqlite_database_still_initialized(self):
        """没有用户业务对象的空 SQLite 库保持原来的初始化行为。"""
        empty = self.tmp_dir / "empty.sqlite"
        conn = sqlite3.connect(str(empty))
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()

        result = self.run_cli("export", db=empty)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [])

    def test_missing_parent_directory_still_rejected(self):
        """父目录不存在仍报错且不补建目录。"""
        missing = self.tmp_dir / "no" / "such" / "catalog.sqlite"
        result = self.run_cli("export", db=missing)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse((self.tmp_dir / "no").exists())

    def test_valid_catalog_with_extra_view_and_trigger_still_works(self):
        """含兼容素材表的正常目录即使附带视图或触发器也继续可用。"""
        canonical = os.path.realpath(str(self.sample))
        result_add = self.run_cli(
            "add", str(self.sample), "--type", "image", "--tag", "demo"
        )
        self.assertEqual(result_add.returncode, 0, result_add.stderr)

        # 在已初始化的正常目录上附加一个视图与一个触发器。
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("CREATE VIEW asset_note AS SELECT 'keep' AS note")
        conn.execute(
            "CREATE TRIGGER asset_tag_touch AFTER INSERT ON asset_tag "
            "BEGIN SELECT 1; END"
        )
        conn.commit()
        conn.close()

        expected = [{"path": canonical, "type": "image", "tags": ["demo"]}]

        result_export = self.run_cli("export")
        self.assertEqual(result_export.returncode, 0, result_export.stderr)
        self.assertEqual(result_export.stderr, "")
        self.assertEqual(json.loads(result_export.stdout), expected)

        result_query = self.run_cli("query", "--tag", "demo")
        self.assertEqual(result_query.returncode, 0, result_query.stderr)
        self.assertEqual(json.loads(result_query.stdout), expected)

        result_retag = self.run_cli(
            "retag", str(self.sample), "--tag", "ui"
        )
        self.assertEqual(result_retag.returncode, 0, result_retag.stderr)
        self.assertEqual(
            json.loads(result_retag.stdout),
            {"path": canonical, "type": "image", "tags": ["ui"]},
        )

        # 附加的视图与触发器保持原样。
        conn = sqlite3.connect(str(self.db_path))
        rows = conn.execute("SELECT note FROM asset_note").fetchall()
        trigger = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' "
            "AND name = 'asset_tag_touch'"
        ).fetchone()
        conn.close()
        self.assertEqual(rows, [("keep",)])
        self.assertIsNotNone(trigger, "附加的触发器丢失")


if __name__ == "__main__":
    unittest.main(verbosity=2)
