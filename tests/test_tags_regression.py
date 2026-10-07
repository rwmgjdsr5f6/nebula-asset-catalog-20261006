"""tags 全部已用标签统计的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile / sqlite3），无需
第三方依赖。

覆盖：
- 验收序列：A.bin（demo、ui）与 B.bin（demo、UI）登记后，tags 依次返回
  UI=1、demo=2、ui=1；每项只含 tag 与 asset_count，数量为正整数，标签不
  重复，按文本 Unicode 码点字典序升序；
- retag --remove 从 A.bin 移除 ui 后只剩 UI=1、demo=2，新进程读取仍相同；
- 完整匹配并区分大小写；同一素材同一标签只计数一次；不同登记路径即使
  文件内容相同也分别计数；标签文本原样输出不改写；
- 素材文件已删除或原登记路径变成目录仍参与统计，tags 不检查文件状态、
  不读取素材内容、不扫描目录，数据库字节与源文件保持不变；
- 空目录输出 []：数据库不存在但父目录存在时初始化空库并返回 []，
  父目录缺失时退出码 2 且不补建目录；
- 缺少 --db、数据库路径为空、tags 传入素材路径或 --tag、--type、
  --check-files 等不支持参数时：退出码 2、标准输出为空、标准错误指出
  参数与原因且不含调用栈，不创建数据库；
- 数据库路径指向目录、无法打开、非 SQLite、含其他业务表、结构不兼容或
  损坏镜像时：退出码 2、标准输出为空、标准错误说明具体原因，不输出调用栈
  或部分数组；被拒绝的数据库字节保持原样。

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


class TagsRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "A.bin"
        self.file_b = self.tmp_dir / "B.bin"
        self.file_a.write_text("same demo content\n", encoding="utf-8")
        self.file_b.write_text("same demo content\n", encoding="utf-8")
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

    def register_acceptance_samples(self):
        """登记验收样例：A(demo, ui) 与 B(demo, UI)。"""
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
        # 标准输出仅为一行 JSON 数组（行尾一个换行，没有第二行内容）。
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertTrue(result.stdout.endswith("\n"))
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

    def test_acceptance_ui_demo_ui_counts_in_codepoint_order(self):
        self.register_acceptance_samples()

        data = self.assertTagsOk(
            [
                {"tag": "UI", "asset_count": 1},
                {"tag": "demo", "asset_count": 2},
                {"tag": "ui", "asset_count": 1},
            ]
        )
        # 每项只含 tag 与 asset_count 两个字段；数量为正整数。
        for item in data:
            self.assertEqual(set(item), {"tag", "asset_count"})
            self.assertIsInstance(item["asset_count"], int)
            self.assertNotIsInstance(item["asset_count"], bool)
            self.assertGreaterEqual(item["asset_count"], 1)
        # 标签不重复。
        tags = [item["tag"] for item in data]
        self.assertEqual(len(tags), len(set(tags)))
        # 明确按 Unicode 码点字典序升序（大写 U+0055 在小写之前）。
        self.assertEqual(tags, sorted(tags, key=lambda t: t.encode("utf-32-be")))

    def test_retag_remove_takes_effect_in_next_and_fresh_process(self):
        self.register_acceptance_samples()

        # 从 A 移除 ui：ui 整体消失（只有 A 使用过它），demo 仍为 2。
        result = self.run_cli(
            "retag", str(self.file_a), "--remove", "--tag", "ui"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        expected = [
            {"tag": "UI", "asset_count": 1},
            {"tag": "demo", "asset_count": 2},
        ]
        self.assertTagsOk(expected)
        # 由全新进程再次读取同一数据库，结论一致。
        self.assertTagsOk(expected)

    def test_case_sensitive_exact_and_distinct_paths_count_separately(self):
        self.register_acceptance_samples()

        # ui 与 UI 是两个标签：完整匹配、区分大小写、标签文本不被改写。
        data = self.assertTagsOk(
            [
                {"tag": "UI", "asset_count": 1},
                {"tag": "demo", "asset_count": 2},
                {"tag": "ui", "asset_count": 1},
            ]
        )
        # A、B 文件内容相同，但不同登记路径分别计数：demo=2 而非 1。
        demo_count = {item["tag"]: item["asset_count"] for item in data}["demo"]
        self.assertEqual(demo_count, 2)

        # 同一素材重复追加同一标签不增加计数（每素材每标签只计一次）。
        result = self.run_cli(
            "retag",
            str(self.file_a),
            "--append",
            "--tag",
            "demo",
            "--tag",
            "ui",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTagsOk(
            [
                {"tag": "UI", "asset_count": 1},
                {"tag": "demo", "asset_count": 2},
                {"tag": "ui", "asset_count": 1},
            ]
        )

    def test_unicode_codepoint_ordering_and_verbatim_text(self):
        # 非 ASCII 标签：a(U+0061) < É(U+00C9) < é(U+00E9) < 中文(U+4E2D)。
        labels = [(" 中 文 ", "中 文"), ("é", "é"), ("a", "a"), ("É", "É")]
        files = []
        for index, (raw, _saved) in enumerate(labels):
            path = self.tmp_dir / f"u{index}.bin"
            path.write_text(f"u{index}\n", encoding="utf-8")
            files.append(path)
            result = self.run_cli(
                "add", str(path), "--type", "image", "--tag", raw
            )
            self.assertEqual(result.returncode, 0, result.stderr)

        data = self.assertTagsOk(
            [
                {"tag": "a", "asset_count": 1},
                {"tag": "É", "asset_count": 1},
                {"tag": "é", "asset_count": 1},
                {"tag": "中 文", "asset_count": 1},
            ]
        )
        tags = [item["tag"] for item in data]
        # 与 Python 按码点逐位比较的顺序一致；首尾空白在登记时去除，
        # 标签内部空白与大小写原样保留，tags 不再改写文本。
        self.assertEqual(tags, sorted(tags))

    def test_deleted_or_directory_source_still_counted(self):
        self.register_acceptance_samples()
        expected = [
            {"tag": "UI", "asset_count": 1},
            {"tag": "demo", "asset_count": 2},
            {"tag": "ui", "asset_count": 1},
        ]

        self.file_a.unlink()
        self.file_b.unlink()
        # 原登记路径 B 变成目录：统计仍只反映数据库元数据。
        self.file_b.mkdir()
        self.assertFalse(self.file_a.exists())
        self.assertTrue(self.file_b.is_dir())

        self.assertTagsOk(expected)

    def test_tags_is_readonly_against_database_and_sources(self):
        self.register_acceptance_samples()
        db_before = self.db_path.read_bytes()
        a_before = self.file_a.read_bytes()

        self.assertTagsOk(
            [
                {"tag": "UI", "asset_count": 1},
                {"tag": "demo", "asset_count": 2},
                {"tag": "ui", "asset_count": 1},
            ]
        )

        # 数据库字节与源文件内容都不改变，也不留下侧车文件。
        self.assertEqual(self.db_path.read_bytes(), db_before)
        self.assertEqual(self.file_a.read_bytes(), a_before)
        self.assertFalse(
            list(self.tmp_dir.glob("catalog.sqlite-*")),
            "tags 不应产生 -journal/-wal/-shm 侧车文件",
        )
        # 现有六个子命令的输出保持不变。
        result = self.run_cli("export")
        self.assertEqual(result.returncode, 0, result.stderr)
        records = json.loads(result.stdout)
        self.assertEqual([r["tags"] for r in records], [["demo", "ui"], ["demo", "UI"]])

    def test_empty_database_outputs_empty_array(self):
        # 文件不存在但父目录存在：初始化空库并返回 []。
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())
        self.assertTagsOk([], db=fresh)
        self.assertTrue(fresh.exists())
        # 新进程再次读取同一空库，结论一致。
        self.assertTagsOk([], db=fresh)

    def test_missing_parent_directory_is_not_created(self):
        missing = self.tmp_dir / "no" / "such" / "catalog.sqlite"
        self.assertTagsError(db=missing)
        self.assertFalse((self.tmp_dir / "no").exists())

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

    def test_empty_database_path_rejected(self):
        self.assertTagsError(db="")

    def test_unsupported_arguments_rejected_without_creating_database(self):
        self.register_acceptance_samples()
        # 位置参数（素材路径）与其他子命令的筛选/编辑选项都不属于 tags。
        self.assertTagsError(str(self.file_a))
        self.assertTagsError("--tag", "demo")
        self.assertTagsError("--type", "image")
        self.assertTagsError("--check-files")
        self.assertTagsError("--file-status", "present")
        self.assertTagsError("--append")
        self.assertTagsError("--remove")
        self.assertTagsError("--unknown-option")

        # 参数错误不创建数据库：逐个使用尚不存在的数据库路径复核。
        for extra in ("positional",):
            db = self.tmp_dir / f"arg_{extra}.sqlite"
            self.assertTagsError("extra-positional", db=db)
            self.assertFalse(db.exists())
        for name, flags in (
            ("tag", ["--tag", "demo"]),
            ("type", ["--type", "image"]),
            ("check", ["--check-files"]),
        ):
            db = self.tmp_dir / f"arg_{name}.sqlite"
            self.assertTagsError(*flags, db=db)
            self.assertFalse(db.exists())

    def test_database_path_is_directory_rejected(self):
        target = self.tmp_dir / "a_directory"
        target.mkdir()
        self.assertTagsError(db=target)

    def test_corrupt_and_incompatible_databases_rejected_and_untouched(self):
        self.register_acceptance_samples()

        # 非 SQLite 文本文件：拒绝且字节不变。
        non_sqlite = self.tmp_dir / "broken.sqlite"
        non_sqlite.write_text("not a sqlite database", encoding="utf-8")
        before = non_sqlite.read_bytes()
        self.assertTagsError(db=non_sqlite)
        self.assertEqual(non_sqlite.read_bytes(), before)

        # 含其他业务表的 SQLite 文件：拒绝、不补表、不重建，固定记录保留。
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
        before = bad_schema.read_bytes()
        self.assertTagsError(db=bad_schema)
        self.assertEqual(bad_schema.read_bytes(), before)

    def test_malformed_database_produces_no_partial_output(self):
        self.register_acceptance_samples()
        malformed = self.tmp_dir / "malformed.sqlite"
        malformed.write_bytes(self.db_path.read_bytes())
        size = malformed.stat().st_size
        with malformed.open("wb") as fh:
            fh.truncate(size // 2)

        # 镜像损坏：整次操作失败，不输出任何部分数组。
        self.assertTagsError(db=malformed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
