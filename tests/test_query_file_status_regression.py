"""query --check-files 文件状态查询的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

覆盖：
- 依次登记 A(image; demo, ui)、B(audio; demo)、C(image; ui, demo) 后，
  ``query --tag demo --check-files`` 按登记顺序各返回一次，file_status 均为
  present，规范绝对路径、类型与完整标签顺序保持不变；
- 删除 B、将 C 原路径替换为空目录后，同样查询依次得到
  present / missing / not_file，三条记录仍在且元数据不变；
- 不传 --check-files 时返回原有元数据，结果不含 file_status 字段；
- 附加 --type image 时只返回 A、C，状态与顺序保持；
- 查询未登记标签时返回 []；
- 在 B 原路径重新创建普通文件后，新查询进程报告 B 为 present，无需重新登记；
- 状态读取出现 PermissionError 时（子进程内确定性注入，不依赖系统权限
  配置），即使前一条记录已检查成功，整次查询仍退出码 2、标准输出为空、
  标准错误指出状态读取失败及相关路径且不含调用栈；失败后普通查询元数据不变；
- 成功查询退出码 0、标准错误为空、标准输出仅为一个 JSON 数组，
  按解析后的内容比较，不依赖键序或空白。

每个用例使用独立的临时目录与数据库，只创建/清理自己的样例文件；
除样例主动变动的文件外，素材内容保持原样，查询不改变已登记记录。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 在子进程内确定性注入 PermissionError：仅对目标路径让 os.stat 失败，
# 不依赖系统权限配置（对 root 运行同样有效）。其余路径正常统计，
# 之后交给 asset_catalog.cli 的正式入口完成整次查询。
PERMISSION_ERROR_DRIVER = """
import os
import sys

from asset_catalog import cli

target = sys.argv[1]
real_stat = os.stat


def fake_stat(path, *args, **kwargs):
    if os.fspath(path) == target:
        raise PermissionError(13, "Permission denied", os.fspath(path))
    return real_stat(path, *args, **kwargs)


os.stat = fake_stat
sys.exit(cli.main(sys.argv[2:]))
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

    def run_query_with_stat_failure(self, target_path, *args):
        """以全新进程运行查询，但对 target_path 的 os.stat 注入 PermissionError。"""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(PROJECT_ROOT)
        cmd = [
            sys.executable,
            "-c",
            PERMISSION_ERROR_DRIVER,
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
            env=env,
        )

    def register_samples(self):
        """依次登记 A、B、C（add 仅用于准备数据，非本次测试对象）。"""
        adds = [
            (self.file_a, "image", ["demo", "ui"]),
            (self.file_b, "audio", ["demo"]),
            (self.file_c, "image", ["ui", "demo"]),
        ]
        for path, asset_type, tags in adds:
            cmd = ["add", str(path), "--type", asset_type]
            for tag in tags:
                cmd += ["--tag", tag]
            result = self.run_cli(*cmd)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")

    @staticmethod
    def with_status(record, status):
        merged = dict(record)
        merged["file_status"] = status
        return merged

    def assertQueryOk(self, query_args, expected):
        """查询成功：退出码 0、标准错误为空、标准输出仅为预期 JSON 数组。"""
        result = self.run_cli("query", *query_args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        self.assertEqual(data, expected)
        return data

    def disturb_files(self):
        """删除 B，并将 C 原路径替换为空目录。"""
        self.file_b.unlink()
        self.assertFalse(self.file_b.exists())
        self.file_c.unlink()
        self.file_c.mkdir()
        self.assertTrue(self.file_c.is_dir())

    def test_check_files_all_present_after_registration(self):
        self.register_samples()

        data = self.assertQueryOk(
            ["--tag", "demo", "--check-files"],
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
            data, [self.expected_a, self.expected_b, self.expected_c]
        ):
            self.assertTrue(os.path.isabs(record["path"]))
            self.assertEqual(record["path"], expected["path"])
            self.assertEqual(record["type"], expected["type"])
            self.assertEqual(record["tags"], expected["tags"])

    def test_check_files_reports_missing_and_not_file(self):
        self.register_samples()
        self.disturb_files()

        data = self.assertQueryOk(
            ["--tag", "demo", "--check-files"],
            [
                self.with_status(self.expected_a, "present"),
                self.with_status(self.expected_b, "missing"),
                self.with_status(self.expected_c, "not_file"),
            ],
        )
        # 三条记录仍在，且已登记的元数据不变。
        self.assertEqual(len(data), 3)
        for record, expected in zip(
            data, [self.expected_a, self.expected_b, self.expected_c]
        ):
            self.assertEqual(record["path"], expected["path"])
            self.assertEqual(record["type"], expected["type"])
            self.assertEqual(record["tags"], expected["tags"])

    def test_query_without_check_files_omits_file_status(self):
        self.register_samples()
        self.disturb_files()

        # 不传 --check-files：返回原有元数据，均不含 file_status。
        data = self.assertQueryOk(
            ["--tag", "demo"],
            [self.expected_a, self.expected_b, self.expected_c],
        )
        for record in data:
            self.assertNotIn("file_status", record)

    def test_type_filter_keeps_status_and_order(self):
        self.register_samples()
        self.disturb_files()

        # --type image 只返回 A、C，状态与登记顺序保持。
        self.assertQueryOk(
            ["--tag", "demo", "--type", "image", "--check-files"],
            [
                self.with_status(self.expected_a, "present"),
                self.with_status(self.expected_c, "not_file"),
            ],
        )

    def test_unregistered_tag_returns_empty_array(self):
        self.register_samples()

        self.assertQueryOk(["--tag", "unknown", "--check-files"], [])
        self.assertQueryOk(["--tag", "unknown"], [])

    def test_recreated_file_reported_present_by_new_process(self):
        self.register_samples()

        # 删除 B 后，新查询进程报告 missing。
        self.file_b.unlink()
        self.assertQueryOk(
            ["--tag", "demo", "--check-files"],
            [
                self.with_status(self.expected_a, "present"),
                self.with_status(self.expected_b, "missing"),
                self.with_status(self.expected_c, "present"),
            ],
        )

        # 在 B 原路径重新创建普通文件，无需重新登记，
        # 又一个全新查询进程报告 B 为 present。
        self.file_b.write_text("sample asset B\n", encoding="utf-8")
        self.assertQueryOk(
            ["--tag", "demo", "--check-files"],
            [
                self.with_status(self.expected_a, "present"),
                self.with_status(self.expected_b, "present"),
                self.with_status(self.expected_c, "present"),
            ],
        )

    def test_status_read_permission_error_fails_entire_query(self):
        self.register_samples()

        # 对 B（第二条记录）注入 PermissionError：即使 A 已检查成功，
        # 整次查询仍失败，不输出部分结果。
        result = self.run_query_with_stat_failure(
            self.expected_b["path"], "query", "--tag", "demo", "--check-files"
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        # 标准错误指出状态读取失败及相关路径，不含调用栈。
        self.assertIn("状态", result.stderr)
        self.assertIn(self.expected_b["path"], result.stderr)
        self.assertNotIn("Traceback", result.stderr)

        # 失败后普通查询的元数据不变，且结果不含 file_status。
        data = self.assertQueryOk(
            ["--tag", "demo"],
            [self.expected_a, self.expected_b, self.expected_c],
        )
        for record in data:
            self.assertNotIn("file_status", record)


if __name__ == "__main__":
    unittest.main(verbosity=2)
