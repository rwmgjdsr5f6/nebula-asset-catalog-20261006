"""show 按路径查看单条记录的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

覆盖：
- 成功查看：退出码 0、标准错误为空、标准输出仅一行 JSON 对象，
  只含 path、type、tags，反映当前保存的类型与完整标签，标签顺序不变；
- 路径按 add 的规范绝对路径规则定位同一记录（相对路径、.、.. 等价写法、
  指向同一登记路径的符号链接）；
- 不读取素材内容、不检查当前文件状态：源文件已删除或原路径变成目录时
  仍返回记录，源文件内容保持只读；
- 未登记时报错且标准错误含规范绝对路径；缺少路径、路径为空字符串、
  传入不支持参数、数据库路径指向目录、父目录缺失、数据库打不开、损坏、
  结构不兼容或读取失败时：退出码 2、标准输出为空、标准错误指出原因
  且不含调用栈；
- 参数错误不创建数据库；数据库文件不存在但父目录存在时创建空库后
  报告未登记，父目录缺失时不补建目录；
- show 为只读操作：成功或失败后再次导出，已有记录的路径、类型、标签
  及登记顺序均不变。

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


class ShowRegressionTest(unittest.TestCase):
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

    def run_cli(self, *args, db=None, cwd=None):
        """以全新进程运行 python -m asset_catalog，返回 CompletedProcess。"""
        cmd = [
            sys.executable,
            "-m",
            "asset_catalog",
            "--db",
            str(self.db_path if db is None else db),
            *args,
        ]
        # 从其他工作目录（如演示目录）运行时仍能定位 asset_catalog 包。
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(PROJECT_ROOT), env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
        return subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT if cwd is None else cwd),
            capture_output=True,
            text=True,
            env=env,
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

    def run_show(self, *args, db=None, cwd=None):
        return self.run_cli("show", *args, db=db, cwd=cwd)

    def assertShowOk(self, expected, *args, db=None, cwd=None):
        result = self.run_show(*args, db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        # 标准输出仅一行 JSON 对象。
        self.assertEqual(result.stdout.count("\n"), 1)
        data = json.loads(result.stdout)
        self.assertEqual(set(data), {"path", "type", "tags"})
        self.assertEqual(data, expected)
        return data

    def assertShowError(self, *args, db=None, cwd=None):
        result = self.run_show(*args, db=db, cwd=cwd)
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

    def test_show_returns_single_record_and_export_unchanged(self):
        self.register_samples()

        # 验收场景：在演示目录内以相对路径查看 A.bin。
        self.assertShowOk(self.expected_a, "./A.bin", cwd=self.tmp_dir)
        self.assertShowOk(self.expected_b, "./B.bin", cwd=self.tmp_dir)

        # 成功后再次导出：路径、类型、标签及登记顺序均不变。
        self.assertExport([self.expected_a, self.expected_b])

    def test_show_reflects_current_type_and_full_tag_order(self):
        self.register_samples()
        # retag 与 retype 后 show 反映当前保存的类型与完整标签，标签顺序不变。
        result = self.run_cli("retag", str(self.file_a), "--tag", "ui", "--tag", "demo")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli("retype", str(self.file_a), "--type", "texture")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertShowOk(
            {"path": self.path_a, "type": "texture", "tags": ["ui", "demo"]},
            str(self.file_a),
        )

    def test_equivalent_path_spellings_resolve_same_record(self):
        self.register_samples()
        relative = os.path.relpath(self.path_a, PROJECT_ROOT)
        dotted = str(self.tmp_dir / "." / "A.bin")
        dotdot = str(self.tmp_dir / "sub" / ".." / "A.bin")

        for spelling in (relative, dotted, dotdot):
            data = self.assertShowOk(self.expected_a, spelling)
            self.assertEqual(data["path"], self.path_a)

    def test_symlink_to_registered_path_resolves_same_record(self):
        self.register_samples()
        link = self.tmp_dir / "link_to_a.bin"
        try:
            os.symlink(self.path_a, str(link))
        except (OSError, NotImplementedError):
            self.skipTest("当前平台不支持创建符号链接")
        data = self.assertShowOk(self.expected_a, str(link))
        self.assertEqual(data["path"], self.path_a)

    def test_deleted_or_directory_source_still_shown(self):
        self.register_samples()

        # 源文件已删除仍返回记录。
        self.file_a.unlink()
        self.assertShowOk(self.expected_a, str(self.file_a))

        # 原登记路径变成目录仍返回记录。
        self.file_b.unlink()
        os.mkdir(self.path_b)
        self.assertShowOk(self.expected_b, str(self.file_b))

    def test_show_does_not_touch_source_file_content(self):
        self.register_samples()
        before = self.file_a.read_bytes()
        self.assertShowOk(self.expected_a, str(self.file_a))
        self.assertEqual(self.file_a.read_bytes(), before)

    def test_unregistered_target_rejected_with_canonical_path(self):
        self.register_samples()
        # 验收场景：查询未登记的 C.bin。
        stderr = self.assertShowError("./C.bin", cwd=self.tmp_dir)
        self.assertIn("未登记", stderr)
        self.assertIn(os.path.realpath(str(self.tmp_dir / "C.bin")), stderr)
        # 失败后再次导出：已有记录不变。
        self.assertExport([self.expected_a, self.expected_b])

    def test_missing_or_empty_path_and_unsupported_args_rejected(self):
        self.register_samples()
        # 缺少路径
        self.assertShowError()
        # 路径为空字符串
        self.assertShowError("")
        # 不支持的参数
        self.assertShowError(str(self.file_a), "--tag", "demo")
        self.assertShowError(str(self.file_a), "--type", "image")
        self.assertShowError(str(self.file_a), "extra")

        # 失败不改变已有记录。
        self.assertExport([self.expected_a, self.expected_b])

    def test_missing_db_option_rejected(self):
        self.register_samples()
        result = subprocess.run(
            [sys.executable, "-m", "asset_catalog", "show", str(self.file_a)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr.strip(), "")
        self.assertNotIn("Traceback", result.stderr)

    def test_empty_db_path_rejected(self):
        self.register_samples()
        self.assertShowError(str(self.file_a), db="")

    def test_argument_errors_do_not_create_database(self):
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())
        self.assertShowError("", db=fresh)
        self.assertShowError(db=fresh)
        self.assertShowError(str(self.file_a), "--tag", "x", db=fresh)
        self.assertFalse(fresh.exists())

    def test_fresh_database_created_then_unregistered_reported(self):
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())
        stderr = self.assertShowError(str(self.file_a), db=fresh)
        self.assertIn(self.path_a, stderr)
        # 沿用自动创建规则：空库已创建且结构可用。
        self.assertTrue(fresh.exists())
        result = self.run_cli("export", db=fresh)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

    def test_missing_parent_directory_is_not_created(self):
        missing = self.tmp_dir / "no" / "such" / "catalog.sqlite"
        self.assertShowError(str(self.file_a), db=missing)
        self.assertFalse((self.tmp_dir / "no").exists())

    def test_database_open_and_schema_errors_rejected(self):
        self.register_samples()

        # 数据库路径指向目录。
        target = self.tmp_dir / "a_directory"
        target.mkdir()
        self.assertShowError(str(self.file_a), db=target)

        # 非 SQLite 文件：拒绝且字节不变。
        non_sqlite = self.tmp_dir / "broken.sqlite"
        non_sqlite.write_text("not a sqlite database", encoding="utf-8")
        before = non_sqlite.read_bytes()
        self.assertShowError(str(self.file_a), db=non_sqlite)
        self.assertEqual(non_sqlite.read_bytes(), before)

        # 结构不兼容：拒绝且原有记录保留。
        foreign = self.tmp_dir / "foreign.sqlite"
        conn = sqlite3.connect(str(foreign))
        conn.execute("CREATE TABLE business_record(id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO business_record(note) VALUES ('fixed record')")
        conn.commit()
        conn.close()
        before = foreign.read_bytes()
        self.assertShowError(str(self.file_a), db=foreign)
        self.assertEqual(foreign.read_bytes(), before)

        # 失败不影响既有数据库中的记录。
        self.assertExport([self.expected_a, self.expected_b])

    def test_show_is_read_only_on_database(self):
        self.register_samples()
        before = self.db_path.read_bytes()
        self.assertShowOk(self.expected_a, str(self.file_a))
        # 只读查看：数据库文件字节不变（无日志或页缓存改动落盘）。
        self.assertEqual(self.db_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
