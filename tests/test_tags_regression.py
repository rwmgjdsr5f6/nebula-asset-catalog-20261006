"""tags 已用标签统计的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

覆盖：
- 每项仅含 tag 与 asset_count，标签不重复，按 Unicode 码点字典序升序，
  数量为正整数；空目录输出 []；
- 统计区分大小写、完整匹配、不改写标签文本；同一素材的同一标签只计一次，
  不同登记路径分别计数；没有素材使用的标签不输出零计数项；
- retag 编辑后的标签在下次统计中生效，新进程读取结论一致；
- 源文件删除或原路径变成目录后素材仍参与统计；tags 不检查文件状态、
  不读取素材内容、不扫描目录，也不改动数据库或素材文件；
- 数据库文件不存在但父目录存在时创建空目录数据库并输出 []，
  父目录缺失时不补建目录；
- 缺少 --db、数据库路径为空、tags 传入素材路径或 --tag、
  --check-files 等不支持的参数、数据库路径指向目录、数据库无法打开或
  内容损坏、表结构不兼容时：退出码 2、标准输出为空、标准错误说明原因
  且不含调用栈；被拒绝的损坏或不兼容数据库字节保持不变；
- 读取失败时不输出部分数组。

每个用例使用独立的临时目录与数据库，只创建/清理自己的样例文件。
"""

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TagsRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "A.bin"
        self.file_b = self.tmp_dir / "B.bin"
        self.file_a.write_text("demo asset A\n", encoding="utf-8")
        self.file_b.write_text("demo asset B\n", encoding="utf-8")
        self.db_path = self.tmp_dir / "catalog.sqlite"

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

    def register_samples(self):
        """按验收顺序登记 A（image: demo, ui）与 B（image: demo, UI）。"""
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
        self.assertEqual(result_a.stderr, "")

        result_b = self.run_cli(
            "add",
            str(self.file_b),
            "--type",
            "image",
            "--tag",
            "demo",
            "--tag",
            "UI",
        )
        self.assertEqual(result_b.returncode, 0, result_b.stderr)
        self.assertEqual(result_b.stderr, "")

    def assertTagsOk(self, expected, db=None):
        result = self.run_cli("tags", db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        # 标准输出仅为一行 JSON 数组。
        self.assertEqual(result.stdout.count("\n"), 1)
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        self.assertEqual(data, expected)
        return data

    def assertTagsError(self, *args, db=None):
        result = self.run_cli("tags", *args, db=db)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr.strip(), "")
        self.assertNotIn("Traceback", result.stderr)
        return result.stderr

    def test_tags_counts_case_sensitive_and_sorted(self):
        self.register_samples()

        data = self.assertTagsOk(
            [
                {"tag": "UI", "asset_count": 1},
                {"tag": "demo", "asset_count": 2},
                {"tag": "ui", "asset_count": 1},
            ]
        )
        # 每项仅含 tag 与 asset_count；标签不重复；数量为正整数；
        # 按标签文本的 Unicode 码点字典序升序（大写先于小写）。
        self.assertEqual(
            [set(item) for item in data],
            [{"tag", "asset_count"}] * 3,
        )
        tags = [item["tag"] for item in data]
        self.assertEqual(len(tags), len(set(tags)))
        self.assertEqual(tags, sorted(tags))
        for item in data:
            self.assertIsInstance(item["asset_count"], int)
            self.assertGreater(item["asset_count"], 0)

    def test_same_tag_same_asset_counted_once(self):
        # add 时重复传入同一标签只登记一次，统计中同一素材只计一次。
        result = self.run_cli(
            "add",
            str(self.file_a),
            "--type",
            "image",
            "--tag",
            "demo",
            "--tag",
            "demo",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTagsOk([{"tag": "demo", "asset_count": 1}])

    def test_same_content_different_paths_counted_separately(self):
        # 不同登记路径即使文件内容相同仍分别计数。
        file_c = self.tmp_dir / "C.bin"
        file_c.write_text("demo asset A\n", encoding="utf-8")
        for path in (self.file_a, file_c):
            result = self.run_cli(
                "add", str(path), "--type", "image", "--tag", "demo"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTagsOk([{"tag": "demo", "asset_count": 2}])

    def test_retag_changes_reflected_in_next_run(self):
        self.register_samples()

        # 从 A.bin 移除 ui 后，统计只剩 UI 数量 1 与 demo 数量 2。
        result = self.run_cli(
            "retag",
            str(self.file_a),
            "--remove",
            "--tag",
            "ui",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        expected = [
            {"tag": "UI", "asset_count": 1},
            {"tag": "demo", "asset_count": 2},
        ]
        self.assertTagsOk(expected)
        # 新进程再次读取同一数据库，结论一致。
        self.assertTagsOk(expected)

    def test_deleted_or_replaced_source_still_counted(self):
        self.register_samples()
        # 源文件删除后仍参与统计。
        self.file_b.unlink()
        self.assertFalse(self.file_b.exists())
        # 原路径变成目录后仍参与统计。
        self.file_a.unlink()
        self.file_a.mkdir()

        self.assertTagsOk(
            [
                {"tag": "UI", "asset_count": 1},
                {"tag": "demo", "asset_count": 2},
                {"tag": "ui", "asset_count": 1},
            ]
        )

    def test_tags_does_not_change_database_or_files(self):
        self.register_samples()
        db_before = self.db_path.read_bytes()
        a_before = self.file_a.read_bytes()

        self.assertTagsOk(
            [
                {"tag": "UI", "asset_count": 1},
                {"tag": "demo", "asset_count": 2},
                {"tag": "ui", "asset_count": 1},
            ]
        )

        # 数据库内容与素材文件均不变。
        self.assertEqual(self.db_path.read_bytes(), db_before)
        self.assertEqual(self.file_a.read_bytes(), a_before)

    def test_empty_database_outputs_empty_array(self):
        # 尚未初始化：文件不存在但父目录存在，创建空目录数据库并输出 []。
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())
        self.assertTagsOk([], db=fresh)
        self.assertTrue(fresh.exists())
        # 由新进程再次读取同一空库，结论一致。
        self.assertTagsOk([], db=fresh)

    def test_missing_parent_directory_is_not_created(self):
        missing = self.tmp_dir / "no" / "such" / "catalog.sqlite"
        self.assertTagsError(db=missing)
        self.assertFalse((self.tmp_dir / "no").exists())

    def test_unsupported_tags_arguments_rejected(self):
        self.register_samples()
        db_before = self.db_path.read_bytes()
        self.assertTagsError(str(self.file_a))
        self.assertTagsError("--tag", "demo")
        self.assertTagsError("--check-files")
        # 参数错误不改动既有数据库。
        self.assertEqual(self.db_path.read_bytes(), db_before)

    def test_unsupported_arguments_do_not_create_database(self):
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertTagsError("--tag", "demo", db=fresh)
        self.assertFalse(fresh.exists())

    def test_database_path_is_directory_rejected(self):
        target = self.tmp_dir / "a_directory"
        target.mkdir()
        self.assertTagsError(db=target)

    def test_empty_database_path_rejected(self):
        # --db 显式传空字符串：退出码 2、标准输出为空。
        result = self.run_cli("tags", db="")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_db_option_rejected(self):
        cmd = [sys.executable, "-m", "asset_catalog", "tags"]
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

    def test_corrupt_and_incompatible_databases_rejected_and_untouched(self):
        self.register_samples()

        # 非 SQLite 文本文件：拒绝且字节不变。
        non_sqlite = self.tmp_dir / "broken.sqlite"
        non_sqlite.write_text("not a sqlite database", encoding="utf-8")
        before = non_sqlite.read_bytes()
        self.assertTagsError(db=non_sqlite)
        self.assertEqual(non_sqlite.read_bytes(), before)

        # 含其他业务表的 SQLite 文件：拒绝、不补表、不重建，原有记录保留。
        foreign = self.tmp_dir / "foreign.sqlite"
        conn = sqlite3.connect(str(foreign))
        conn.execute("CREATE TABLE business_record(id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO business_record(note) VALUES ('fixed record')")
        conn.commit()
        conn.close()
        before = foreign.read_bytes()
        self.assertTagsError(db=foreign)
        self.assertEqual(foreign.read_bytes(), before)
        conn = sqlite3.connect(str(foreign))
        self.assertEqual(
            conn.execute("SELECT note FROM business_record").fetchall(),
            [("fixed record",)],
        )
        conn.close()

        # asset 缺少 type 列的不兼容结构：同样拒绝。
        bad_schema = self.tmp_dir / "bad_schema.sqlite"
        conn = sqlite3.connect(str(bad_schema))
        conn.execute(
            "CREATE TABLE asset(id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "path TEXT NOT NULL UNIQUE)"
        )
        conn.execute(
            "CREATE TABLE asset_tag(asset_id INTEGER NOT NULL, tag TEXT NOT NULL, "
            "position INTEGER NOT NULL, PRIMARY KEY(asset_id, tag))"
        )
        conn.commit()
        conn.close()
        self.assertTagsError(db=bad_schema)

    def test_malformed_database_produces_no_partial_output(self):
        self.register_samples()
        malformed = self.tmp_dir / "malformed.sqlite"
        malformed.write_bytes(self.db_path.read_bytes())
        size = malformed.stat().st_size
        with malformed.open("wb") as fh:
            fh.truncate(size // 2)

        # 镜像损坏：整次操作失败，不输出任何部分数组。
        self.assertTagsError(db=malformed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
