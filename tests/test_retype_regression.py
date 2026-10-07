"""retype 类型修改的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

覆盖：
- 成功修改：退出码 0、标准错误为空、标准输出仅为含 path、type、tags 的
  JSON 对象；新类型去除首尾空白、保留大小写、接受任意非空文本，
  不根据扩展名猜测类型；
- 路径按 add 的规范绝对路径规则定位同一记录（相对路径、.、.. 等价写法）；
- 提交与当前类型相同的规范化值仍成功，输出不变；
- 源文件已删除或变成目录仍可改类型，源文件内容保持只读；
- 结果持久保存：新进程的 query --type 按新类型筛选，export 显示新类型，
  旧类型不再命中；
- 素材路径、完整标签及其顺序、首次登记顺序与其他素材的记录保持不变；
- 缺少路径或 --type、路径为空、--type 为空或只有空白、传入不支持参数、
  目标未登记（错误含规范路径）、数据库无法打开或读写、损坏或结构不兼容
  时：退出码 2、标准输出为空、标准错误指出原因且不含调用栈；
- 参数错误不创建数据库；失败不新增素材、不改变已有记录、不留部分修改；
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


class RetypeRegressionTest(unittest.TestCase):
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

    def run_retype(self, *args, db=None):
        return self.run_cli("retype", *args, db=db)

    def assertRetypeOk(self, expected, *args, db=None):
        result = self.run_retype(*args, db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertEqual(set(data), {"path", "type", "tags"})
        self.assertEqual(data, expected)
        return data

    def assertRetypeError(self, *args, db=None):
        result = self.run_retype(*args, db=db)
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

    def test_retype_changes_type_and_query_filters_by_new_type(self):
        self.register_samples()

        # 验收场景：retype A.bin --type texture
        expected = {"path": self.path_a, "type": "texture", "tags": ["demo", "ui"]}
        self.assertRetypeOk(expected, str(self.file_a), "--type", "texture")

        # 新进程 query：--tag demo --type texture 只命中 A，标签保持 ["demo", "ui"]。
        result = self.run_cli("query", "--tag", "demo", "--type", "texture")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [expected])

        # 旧类型不再命中 A；B 保持原记录。
        result = self.run_cli("query", "--tag", "demo", "--type", "image")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

        result = self.run_cli("query", "--tag", "demo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [expected, self.expected_b])

        # export 仍按 A、B 顺序返回，B 不变。
        self.assertExport([expected, self.expected_b])

    def test_retype_strips_whitespace_and_preserves_case(self):
        self.register_samples()
        expected = {"path": self.path_a, "type": "TexTure", "tags": ["demo", "ui"]}
        self.assertRetypeOk(expected, str(self.file_a), "--type", "  TexTure  ")
        # 类型筛选区分大小写且完整匹配。
        result = self.run_cli("query", "--tag", "demo", "--type", "TexTure")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [expected])
        result = self.run_cli("query", "--tag", "demo", "--type", "texture")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

    def test_retype_accepts_arbitrary_text_without_extension_guess(self):
        self.register_samples()
        # 任意非空文本都可作为类型，不根据 .bin 扩展名猜测。
        expected = {"path": self.path_a, "type": "自定义/类型 v2", "tags": ["demo", "ui"]}
        self.assertRetypeOk(expected, str(self.file_a), "--type", "自定义/类型 v2")
        self.assertExport([expected, self.expected_b])

    def test_equivalent_path_spellings_resolve_same_record(self):
        self.register_samples()
        relative = os.path.relpath(self.path_a, PROJECT_ROOT)
        dotted = str(self.tmp_dir / "." / "A.bin")

        for spelling in (relative, dotted):
            data = self.assertRetypeOk(
                {"path": self.path_a, "type": "texture", "tags": ["demo", "ui"]},
                spelling, "--type", "texture",
            )
            self.assertEqual(data["path"], self.path_a)

    def test_same_normalized_type_repeated_succeeds(self):
        self.register_samples()
        expected = {"path": self.path_a, "type": "texture", "tags": ["demo", "ui"]}
        self.assertRetypeOk(expected, str(self.file_a), "--type", " texture ")
        # 提交与当前类型相同的规范化值仍成功，输出不变。
        self.assertRetypeOk(expected, str(self.file_a), "--type", "texture")
        self.assertRetypeOk(expected, str(self.file_a), "--type", "  texture  ")
        self.assertExport([expected, self.expected_b])

    def test_deleted_or_directory_source_still_retypable(self):
        self.register_samples()

        # 源文件已删除仍可改类型。
        self.file_a.unlink()
        self.assertRetypeOk(
            {"path": self.path_a, "type": "texture", "tags": ["demo", "ui"]},
            str(self.file_a), "--type", "texture",
        )

        # 原路径变成目录仍可改类型。
        self.file_b.unlink()
        os.mkdir(self.path_b)
        self.assertRetypeOk(
            {"path": self.path_b, "type": "voice", "tags": ["demo"]},
            str(self.file_b), "--type", "voice",
        )

    def test_retype_does_not_touch_source_file_content(self):
        self.register_samples()
        before = self.file_a.read_bytes()
        self.assertRetypeOk(
            {"path": self.path_a, "type": "texture", "tags": ["demo", "ui"]},
            str(self.file_a), "--type", "texture",
        )
        self.assertEqual(self.file_a.read_bytes(), before)

    def test_missing_path_or_type_rejected(self):
        self.register_samples()
        # 缺少路径
        self.assertRetypeError("--type", "texture")
        # 缺少 --type
        self.assertRetypeError(str(self.file_a))
        # 路径为空
        self.assertRetypeError("", "--type", "texture")
        # --type 为空或只有空白
        self.assertRetypeError(str(self.file_a), "--type", "")
        self.assertRetypeError(str(self.file_a), "--type", "   ")
        # 不支持的参数
        self.assertRetypeError(str(self.file_a), "--type", "texture", "--tag", "x")
        self.assertRetypeError(str(self.file_a), "--type", "texture", "extra")

        # 失败不改变已有记录。
        self.assertExport([self.expected_a, self.expected_b])

    def test_argument_errors_do_not_create_database(self):
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())
        self.assertRetypeError(str(self.file_a), "--type", " ", db=fresh)
        self.assertRetypeError(str(self.file_a), db=fresh)
        self.assertFalse(fresh.exists())

    def test_unregistered_target_rejected_with_canonical_path(self):
        self.register_samples()
        stderr = self.assertRetypeError(str(self.tmp_dir / "C.bin"), "--type", "texture")
        self.assertIn(os.path.realpath(str(self.tmp_dir / "C.bin")), stderr)
        # 失败不新增素材、不改变已有记录。
        self.assertExport([self.expected_a, self.expected_b])

    def test_fresh_database_created_then_unregistered_reported(self):
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())
        stderr = self.assertRetypeError(str(self.file_a), "--type", "texture", db=fresh)
        self.assertIn(self.path_a, stderr)
        # 沿用自动创建规则：空库已创建且结构可用。
        self.assertTrue(fresh.exists())
        result = self.run_cli("export", db=fresh)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

    def test_missing_parent_directory_is_not_created(self):
        missing = self.tmp_dir / "no" / "such" / "catalog.sqlite"
        self.assertRetypeError(str(self.file_a), "--type", "texture", db=missing)
        self.assertFalse((self.tmp_dir / "no").exists())

    def test_database_open_and_schema_errors_rejected(self):
        self.register_samples()

        # 数据库路径指向目录。
        target = self.tmp_dir / "a_directory"
        target.mkdir()
        self.assertRetypeError(str(self.file_a), "--type", "texture", db=target)

        # 非 SQLite 文件：拒绝且字节不变。
        non_sqlite = self.tmp_dir / "broken.sqlite"
        non_sqlite.write_text("not a sqlite database", encoding="utf-8")
        before = non_sqlite.read_bytes()
        self.assertRetypeError(str(self.file_a), "--type", "texture", db=non_sqlite)
        self.assertEqual(non_sqlite.read_bytes(), before)

        # 结构不兼容：拒绝且原有记录保留。
        foreign = self.tmp_dir / "foreign.sqlite"
        conn = sqlite3.connect(str(foreign))
        conn.execute("CREATE TABLE business_record(id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO business_record(note) VALUES ('fixed record')")
        conn.commit()
        conn.close()
        before = foreign.read_bytes()
        self.assertRetypeError(str(self.file_a), "--type", "texture", db=foreign)
        self.assertEqual(foreign.read_bytes(), before)

        # 失败不影响既有数据库中的记录。
        self.assertExport([self.expected_a, self.expected_b])

    def test_add_and_retag_still_work_after_retype(self):
        self.register_samples()
        self.assertRetypeOk(
            {"path": self.path_a, "type": "texture", "tags": ["demo", "ui"]},
            str(self.file_a), "--type", "texture",
        )
        # 重复登记同一路径仍被拒绝。
        result = self.run_cli(
            "add", str(self.file_a), "--type", "image", "--tag", "other"
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

        # retag 保留 retype 后的新类型。
        result = self.run_cli("retag", str(self.file_a), "--tag", "new")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"path": self.path_a, "type": "texture", "tags": ["new"]},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
