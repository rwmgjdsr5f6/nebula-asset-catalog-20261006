"""show 按路径查看单条记录的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

覆盖：
- 验收：预先按 A.bin、B.bin 顺序登记（A 为 image，标签依次 demo、ui），
  在演示目录执行 ``--db catalog.sqlite show ./A.bin`` 返回 A 的规范绝对路径、
  image 与 ["demo", "ui"]；标准输出仅一行 JSON 对象且只含 path、type、tags，
  退出码 0、标准错误为空；查询未登记的 ./C.bin 按未登记规则失败；
- 相对路径、含 . 或 .. 的等价写法与解析后指向同一登记路径的符号链接
  定位同一记录；
- show 不读取素材内容、不检查当前文件状态：源文件删除或原登记路径变成
  目录时，通过原路径仍返回记录；show 前后数据库字节与源文件字节不变；
- 记录反映当前保存的类型与完整标签、标签顺序不变（retag/retype 后）；
- 缺少路径或 --db、素材或数据库路径为空字符串、传入不支持的参数：
  退出码 2、标准输出为空、标准错误指出参数及原因且不含调用栈，
  参数错误不创建数据库；
- 未登记时标准错误说明素材未登记并包含规范绝对路径；数据库文件不存在但
  父目录存在时沿用空库创建规则后报告未登记，父目录缺失时不补建目录；
- 数据库路径指向目录、打不开、损坏、结构不兼容或记录读取失败：
  退出码 2、标准输出为空、不输出部分记录；
- 成功或失败后再次导出，已有记录的路径、类型、标签及登记顺序均不变；
  show 兼容现有数据库，add/query/export/retag/retype 行为不受影响。

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
        self.expected_b = {"path": self.path_b, "type": "audio", "tags": ["music"]}

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args, db=None, cwd=None):
        """以全新进程运行 python -m asset_catalog，返回 CompletedProcess。

        cwd 默认为项目根目录；在临时演示目录中运行验收命令时通过
        PYTHONPATH 保证仍能导入本包。
        """
        cmd = [
            sys.executable,
            "-m",
            "asset_catalog",
            "--db",
            str(self.db_path if db is None else db),
            *args,
        ]
        env = None
        if cwd is not None:
            env = dict(os.environ)
            env["PYTHONPATH"] = (
                str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
            )
        return subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT if cwd is None else cwd),
            capture_output=True,
            text=True,
            env=env,
        )

    def register_samples(self):
        """按验收顺序登记 A（image: demo, ui）与 B（audio: music）。"""
        result_a = self.run_cli(
            "add", str(self.file_a), "--type", "image", "--tag", "demo", "--tag", "ui"
        )
        self.assertEqual(result_a.returncode, 0, result_a.stderr)
        self.assertEqual(result_a.stderr, "")

        result_b = self.run_cli(
            "add", str(self.file_b), "--type", "audio", "--tag", "music"
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
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1, result.stdout)
        data = json.loads(lines[0])
        self.assertIsInstance(data, dict)
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

    def test_acceptance_relative_path_in_demo_directory(self):
        self.register_samples()

        # 验收命令：在同一演示目录执行
        # python -m asset_catalog --db catalog.sqlite show ./A.bin
        data = self.assertShowOk(
            self.expected_a, "./A.bin", db="catalog.sqlite", cwd=self.tmp_dir
        )
        self.assertEqual(data["path"], self.path_a)
        self.assertTrue(os.path.isabs(data["path"]))
        self.assertEqual(data["type"], "image")
        self.assertEqual(data["tags"], ["demo", "ui"])

    def test_unregistered_c_rejected_by_relative_path_in_demo_directory(self):
        self.register_samples()

        # 未登记的 ./C.bin：按未登记规则失败，错误含规范绝对路径。
        canonical_c = os.path.realpath(str(self.tmp_dir / "C.bin"))
        stderr = self.assertShowError(
            "./C.bin", db="catalog.sqlite", cwd=self.tmp_dir
        )
        self.assertIn("未登记", stderr)
        self.assertIn(canonical_c, stderr)

        # 失败后导出不变：A、B 顺序与内容保持。
        self.assertExport([self.expected_a, self.expected_b])

    def test_equivalent_path_spellings_resolve_same_record(self):
        self.register_samples()
        sub = self.tmp_dir / "sub"
        sub.mkdir()

        spellings = (
            self.path_a,  # 绝对路径
            os.path.relpath(self.path_a, PROJECT_ROOT),  # 相对项目根的路径
            str(self.tmp_dir / "." / "A.bin"),  # 含 . 的等价写法
            str(sub / ".." / "A.bin"),  # 含 .. 的等价写法
            str(self.tmp_dir / "." / "sub" / ".." / "A.bin"),  # 同时含 . 与 ..
        )
        for spelling in spellings:
            self.assertShowOk(self.expected_a, spelling)

    def test_symlink_resolving_to_registered_path_shows_same_record(self):
        self.register_samples()
        link = self.tmp_dir / "A_link.bin"
        try:
            os.symlink(self.file_a, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"当前环境不支持创建符号链接: {exc}")

        # 符号链接路径解析后与登记路径相同：定位同一记录，输出登记路径本身。
        data = self.assertShowOk(self.expected_a, str(link))
        self.assertEqual(data["path"], self.path_a)

    def test_deleted_source_still_shown_through_original_path(self):
        self.register_samples()
        self.file_a.unlink()
        self.assertFalse(self.file_a.exists())
        # show 不检查当前文件状态：源文件已删除仍返回登记记录。
        self.assertShowOk(self.expected_a, str(self.file_a))

    def test_directory_at_registered_path_still_shown(self):
        self.register_samples()
        self.file_b.unlink()
        os.mkdir(self.path_b)
        # 原登记路径变成目录：仍按登记记录返回，不报“不是普通文件”。
        self.assertShowOk(self.expected_b, str(self.file_b))

    def test_show_is_readonly_against_database_and_source(self):
        self.register_samples()
        db_before = self.db_path.read_bytes()
        a_before = self.file_a.read_bytes()

        self.assertShowOk(self.expected_a, str(self.file_a))

        self.assertEqual(self.db_path.read_bytes(), db_before)
        self.assertEqual(self.file_a.read_bytes(), a_before)

    def test_show_reflects_current_type_and_full_tag_order(self):
        self.register_samples()

        # retag 调整标签顺序，retype 修改类型后，show 反映当前保存值。
        result = self.run_cli(
            "retag", str(self.file_a), "--tag", "zeta", "--tag", "demo"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli(
            "retype", str(self.file_a), "--type", "texture"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertShowOk(
            {"path": self.path_a, "type": "texture", "tags": ["zeta", "demo"]},
            str(self.file_a),
        )
        # B 不受影响。
        self.assertShowOk(self.expected_b, str(self.file_b))

    def test_missing_path_or_unsupported_arguments_rejected(self):
        self.register_samples()

        # 缺少素材路径。
        self.assertShowError()
        # 多余的位置参数。
        self.assertShowError(str(self.file_a), str(self.file_b))
        # show 不接受标签或其他子命令的选项（--check-files 已为 show 支持，
        # 其行为见 tests/test_show_check_files_regression.py）。
        self.assertShowError(str(self.file_a), "--tag", "demo")
        self.assertShowError(str(self.file_a), "--type", "image")
        self.assertShowError(str(self.file_a), "--append")
        self.assertShowError("--unknown-option", str(self.file_a))
        # 素材路径为空字符串。
        self.assertShowError("")

        # 全部失败不改变已有记录。
        self.assertExport([self.expected_a, self.expected_b])

    def test_argument_errors_do_not_create_database(self):
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())
        # 空路径在打开数据库之前即报错。
        self.assertShowError("", db=fresh)
        # 不支持的参数在参数解析阶段即报错。
        self.assertShowError(str(self.file_a), "--tag", "x", db=fresh)
        self.assertFalse(fresh.exists())

    def test_missing_db_option_rejected(self):
        cmd = [sys.executable, "-m", "asset_catalog", "show", str(self.file_a)]
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
        result = self.run_show(str(self.file_a), db="")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

    def test_fresh_database_created_then_unregistered_reported(self):
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())

        stderr = self.assertShowError(str(self.file_a), db=fresh)
        self.assertIn("未登记", stderr)
        self.assertIn(self.path_a, stderr)

        # 沿用自动创建规则：空库已创建且结构可用，导出为 []。
        self.assertTrue(fresh.exists())
        result = self.run_cli("export", db=fresh)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

    def test_missing_parent_directory_is_not_created(self):
        missing = self.tmp_dir / "no" / "such" / "catalog.sqlite"
        self.assertShowError(str(self.file_a), db=missing)
        self.assertFalse((self.tmp_dir / "no").exists())

    def test_database_path_is_directory_rejected(self):
        self.register_samples()
        target = self.tmp_dir / "a_directory"
        target.mkdir()
        self.assertShowError(str(self.file_a), db=target)

    def test_corrupt_and_incompatible_databases_rejected_and_untouched(self):
        self.register_samples()

        # 非 SQLite 文本文件：拒绝且字节不变。
        non_sqlite = self.tmp_dir / "broken.sqlite"
        non_sqlite.write_text("not a sqlite database", encoding="utf-8")
        before = non_sqlite.read_bytes()
        self.assertShowError(str(self.file_a), db=non_sqlite)
        self.assertEqual(non_sqlite.read_bytes(), before)

        # 含其他业务表的 SQLite 文件：拒绝、不补表、不重建，原有记录保留。
        foreign = self.tmp_dir / "foreign.sqlite"
        conn = sqlite3.connect(str(foreign))
        conn.execute("CREATE TABLE business_record(id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO business_record(note) VALUES ('fixed record')")
        conn.commit()
        conn.close()
        before = foreign.read_bytes()
        self.assertShowError(str(self.file_a), db=foreign)
        self.assertEqual(foreign.read_bytes(), before)

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
        self.assertShowError(str(self.file_a), db=bad_schema)

        # 正常数据库截断为损坏镜像：不输出部分记录。
        malformed = self.tmp_dir / "malformed.sqlite"
        malformed.write_bytes(self.db_path.read_bytes())
        with malformed.open("wb") as fh:
            fh.truncate(malformed.stat().st_size // 2)
        self.assertShowError(str(self.file_a), db=malformed)

        # 各类失败不影响既有数据库中的记录。
        self.assertExport([self.expected_a, self.expected_b])

    def test_export_unchanged_after_success_and_failure(self):
        self.register_samples()

        self.assertShowOk(self.expected_a, str(self.file_a))
        self.assertShowError(str(self.tmp_dir / "C.bin"))
        self.assertShowOk(self.expected_b, str(self.file_b))

        # 路径、类型、标签及 A、B 的登记顺序均不变。
        self.assertExport([self.expected_a, self.expected_b])

    def test_show_works_with_existing_database_and_other_commands_intact(self):
        # show 兼容由既有 add 流程建好的数据库，无需迁移；
        # 其他子命令的公开行为保持原样。
        self.register_samples()
        self.assertShowOk(self.expected_a, str(self.file_a))

        result = self.run_cli("query", "--tag", "demo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [self.expected_a])

        result = self.run_cli("add", str(self.file_a), "--type", "x", "--tag", "y")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
