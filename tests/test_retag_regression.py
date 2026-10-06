"""retag 标签替换的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

覆盖：
- 成功替换：退出码 0、标准错误为空、标准输出仅为含 path、type、tags 的
  JSON 对象；新标签去除首尾空白、按首次出现顺序去重、保留大小写；
- 路径按 add 的规范绝对路径规则定位同一记录（相对路径、.、.. 等价写法）；
- 重复提交相同的规范化标签与顺序仍成功；
- 源文件已删除或变成目录仍可改标签，源文件内容保持只读；
- 结果持久保存：新进程的 query 与 export 反映新标签，被移除标签不再使
  该素材入选；all/any、类型筛选、文件状态检查与排序规则不变；
- 素材路径、类型、首次登记顺序与其他素材的记录保持不变；
- 缺少路径或标签、路径为空、任一标签为空或只有空白、传入不支持参数、
  目标未登记（错误含规范路径）、数据库无法打开或读写、损坏或结构不兼容
  时：退出码 2、标准输出为空、标准错误指出原因且不含调用栈；
- 参数错误不创建数据库；失败不新增素材、不改变已有记录、不留部分新标签；
- 数据库文件不存在但父目录存在时创建空库后报告目标未登记；
  父目录缺失时报错且不补建目录。

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


class RetagRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "A.bin"
        self.file_b = self.tmp_dir / "B.bin"
        self.file_a.write_text("demo asset A\n", encoding="utf-8")
        self.file_b.write_text("demo asset B\n", encoding="utf-8")
        self.db_path = self.tmp_dir / "catalog.sqlite"

        self.path_a = os.path.realpath(str(self.file_a))
        self.path_b = os.path.realpath(str(self.file_b))
        self.expected_a = {"path": self.path_a, "type": "image", "tags": ["demo", "ui"]}
        self.expected_b = {"path": self.path_b, "type": "audio", "tags": ["demo"]}

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
        """按验收顺序登记 A（image: demo, ui）与 B（audio: demo）。"""
        result_a = self.run_cli(
            "add", str(self.file_a), "--type", "image", "--tag", "demo", "--tag", "ui"
        )
        self.assertEqual(result_a.returncode, 0, result_a.stderr)
        self.assertEqual(result_a.stderr, "")

        result_b = self.run_cli(
            "add", str(self.file_b), "--type", "audio", "--tag", "demo"
        )
        self.assertEqual(result_b.returncode, 0, result_b.stderr)
        self.assertEqual(result_b.stderr, "")

    def run_retag(self, *args, db=None):
        return self.run_cli("retag", *args, db=db)

    def assertRetagOk(self, expected, *args, db=None):
        result = self.run_retag(*args, db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertEqual(set(data), {"path", "type", "tags"})
        self.assertEqual(data, expected)
        return data

    def assertRetagError(self, *args, db=None):
        result = self.run_retag(*args, db=db)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr.strip(), "")
        self.assertNotIn("Traceback", result.stderr)
        return result.stderr

    def assertExport(self, expected):
        result = self.run_cli("export")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected)

    def test_retag_replaces_tags_and_export_reflects_change(self):
        self.register_samples()

        # 验收场景：retag A --tag " project " --tag ui --tag project
        expected = {"path": self.path_a, "type": "image", "tags": ["project", "ui"]}
        self.assertRetagOk(
            expected, str(self.file_a), "--tag", " project ", "--tag", "ui", "--tag", "project"
        )

        # export 仍按 A、B 顺序返回，B 不变。
        self.assertExport([expected, self.expected_b])

    def test_retag_persists_for_query_in_new_process(self):
        self.register_samples()
        self.assertRetagOk(
            {"path": self.path_a, "type": "image", "tags": ["project", "ui"]},
            str(self.file_a), "--tag", "project", "--tag", "ui",
        )

        # 新进程 query：新标签命中 A，被移除的 demo 不再使 A 入选。
        result = self.run_cli("query", "--tag", "project")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            [{"path": self.path_a, "type": "image", "tags": ["project", "ui"]}],
        )

        result = self.run_cli("query", "--tag", "demo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [self.expected_b])

        # all / any / 类型筛选 / 文件状态检查规则不变。
        result = self.run_cli("query", "--tag", "project", "--tag", "ui")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)), 1)

        result = self.run_cli(
            "query", "--tag-mode", "any", "--tag", "project", "--tag", "demo"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)), 2)

        result = self.run_cli("query", "--tag", "project", "--type", "image")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)), 1)

        result = self.run_cli("query", "--tag", "project", "--check-files")
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)[0]
        self.assertEqual(record["file_status"], "present")

    def test_equivalent_path_spellings_resolve_same_record(self):
        self.register_samples()
        relative = os.path.relpath(self.path_a, PROJECT_ROOT)
        dotted = str(self.tmp_dir / "." / "A.bin")

        for spelling in (relative, dotted):
            data = self.assertRetagOk(
                {"path": self.path_a, "type": "image", "tags": ["x"]},
                spelling, "--tag", "x",
            )
            self.assertEqual(data["path"], self.path_a)

    def test_same_normalized_tags_repeated_succeeds(self):
        self.register_samples()
        args = (str(self.file_a), "--tag", " project ", "--tag", "ui", "--tag", "project")
        expected = {"path": self.path_a, "type": "image", "tags": ["project", "ui"]}
        self.assertRetagOk(expected, *args)
        # 重复提交相同的规范化标签与顺序仍成功，结果一致。
        self.assertRetagOk(expected, *args)
        self.assertExport([expected, self.expected_b])

    def test_deleted_or_directory_source_still_retaggable(self):
        self.register_samples()

        # 源文件已删除仍可改标签。
        self.file_a.unlink()
        self.assertRetagOk(
            {"path": self.path_a, "type": "image", "tags": ["gone"]},
            str(self.file_a), "--tag", "gone",
        )

        # 原路径变成目录仍可改标签。
        self.file_b.unlink()
        os.mkdir(self.path_b)
        self.assertRetagOk(
            {"path": self.path_b, "type": "audio", "tags": ["dir"]},
            str(self.file_b), "--tag", "dir",
        )

    def test_retag_does_not_touch_source_file_content(self):
        self.register_samples()
        before = self.file_a.read_bytes()
        self.assertRetagOk(
            {"path": self.path_a, "type": "image", "tags": ["new"]},
            str(self.file_a), "--tag", "new",
        )
        self.assertEqual(self.file_a.read_bytes(), before)

    def test_missing_path_or_tag_rejected(self):
        self.register_samples()
        # 缺少路径
        self.assertRetagError("--tag", "x")
        # 缺少 --tag
        self.assertRetagError(str(self.file_a))
        # 路径为空
        self.assertRetagError("", "--tag", "x")
        # 标签为空或只有空白
        self.assertRetagError(str(self.file_a), "--tag", "")
        self.assertRetagError(str(self.file_a), "--tag", "   ")
        self.assertRetagError(str(self.file_a), "--tag", "ok", "--tag", " ")
        # 不支持的参数
        self.assertRetagError(str(self.file_a), "--tag", "x", "--type", "image")
        self.assertRetagError(str(self.file_a), "--tag", "x", "extra")

        # 失败不改变已有记录。
        self.assertExport([self.expected_a, self.expected_b])

    def test_argument_errors_do_not_create_database(self):
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())
        self.assertRetagError(str(self.file_a), "--tag", " ", db=fresh)
        self.assertRetagError(str(self.file_a), db=fresh)
        self.assertFalse(fresh.exists())

    def test_unregistered_target_rejected_with_canonical_path(self):
        self.register_samples()
        stderr = self.assertRetagError(str(self.tmp_dir / "C.bin"), "--tag", "x")
        self.assertIn(os.path.realpath(str(self.tmp_dir / "C.bin")), stderr)
        # 失败不新增素材、不改变已有记录。
        self.assertExport([self.expected_a, self.expected_b])

    def test_fresh_database_created_then_unregistered_reported(self):
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())
        stderr = self.assertRetagError(str(self.file_a), "--tag", "x", db=fresh)
        self.assertIn(self.path_a, stderr)
        # 沿用自动创建规则：空库已创建且结构可用。
        self.assertTrue(fresh.exists())
        result = self.run_cli("export", db=fresh)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

    def test_missing_parent_directory_is_not_created(self):
        missing = self.tmp_dir / "no" / "such" / "catalog.sqlite"
        self.assertRetagError(str(self.file_a), "--tag", "x", db=missing)
        self.assertFalse((self.tmp_dir / "no").exists())

    def test_database_open_and_schema_errors_rejected(self):
        self.register_samples()

        # 数据库路径指向目录。
        target = self.tmp_dir / "a_directory"
        target.mkdir()
        self.assertRetagError(str(self.file_a), "--tag", "x", db=target)

        # 非 SQLite 文件：拒绝且字节不变。
        non_sqlite = self.tmp_dir / "broken.sqlite"
        non_sqlite.write_text("not a sqlite database", encoding="utf-8")
        before = non_sqlite.read_bytes()
        self.assertRetagError(str(self.file_a), "--tag", "x", db=non_sqlite)
        self.assertEqual(non_sqlite.read_bytes(), before)

        # 结构不兼容：拒绝且原有记录保留。
        foreign = self.tmp_dir / "foreign.sqlite"
        conn = sqlite3.connect(str(foreign))
        conn.execute("CREATE TABLE business_record(id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO business_record(note) VALUES ('fixed record')")
        conn.commit()
        conn.close()
        before = foreign.read_bytes()
        self.assertRetagError(str(self.file_a), "--tag", "x", db=foreign)
        self.assertEqual(foreign.read_bytes(), before)

        # 失败不影响既有数据库中的记录。
        self.assertExport([self.expected_a, self.expected_b])

    def test_add_still_rejects_duplicate_path_after_retag(self):
        self.register_samples()
        self.assertRetagOk(
            {"path": self.path_a, "type": "image", "tags": ["new"]},
            str(self.file_a), "--tag", "new",
        )
        result = self.run_cli(
            "add", str(self.file_a), "--type", "image", "--tag", "other"
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
