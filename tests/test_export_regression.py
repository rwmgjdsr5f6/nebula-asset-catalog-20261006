"""export 导出完整目录的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

覆盖：
- export 不需标签参数即返回全部登记素材，每条记录只含 path、type、tags，
  保留规范绝对路径、类型文本与完整标签顺序；
- 结果按首次登记顺序排列，每个素材只出现一次，空目录输出 []；
- 导出直接反映登记记录：源文件被删除或登记路径变为目录都不会漏掉素材，
  也不输出 file_status；
- 导出不改变登记内容（导出后 query 结果不变），不复制素材；
- 数据库文件不存在但父目录存在时创建空目录数据库并输出 []，父目录缺失不补建；
- 缺少 --db、数据库路径为空、向 export 传入不支持的参数、数据库路径指向
  目录、数据库无法打开或读取、内容损坏或表结构不兼容时：退出码 2、标准
  输出为空、标准错误说明对应原因且不含调用栈；损坏或不兼容数据库不被
  覆盖、补表或重建，读取失败时不输出部分记录。

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

NOT_SQLITE_CONTENT = b"this is not a sqlite database file\n"


class ExportRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "A.bin"
        self.file_b = self.tmp_dir / "B.bin"
        self.file_a.write_bytes(b"demo asset A\n")
        self.file_b.write_bytes(b"demo asset B\n")
        self.db_path = self.tmp_dir / "catalog.sqlite"

        self.expected_a = {
            "path": os.path.realpath(str(self.file_a)),
            "type": "image",
            "tags": ["demo", "ui"],
        }
        self.expected_b = {
            "path": os.path.realpath(str(self.file_b)),
            "type": "audio",
            "tags": ["music"],
        }

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args):
        """以全新进程运行 python -m asset_catalog，返回 CompletedProcess。"""
        cmd = [
            sys.executable,
            "-m",
            "asset_catalog",
            "--db",
            str(self.db_path),
            *args,
        ]
        return subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )

    def register_demo_assets(self):
        """按验收场景登记 A（image, demo/ui）与 B（audio, music）。"""
        result_a = self.run_cli(
            "add",
            str(self.file_a),
            "--type",
            "image",
            "--tag",
            "demo",
            "--tag",
            "ui",
        )
        self.assertEqual(result_a.returncode, 0, result_a.stderr)
        result_b = self.run_cli(
            "add", str(self.file_b), "--type", "audio", "--tag", "music"
        )
        self.assertEqual(result_b.returncode, 0, result_b.stderr)

    def assert_export_ok(self, expected):
        """导出成功：退出码 0、标准错误为空、标准输出为预期 JSON 数组。"""
        result = self.run_cli("export")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        self.assertEqual(data, expected)
        return data

    def assert_export_error(self, result):
        """导出失败：退出码 2、标准输出为空、标准错误说明原因且无调用栈。"""
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr.strip(), "")
        self.assertNotIn("Traceback", result.stderr)

    def test_export_returns_full_catalog_in_registration_order(self):
        self.register_demo_assets()

        data = self.assert_export_ok([self.expected_a, self.expected_b])
        # 每条记录只含 path、type、tags，没有 file_status 或内部编号。
        self.assertEqual(
            sorted(data[0].keys()), ["path", "tags", "type"]
        )
        self.assertEqual(sorted(data[1].keys()), ["path", "tags", "type"])
        self.assertEqual(data[0]["tags"], ["demo", "ui"])
        self.assertEqual(data[1]["tags"], ["music"])

    def test_deleted_source_kept_and_registrations_unchanged(self):
        self.register_demo_assets()
        # 移除演示文件 B，但保留其登记记录。
        self.file_b.unlink()
        self.assertFalse(self.file_b.exists())

        # B 仍在导出结果中，按 A、B 顺序输出完整元数据，无 file_status。
        data = self.assert_export_ok([self.expected_a, self.expected_b])
        self.assertNotIn("file_status", data[1])

        # 导出不改变登记内容：query --tag demo 仍只返回 A。
        query = self.run_cli("query", "--tag", "demo")
        self.assertEqual(query.returncode, 0, query.stderr)
        self.assertEqual(query.stderr, "")
        self.assertEqual(json.loads(query.stdout), [self.expected_a])

        # 源文件 A 不被改动。
        self.assertEqual(self.file_a.read_bytes(), b"demo asset A\n")

    def test_registered_path_replaced_by_directory_still_exported(self):
        self.register_demo_assets()
        self.file_b.unlink()
        self.file_b.mkdir()
        self.assertTrue(os.path.isdir(str(self.file_b)))

        # 原路径变成目录不影响已保存的登记记录。
        self.assert_export_ok([self.expected_a, self.expected_b])

    def test_empty_catalog_outputs_empty_array(self):
        # 数据库尚不存在但父目录存在：创建空目录数据库并输出 []。
        self.assertFalse(self.db_path.exists())
        self.assert_export_ok([])
        self.assertTrue(self.db_path.exists())
        # 已初始化的空库再次导出仍是 []。
        self.assert_export_ok([])

    def test_missing_parent_directory_is_not_created(self):
        missing_db = self.tmp_dir / "no_such_dir" / "catalog.sqlite"
        cmd = [
            sys.executable,
            "-m",
            "asset_catalog",
            "--db",
            str(missing_db),
            "export",
        ]
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        self.assert_export_error(result)
        self.assertFalse((self.tmp_dir / "no_such_dir").exists())

    def test_argument_and_path_errors(self):
        self.register_demo_assets()

        # 缺少 --db（全局必填）。
        result = subprocess.run(
            [sys.executable, "-m", "asset_catalog", "export"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        self.assert_export_error(result)

        # 数据库路径为空。
        result = subprocess.run(
            [sys.executable, "-m", "asset_catalog", "--db", "", "export"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        self.assert_export_error(result)

        # 向 export 传入不支持的参数。
        result = self.run_cli("export", "--tag", "demo")
        self.assert_export_error(result)
        result = self.run_cli("export", "--unexpected")
        self.assert_export_error(result)

        # 数据库路径指向目录。
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "asset_catalog",
                "--db",
                str(self.tmp_dir),
                "export",
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        self.assert_export_error(result)

        # 出错不输出部分记录，原有登记内容保持不变。
        self.assert_export_ok([self.expected_a, self.expected_b])

    def test_corrupt_or_incompatible_database_rejected_and_untouched(self):
        for label, prepare in (
            ("non_sqlite", lambda p: p.write_bytes(NOT_SQLITE_CONTENT)),
            ("foreign_table", self._prepare_foreign_table),
            ("missing_type_column", self._prepare_missing_type_column),
        ):
            db_path = self.tmp_dir / f"bad_{label}.sqlite"
            prepare(db_path)
            before = db_path.read_bytes()

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "asset_catalog",
                    "--db",
                    str(db_path),
                    "export",
                ],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
            )
            self.assert_export_error(result)
            # 损坏或不兼容数据库不被覆盖、补表或重建。
            self.assertEqual(
                db_path.read_bytes(),
                before,
                f"{label}: 失败后数据库字节内容发生变化",
            )

    def _prepare_foreign_table(self, db_path):
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE business_record ("
            "id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO business_record(id, name) VALUES (1, 'fixed')")
        conn.commit()
        conn.close()

    def _prepare_missing_type_column(self, db_path):
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE asset ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE)"
        )
        conn.execute(
            "CREATE TABLE asset_tag ("
            "asset_id INTEGER NOT NULL, tag TEXT NOT NULL, "
            "position INTEGER NOT NULL, PRIMARY KEY (asset_id, tag))"
        )
        conn.commit()
        conn.close()

    def test_assets_registered_by_earlier_version_export_without_re_register(self):
        # 手工构造一个结构与现有产品一致的数据库（模拟旧版本登记），
        # 已有数据库无需重新登记即可导出。
        conn = sqlite3.connect(str(self.db_path))
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
        conn.execute(
            "INSERT INTO asset(id, path, type) VALUES (?, ?, ?)",
            (1, str(self.expected_a["path"]), "image"),
        )
        conn.executemany(
            "INSERT INTO asset_tag(asset_id, tag, position) VALUES (?, ?, ?)",
            [(1, "demo", 0), (1, "ui", 1)],
        )
        conn.commit()
        conn.close()

        self.assert_export_ok([self.expected_a])


if __name__ == "__main__":
    unittest.main(verbosity=2)
