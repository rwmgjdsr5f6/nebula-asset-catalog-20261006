"""export 完整目录导出的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

覆盖：
- 导出全部素材，按首次登记顺序各出现一次，仅含 path、type、tags 三个字段；
- 标签保留完整集合与登记顺序；空目录输出 []；
- 源文件删除后记录仍在结果中，且不出现 file_status 字段；
- --check-files 为每条记录追加 file_status（present / missing / not_file），
  保留全部记录与首次登记顺序；空目录输出 []；任一记录状态无法判断时
  整次导出退出码 2、标准输出为空、标准错误说明原因且包含相关路径，
  已处理过的记录也不会提前输出；
- A 状态正常、仅 B 的状态查询确定性抛出 PermissionError 时：--check-files
  整体失败（退出码 2、标准输出为空、标准错误说明“无法确定文件状态”并
  包含 B 的规范绝对路径、不含调用栈）；同样的失败条件下不带 --check-files
  的 export 仍以退出码 0 输出 A、B 完整元数据且不含 file_status；撤去失败
  条件后再次检查，两条记录均为 present，路径、类型、标签顺序与登记顺序
  不变；失败前后数据库字节与两个源文件内容保持一致。失败由测试经
  PYTHONPATH 注入子进程的 sitecustomize 垫片合成，只替换 os.stat，与实际
  权限位和账号身份无关，因此在 Windows 普通用户及 Linux root、非 root
  环境中均实际执行，不跳过；
- 导出不改变登记内容（之后 query --tag demo 结果不变），也不改动素材文件；
- 数据库文件不存在但父目录存在时创建空目录数据库并输出 []，
  父目录缺失时不补建目录；
- 缺少 --db、数据库路径为空、export 传入不支持的参数、数据库路径指向目录、
  数据库无法打开或内容损坏、表结构不兼容时：退出码 2、标准输出为空、
  标准错误说明原因且不含调用栈；损坏或不兼容的数据库字节保持不变；
- 读取失败时不输出部分记录。

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

CONTENT_A = b"demo asset A\n"
CONTENT_B = b"demo asset B\n"

# 子进程内自动导入的垫片：仅当环境变量给出目标路径时，把 os.stat 替换为
# 对该路径固定抛出 PermissionError 的版本，其余路径照常。状态检查进程对
# B 的规范路径读取状态时即得到稳定的权限错误，不依赖实际权限配置或账号
# 身份（Windows 普通用户、Linux root 与非 root 行为一致）。
SITECUSTOMIZE_SOURCE = '''\
"""测试垫片：对指定路径的 os.stat 固定抛出 PermissionError。"""

import os

_fail_path = os.environ.get("ASSET_CATALOG_TEST_STAT_FAIL_PATH")
if _fail_path:
    _real_stat = os.stat

    def _stat_with_fixed_failure(path, *args, **kwargs):
        if os.fspath(path) == _fail_path:
            raise PermissionError(13, "Permission denied", _fail_path)
        return _real_stat(path, *args, **kwargs)

    os.stat = _stat_with_fixed_failure
'''

STAT_FAIL_ENV = "ASSET_CATALOG_TEST_STAT_FAIL_PATH"


class ExportRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "A.bin"
        self.file_b = self.tmp_dir / "B.bin"
        self.file_a.write_bytes(CONTENT_A)
        self.file_b.write_bytes(CONTENT_B)
        self.db_path = self.tmp_dir / "catalog.sqlite"

        # 垫片目录：sitecustomize.py 随 PYTHONPATH 进入每个子进程，
        # 仅在环境变量点名 B 的规范路径时生效。
        self.shim_dir = self.tmp_dir / "shim"
        self.shim_dir.mkdir()
        (self.shim_dir / "sitecustomize.py").write_text(
            SITECUSTOMIZE_SOURCE, encoding="utf-8"
        )

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

    def run_cli(self, *args, db=None, fail_stat_on_b=False):
        """以全新进程运行 python -m asset_catalog，返回 CompletedProcess。

        fail_stat_on_b 为真时，该进程内对 B 的规范登记路径的 os.stat
        固定抛出 PermissionError，其余路径与不带开关时完全一致。
        """
        cmd = [
            sys.executable,
            "-m",
            "asset_catalog",
            "--db",
            str(self.db_path if db is None else db),
            *args,
        ]
        # 显式把项目根与垫片目录放入 PYTHONPATH，使验收命令在任意工作
        # 目录下均可解析 asset_catalog 包与 sitecustomize 垫片；子进程
        # 仍是全新的独立进程。
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            [str(PROJECT_ROOT), str(self.shim_dir), env.get("PYTHONPATH", "")]
        )
        if fail_stat_on_b:
            env[STAT_FAIL_ENV] = self.expected_b["path"]
        else:
            env.pop(STAT_FAIL_ENV, None)
        return subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            env=env,
        )

    def register_samples(self):
        """按验收顺序登记 A（image: demo, ui）与 B（audio: music）。"""
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
            "audio",
            "--tag",
            "music",
        )
        self.assertEqual(result_b.returncode, 0, result_b.stderr)
        self.assertEqual(result_b.stderr, "")

    def assertExportOk(self, expected, db=None):
        result = self.run_cli("export", db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        self.assertEqual(data, expected)
        return data

    def assertExportError(self, *args, db=None):
        result = self.run_cli("export", *args, db=db)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr.strip(), "")
        self.assertNotIn("Traceback", result.stderr)
        return result.stderr

    def test_export_returns_all_records_in_registration_order(self):
        self.register_samples()

        data = self.assertExportOk([self.expected_a, self.expected_b])
        self.assertEqual(len(data), 2)
        # 每条记录仅含现有查询使用的三个字段，没有文件状态或内部编号。
        for record in data:
            self.assertEqual(set(record), {"path", "type", "tags"})
        # 保留规范绝对路径、类型文本与完整标签顺序。
        self.assertTrue(os.path.isabs(data[0]["path"]))
        self.assertEqual(data[0]["tags"], ["demo", "ui"])
        self.assertEqual(data[1]["tags"], ["music"])

    def test_deleted_source_file_still_exported_without_file_status(self):
        self.register_samples()
        self.file_b.unlink()
        self.assertFalse(self.file_b.exists())

        # 导出只反映登记记录，不检查文件状态：B 仍在结果中。
        data = self.assertExportOk([self.expected_a, self.expected_b])
        for record in data:
            self.assertNotIn("file_status", record)

    def test_export_does_not_change_registrations_or_files(self):
        self.register_samples()
        db_before = self.db_path.read_bytes()
        a_before = self.file_a.read_bytes()

        self.assertExportOk([self.expected_a, self.expected_b])

        # 数据库内容与未删除的素材文件均不变。
        self.assertEqual(self.db_path.read_bytes(), db_before)
        self.assertEqual(self.file_a.read_bytes(), a_before)

        # 之后 query --tag demo 仍只返回 A。
        result = self.run_cli("query", "--tag", "demo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [self.expected_a])

    def test_empty_database_exports_empty_array(self):
        # 尚未初始化：文件不存在但父目录存在，创建空目录数据库并输出 []。
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())
        self.assertExportOk([], db=fresh)
        self.assertTrue(fresh.exists())
        # 由新进程再次读取同一空库，结论一致。
        self.assertExportOk([], db=fresh)

    def test_missing_parent_directory_is_not_created(self):
        missing = self.tmp_dir / "no" / "such" / "catalog.sqlite"
        self.assertExportError(db=missing)
        self.assertFalse((self.tmp_dir / "no").exists())

    def test_unsupported_export_arguments_rejected(self):
        self.register_samples()
        self.assertExportError("--tag", "demo")
        self.assertExportError("extra-positional")
        self.assertExportError("--type", "image")
        self.assertExportError("--file-status", "present")

    def test_check_files_appends_status_and_keeps_all_records(self):
        self.register_samples()
        self.file_b.unlink()

        result = self.run_cli("export", "--check-files")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        # 标准输出仅一行 JSON 数组。
        self.assertEqual(result.stdout.count("\n"), 1)
        data = json.loads(result.stdout)
        self.assertEqual(
            data,
            [
                {**self.expected_a, "file_status": "present"},
                {**self.expected_b, "file_status": "missing"},
            ],
        )
        # 仍按首次登记顺序各出现一次，缺失文件的记录也保留。
        self.assertEqual(len(data), 2)

        # 新进程默认导出不含 file_status，也不检查文件状态。
        data = self.assertExportOk([self.expected_a, self.expected_b])
        for record in data:
            self.assertNotIn("file_status", record)

    def test_check_files_reports_directory_as_not_file(self):
        self.register_samples()
        self.file_b.unlink()
        self.file_b.mkdir()

        result = self.run_cli("export", "--check-files")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data[0]["file_status"], "present")
        self.assertEqual(data[1]["file_status"], "not_file")
        # 输出 path 仍保留登记值。
        self.assertEqual(data[1]["path"], self.expected_b["path"])

    def test_check_files_empty_database_outputs_empty_array(self):
        fresh = self.tmp_dir / "fresh.sqlite"
        result = self.run_cli("export", "--check-files", db=fresh)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [])

    def test_check_files_status_error_fails_whole_export(self):
        # A 状态正常，仅 B 的状态查询确定性抛出 PermissionError。错误由
        # 子进程内的 sitecustomize 垫片对 os.stat 定点替换合成，不改动
        # 实际权限位，因此 Windows 普通用户、Linux root 与非 root 都执行
        # 本用例，不跳过。
        self.register_samples()
        db_before = self.db_path.read_bytes()
        a_before = self.file_a.read_bytes()
        b_before = self.file_b.read_bytes()

        # --check-files：整次导出失败，不提前输出 A 的记录。
        result = self.run_cli("export", "--check-files", fail_stat_on_b=True)
        self.assertEqual(
            result.returncode,
            2,
            f"状态读取失败应退出为 2；实际退出码 {result.returncode}，"
            f"输出: {result.stdout!r}，错误: {result.stderr!r}",
        )
        # 标准输出完全为空：A 虽已处理也不能提前输出。
        self.assertEqual(result.stdout, "")
        self.assertIn("无法确定文件状态", result.stderr)
        self.assertIn(self.expected_b["path"], result.stderr)
        self.assertNotIn(self.expected_a["path"], result.stdout)
        self.assertNotIn("Traceback", result.stderr)

        # 同样的失败条件下，不带 --check-files 的 export 不访问文件系统：
        # 退出码 0、标准错误为空，输出 A、B 完整元数据且不含 file_status。
        result = self.run_cli("export", fail_stat_on_b=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertEqual(data, [self.expected_a, self.expected_b])
        for record in data:
            self.assertNotIn("file_status", record)

        # 撤去失败条件后再次检查：两条记录均 present，原路径、类型、
        # 标签顺序与登记顺序不变。
        result = self.run_cli("export", "--check-files")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            [
                {**self.expected_a, "file_status": "present"},
                {**self.expected_b, "file_status": "present"},
            ],
        )

        # 失败前后数据库字节与两个源文件内容保持一致。
        self.assertEqual(self.db_path.read_bytes(), db_before)
        self.assertEqual(self.file_a.read_bytes(), a_before)
        self.assertEqual(self.file_b.read_bytes(), b_before)

    def test_database_path_is_directory_rejected(self):
        target = self.tmp_dir / "a_directory"
        target.mkdir()
        self.assertExportError(db=target)

    def test_empty_database_path_rejected(self):
        # --db 显式传空字符串：退出码 2、标准输出为空。
        result = self.run_cli("export", db="")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_db_option_rejected(self):
        cmd = [sys.executable, "-m", "asset_catalog", "export"]
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
        self.assertExportError(db=non_sqlite)
        self.assertEqual(non_sqlite.read_bytes(), before)

        # 含其他业务表的 SQLite 文件：拒绝、不补表、不重建，原有记录保留。
        foreign = self.tmp_dir / "foreign.sqlite"
        conn = sqlite3.connect(str(foreign))
        conn.execute("CREATE TABLE business_record(id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO business_record(note) VALUES ('fixed record')")
        conn.commit()
        conn.close()
        before = foreign.read_bytes()
        self.assertExportError(db=foreign)
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
        self.assertExportError(db=bad_schema)

    def test_malformed_database_produces_no_partial_output(self):
        self.register_samples()
        malformed = self.tmp_dir / "malformed.sqlite"
        malformed.write_bytes(self.db_path.read_bytes())
        size = malformed.stat().st_size
        with malformed.open("wb") as fh:
            fh.truncate(size // 2)

        # 镜像损坏：整次操作失败，不输出任何部分记录。
        self.assertExportError(db=malformed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
