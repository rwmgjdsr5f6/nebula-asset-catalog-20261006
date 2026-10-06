"""query --check-files 文件状态查询的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

覆盖：
- 依次登记 A（image，标签 demo、ui）、B（audio，标签 demo）、
  C（image，标签 ui、demo）后，``query --tag demo --check-files``
  按登记顺序各返回一次，file_status 均为 present，
  规范绝对路径、类型与完整标签顺序保持不变；
- 删除 B、将 C 原路径替换为空目录后，同一查询依次报告
  present、missing、not_file，三条记录仍在；
- 不传 --check-files 时返回原有元数据，不含 file_status；
- 加 --type image 时只返回 A、C，保留状态与顺序；
- 查询未登记标签返回 []；
- 在 B 原路径重新创建普通文件后，新查询进程报告 B 为 present，无需重新登记；
- 状态读取出现 PermissionError 时（子进程内确定性注入，不依赖系统权限配置），
  即使前一条记录已检查成功，整次查询仍退出码 2、标准输出为空、
  标准错误指出状态读取失败及相关路径且不含调用栈；
  失败后普通查询的元数据不变；
- 查询成功时退出码 0、标准输出仅为 JSON 数组、标准错误为空；
- 查询不改变已登记记录，除样例主动变动的文件外素材内容保持原样。

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


class QueryFileStatusRegressionTest(unittest.TestCase):
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
        self.assertEqual(os.listdir(self.expected_c["path"]), [])

    @staticmethod
    def with_status(record, status):
        """返回附加 file_status 字段的预期记录副本。"""
        return {**record, "file_status": status}

    def test_check_files_all_present_after_registration(self):
        self.register_samples()

        data = self.assertQueryOk(
            ["--check-files"],
            [
                self.with_status(self.expected_a, "present"),
                self.with_status(self.expected_b, "present"),
                self.with_status(self.expected_c, "present"),
            ],
        )
        # 按登记顺序各返回一次。
        self.assertEqual(len(data), 3)
        # 规范绝对路径、类型与完整标签顺序保持不变。
        for record, expected in zip(
            data, (self.expected_a, self.expected_b, self.expected_c)
        ):
            self.assertTrue(os.path.isabs(record["path"]))
            self.assertEqual(record["path"], expected["path"])
            self.assertEqual(record["type"], expected["type"])
            self.assertEqual(record["tags"], expected["tags"])

    def test_present_missing_not_file_after_filesystem_changes(self):
        self.register_samples()
        self.remove_b_and_replace_c_with_dir()

        # 三条记录仍在，状态依次为 present、missing、not_file。
        data = self.assertQueryOk(
            ["--check-files"],
            [
                self.with_status(self.expected_a, "present"),
                self.with_status(self.expected_b, "missing"),
                self.with_status(self.expected_c, "not_file"),
            ],
        )
        self.assertEqual(len(data), 3)
        self.assertEqual(
            [record["file_status"] for record in data],
            ["present", "missing", "not_file"],
        )
        # 元数据（路径、类型、完整标签顺序）保持登记时的值。
        for record, expected in zip(
            data, (self.expected_a, self.expected_b, self.expected_c)
        ):
            self.assertEqual(record["path"], expected["path"])
            self.assertEqual(record["type"], expected["type"])
            self.assertEqual(record["tags"], expected["tags"])

        # 重复查询结果一致：查询不改变已登记记录。
        self.assertQueryOk(["--check-files"], data)

    def test_default_query_has_no_file_status(self):
        self.register_samples()
        self.remove_b_and_replace_c_with_dir()

        # 不传 --check-files：返回原有元数据，均不含 file_status。
        data = self.assertQueryOk(
            [], [self.expected_a, self.expected_b, self.expected_c]
        )
        for record in data:
            self.assertNotIn("file_status", record)

    def test_type_filter_keeps_status_and_order(self):
        self.register_samples()
        self.remove_b_and_replace_c_with_dir()

        # --type image 只返回 A、C，保留状态与登记顺序。
        data = self.assertQueryOk(
            ["--type", "image", "--check-files"],
            [
                self.with_status(self.expected_a, "present"),
                self.with_status(self.expected_c, "not_file"),
            ],
        )
        self.assertEqual(len(data), 2)
        self.assertEqual(
            [record["path"] for record in data],
            [self.expected_a["path"], self.expected_c["path"]],
        )

    def test_unregistered_tag_returns_empty_array(self):
        self.register_samples()

        result = self.run_cli("query", "--tag", "no_such_tag", "--check-files")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [])

    def test_recreated_file_reported_present_by_fresh_process(self):
        self.register_samples()
        self.remove_b_and_replace_c_with_dir()

        # 在 B 原路径重新创建普通文件，无需重新登记。
        self.file_b.write_text("sample asset B\n", encoding="utf-8")

        # run_cli 每次启动全新解释器进程；新查询进程报告 B 为 present。
        self.assertQueryOk(
            ["--check-files"],
            [
                self.with_status(self.expected_a, "present"),
                self.with_status(self.expected_b, "present"),
                self.with_status(self.expected_c, "not_file"),
            ],
        )

    def test_permission_error_fails_whole_query(self):
        self.register_samples()

        # 对 B 的状态读取注入 PermissionError：A 已检查成功，
        # 整次查询仍失败，退出码 2、标准输出为空。
        failed = self.run_cli_with_permission_error(
            self.expected_b["path"], "query", "--tag", "demo", "--check-files"
        )
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        # 标准错误指出状态读取失败及相关路径，不含调用栈。
        self.assertIn("无法确定文件状态", failed.stderr)
        self.assertIn(self.expected_b["path"], failed.stderr)
        self.assertNotIn("Traceback", failed.stderr)

        # 失败后普通查询的元数据不变，且不含 file_status。
        data = self.assertQueryOk(
            [], [self.expected_a, self.expected_b, self.expected_c]
        )
        for record in data:
            self.assertNotIn("file_status", record)

    def test_sample_files_unchanged_by_queries(self):
        self.register_samples()
        self.assertQueryOk(
            ["--check-files"],
            [
                self.with_status(self.expected_a, "present"),
                self.with_status(self.expected_b, "present"),
                self.with_status(self.expected_c, "present"),
            ],
        )

        # 除样例主动变动的文件外（本用例不做任何变动），素材内容保持原样。
        self.assertEqual(
            self.file_a.read_text(encoding="utf-8"), "sample asset A\n"
        )
        self.assertEqual(
            self.file_b.read_text(encoding="utf-8"), "sample asset B\n"
        )
        self.assertEqual(
            self.file_c.read_text(encoding="utf-8"), "sample asset C\n"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
