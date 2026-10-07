"""query --file-status 文件状态筛选的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

覆盖：
- 依次登记 A（image，标签 demo、ui）、B（audio，标签 demo）、
  C（image，标签 ui、demo）后，删除 B、将 C 原路径替换为空目录：
  ``query --tag demo --file-status missing`` 只返回 B 的原登记记录，
  仅含 path、type、tags；加 --check-files 后该记录追加值为 missing 的
  file_status；--file-status present 只返回 A，not_file 只返回 C；
- 恢复 B 为普通文件后，新进程执行 missing 查询返回 []，无需重新登记；
- 状态筛选与 --type、--tag-mode any 组合时先确定候选再筛选状态；
- 无匹配状态或未命中标签时输出 []；
- 不传 --file-status 时保留原查询行为，两项都未启用时不检查文件状态；
- --file-status 缺值、为空、含首尾空白、大小写不符或取其他值时：
  退出码 2、标准输出为空、标准错误指出 --file-status、不含调用栈、
  不创建数据库；
- 任一候选状态读取出现 PermissionError 时（子进程内确定性注入），
  整次查询退出码 2、标准输出为空、标准错误指出状态读取失败及相关路径，
  不输出部分结果；未命中标签或类型的素材不检查状态；
- 查询成功时退出码 0、标准输出仅为 JSON 数组、标准错误为空；
  查询不改动已登记记录与素材文件。

每个用例使用独立的临时目录与数据库，只创建/清理自己的样例文件。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 在子进程中对指定路径的 os.stat 注入 PermissionError，再经
# python -m asset_catalog 的 __main__ 入口执行命令，
# 从而确定性地复现“状态读取权限不足”，不依赖系统权限配置。
INJECT_PERMISSION_ERROR_RUNNER = r"""
import os
import runpy
import sys

target = sys.argv[1]
sys.argv = ["asset_catalog"] + sys.argv[2:]

_real_stat = os.stat


def _patched_stat(path, *args, **kwargs):
    if os.fspath(path) == target:
        raise PermissionError(13, "Permission denied", os.fspath(path))
    return _real_stat(path, *args, **kwargs)


os.stat = _patched_stat
runpy.run_module("asset_catalog", run_name="__main__")
"""


class QueryFileStatusFilterRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "sample_a.bin"
        self.file_b = self.tmp_dir / "sample_b.bin"
        self.file_c = self.tmp_dir / "sample_c.bin"
        self.file_a.write_text("sample asset A\n", encoding="utf-8")
        self.file_b.write_text("sample asset B\n", encoding="utf-8")
        self.file_c.write_text("sample asset C\n", encoding="utf-8")
        self.db_path = self.tmp_dir / "catalog.sqlite"

        self.expected_a = {
            "path": os.path.realpath(str(self.file_a)),
            "type": "image",
            "tags": ["demo", "ui"],
        }
        self.expected_b = {
            "path": os.path.realpath(str(self.file_b)),
            "type": "audio",
            "tags": ["demo"],
        }
        self.expected_c = {
            "path": os.path.realpath(str(self.file_c)),
            "type": "image",
            "tags": ["ui", "demo"],
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

    def run_cli_with_permission_error(self, target_path, *args):
        """以全新进程运行同一入口，但对 target_path 的状态读取注入 PermissionError。"""
        cmd = [
            sys.executable,
            "-c",
            INJECT_PERMISSION_ERROR_RUNNER,
            target_path,
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

    def register_samples(self):
        """依次登记 A、B、C（add 仅用于准备数据，非本次测试对象）。"""
        adds = [
            (self.file_a, "image", ["demo", "ui"]),
            (self.file_b, "audio", ["demo"]),
            (self.file_c, "image", ["ui", "demo"]),
        ]
        for path, asset_type, tags in adds:
            args = ["add", str(path), "--type", asset_type]
            for tag in tags:
                args += ["--tag", tag]
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")

    def assertQueryOk(self, query_args, expected):
        """查询成功：退出码 0、标准错误为空、标准输出仅为预期 JSON 数组。"""
        result = self.run_cli("query", "--tag", "demo", *query_args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        self.assertEqual(data, expected)
        return data

    def remove_b_and_replace_c_with_dir(self):
        """删除 B；将 C 原路径替换为空目录。"""
        self.file_b.unlink()
        self.assertFalse(os.path.exists(self.expected_b["path"]))
        self.file_c.unlink()
        self.file_c.mkdir()
        self.assertTrue(os.path.isdir(self.expected_c["path"]))

    @staticmethod
    def with_status(record, status):
        """返回附加 file_status 字段的预期记录副本。"""
        return {**record, "file_status": status}

    def test_missing_filter_returns_only_deleted_record(self):
        self.register_samples()
        self.remove_b_and_replace_c_with_dir()

        # 单独使用 --file-status：只返回 B 的原登记记录，仅含 path、type、tags。
        data = self.assertQueryOk(["--file-status", "missing"], [self.expected_b])
        self.assertEqual(len(data), 1)
        self.assertNotIn("file_status", data[0])

        # 同时传 --check-files：该记录追加值为 missing 的 file_status。
        data = self.assertQueryOk(
            ["--file-status", "missing", "--check-files"],
            [self.with_status(self.expected_b, "missing")],
        )
        self.assertEqual(data[0]["file_status"], "missing")

    def test_present_and_not_file_filters(self):
        self.register_samples()
        self.remove_b_and_replace_c_with_dir()

        self.assertQueryOk(["--file-status", "present"], [self.expected_a])
        self.assertQueryOk(["--file-status", "not_file"], [self.expected_c])
        self.assertQueryOk(
            ["--file-status", "not_file", "--check-files"],
            [self.with_status(self.expected_c, "not_file")],
        )

    def test_restored_file_no_longer_matches_missing(self):
        self.register_samples()
        self.remove_b_and_replace_c_with_dir()
        self.assertQueryOk(["--file-status", "missing"], [self.expected_b])

        # 恢复 B 为普通文件后，新进程执行相同查询返回 []，无需重新登记。
        self.file_b.write_text("sample asset B\n", encoding="utf-8")
        self.assertQueryOk(["--file-status", "missing"], [])

    def test_filter_combines_with_type_and_tag_mode_any(self):
        self.register_samples()
        self.remove_b_and_replace_c_with_dir()

        # 先按标签与类型确定候选，再筛选状态。
        self.assertQueryOk(
            ["--type", "image", "--file-status", "not_file"], [self.expected_c]
        )
        self.assertQueryOk(
            ["--type", "image", "--file-status", "missing"], []
        )
        # any 模式下命中任一标签的素材进入候选，再按状态筛选。
        result = self.run_cli(
            "query",
            "--tag-mode",
            "any",
            "--tag",
            "demo",
            "--tag",
            "ui",
            "--file-status",
            "not_file",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [self.expected_c])

    def test_no_match_and_unregistered_tag_return_empty_array(self):
        self.register_samples()

        # 全部候选均为普通文件：missing / not_file 无匹配输出 []。
        self.assertQueryOk(["--file-status", "missing"], [])
        self.assertQueryOk(["--file-status", "not_file"], [])

        # 未命中标签：候选为空，输出 []。
        result = self.run_cli(
            "query", "--tag", "no_such_tag", "--file-status", "missing"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [])

    def test_default_query_behavior_unchanged(self):
        self.register_samples()
        self.remove_b_and_replace_c_with_dir()

        # 不传新选项：返回原有元数据，不含 file_status，不按状态筛选。
        data = self.assertQueryOk(
            [], [self.expected_a, self.expected_b, self.expected_c]
        )
        for record in data:
            self.assertNotIn("file_status", record)

        # 两项都未启用时不检查文件状态：对 B 注入权限错误查询仍成功。
        ok = self.run_cli_with_permission_error(
            self.expected_b["path"], "query", "--tag", "demo"
        )
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(ok.stderr, "")
        self.assertEqual(
            json.loads(ok.stdout),
            [self.expected_a, self.expected_b, self.expected_c],
        )

    def test_invalid_file_status_values_rejected(self):
        self.register_samples()
        db_bytes_before = self.db_path.read_bytes()

        invalid_cases = [
            ("--file-status",),  # 缺值
            ("--file-status", ""),  # 为空
            ("--file-status", " present"),  # 含首尾空白
            ("--file-status", "missing "),  # 含首尾空白
            ("--file-status", "Present"),  # 大小写不符
            ("--file-status", "MISSING"),  # 大小写不符
            ("--file-status", "gone"),  # 其他值
        ]
        for extra in invalid_cases:
            with self.subTest(extra=extra):
                result = self.run_cli("query", "--tag", "demo", *extra)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("--file-status", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

        # 参数错误不改动数据库。
        self.assertEqual(self.db_path.read_bytes(), db_bytes_before)

    def test_invalid_file_status_does_not_create_database(self):
        missing_db = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(missing_db.exists())

        cmd = [
            sys.executable,
            "-m",
            "asset_catalog",
            "--db",
            str(missing_db),
            "query",
            "--tag",
            "demo",
            "--file-status",
            "Present",
        ]
        result = subprocess.run(
            cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--file-status", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(missing_db.exists())

    def test_permission_error_fails_whole_query(self):
        self.register_samples()

        # 对候选 B 的状态读取注入 PermissionError：A 已检查成功，
        # 整次查询仍失败，退出码 2、标准输出为空，不输出部分结果。
        failed = self.run_cli_with_permission_error(
            self.expected_b["path"],
            "query",
            "--tag",
            "demo",
            "--file-status",
            "present",
        )
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("无法确定文件状态", failed.stderr)
        self.assertIn(self.expected_b["path"], failed.stderr)
        self.assertNotIn("Traceback", failed.stderr)

        # 失败后普通查询的元数据不变。
        self.assertQueryOk(
            [], [self.expected_a, self.expected_b, self.expected_c]
        )

    def test_non_candidates_are_not_status_checked(self):
        self.register_samples()

        # D 只带 other 标签，不命中查询条件：对其注入权限错误不影响查询。
        file_d = self.tmp_dir / "sample_d.bin"
        file_d.write_text("sample asset D\n", encoding="utf-8")
        result = self.run_cli(
            "add", str(file_d), "--type", "image", "--tag", "other"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        ok = self.run_cli_with_permission_error(
            os.path.realpath(str(file_d)),
            "query",
            "--tag",
            "demo",
            "--file-status",
            "present",
        )
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(ok.stderr, "")
        self.assertEqual(
            json.loads(ok.stdout),
            [self.expected_a, self.expected_b, self.expected_c],
        )

        # 类型不匹配的素材同样不检查状态：对 audio 类型的 B 注入权限错误，
        # --type image 的筛选查询仍成功。
        ok = self.run_cli_with_permission_error(
            self.expected_b["path"],
            "query",
            "--tag",
            "demo",
            "--type",
            "image",
            "--file-status",
            "present",
        )
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(
            json.loads(ok.stdout), [self.expected_a, self.expected_c]
        )

    def test_query_does_not_modify_records_or_files(self):
        self.register_samples()
        self.remove_b_and_replace_c_with_dir()
        db_bytes_before = self.db_path.read_bytes()

        self.assertQueryOk(["--file-status", "missing"], [self.expected_b])
        self.assertQueryOk(["--file-status", "present"], [self.expected_a])

        # 查询不保存状态、不改动记录和源文件。
        self.assertEqual(self.db_path.read_bytes(), db_bytes_before)
        self.assertEqual(
            self.file_a.read_text(encoding="utf-8"), "sample asset A\n"
        )
        self.assertQueryOk(
            [], [self.expected_a, self.expected_b, self.expected_c]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
