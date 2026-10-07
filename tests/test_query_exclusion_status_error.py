"""被排除路径的文件状态读取错误不影响查询的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

背景：现有排除测试以“被排除文件已删除”验证被排除路径不参与状态检查；
本模块用确定性的 PermissionError 注入保护同一公开行为的错误侧面——
被排除路径在状态读取时固定失败，查询本身不受影响，并以取消排除后的
失败结果作对照。

样例（临时目录中按 A.bin、B.bin、C.bin 顺序登记为 image）：
- A.bin：标签 ["demo", "ui"]
- B.bin：标签 ["demo", "archived"]
- C.bin：标签 ["ui"]

三个文件始终是普通文件，场景不依赖实际权限配置：仅在查询子进程内对
B.bin 的规范登记路径注入 os.stat -> PermissionError，其余路径正常。

覆盖：
- any 命中 demo 或 ui 再排除 archived 时，B 不进入候选，其状态读取错误
  不影响查询：仅 --check-files 时退出码 0、标准错误为空，按 A、C 顺序
  返回并追加值为 present 的 file_status；仅 --file-status present 时仍
  返回 A、C，记录只含 path、type、tags；两者同用时返回 A、C 并附加
  present 状态。比较解析后的 JSON，不依赖键序或空白；
- 取消 --exclude-tag 后同样的错误条件下，仅附加状态、仅筛选 present、
  两者同用均退出码 2、标准输出为空（不返回已处理成功的 A），标准错误
  说明状态读取失败并包含 B 的规范路径，且不含调用栈；
- 每个用例结束后由新进程 export 核对 A、B、C 全部登记记录及顺序未变，
  数据库文件与三个源文件的字节保持不变；临时数据由测试自行清理。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

CONTENT_A = b"sample asset A\n"
CONTENT_B = b"sample asset B\n"
CONTENT_C = b"sample asset C\n"

# 在子进程中对指定路径的 os.stat 注入 PermissionError，再经
# python -m asset_catalog 的 __main__ 入口执行命令，
# 从而确定性地复现“状态读取权限不足”，不依赖系统权限配置。
# 目标文件本身始终是普通文件，注入只影响该进程内的状态读取。
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


class QueryExclusionStatusErrorRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "A.bin"
        self.file_b = self.tmp_dir / "B.bin"
        self.file_c = self.tmp_dir / "C.bin"
        self.file_a.write_bytes(CONTENT_A)
        self.file_b.write_bytes(CONTENT_B)
        self.file_c.write_bytes(CONTENT_C)
        self.db_path = self.tmp_dir / "catalog.sqlite"

        self.expected_a = {
            "path": os.path.realpath(str(self.file_a)),
            "type": "image",
            "tags": ["demo", "ui"],
        }
        self.expected_b = {
            "path": os.path.realpath(str(self.file_b)),
            "type": "image",
            "tags": ["demo", "archived"],
        }
        self.expected_c = {
            "path": os.path.realpath(str(self.file_c)),
            "type": "image",
            "tags": ["ui"],
        }

        self.register_samples()

        # 登记完成后固化数据库与源文件字节，每个用例后逐字节核对不变。
        self.db_bytes_after_register = self.db_path.read_bytes()
        self.file_bytes_after_register = {
            self.expected_a["path"]: CONTENT_A,
            self.expected_b["path"]: CONTENT_B,
            self.expected_c["path"]: CONTENT_C,
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
        """按 A.bin、B.bin、C.bin 顺序登记三个 image 文件。

        A：标签 demo、ui；B：标签 demo、archived；C：标签 ui。
        add 仅用于准备数据，在不注入错误的独立进程中完成。
        """
        registrations = [
            (self.file_a, ["demo", "ui"]),
            (self.file_b, ["demo", "archived"]),
            (self.file_c, ["ui"]),
        ]
        for path, tags in registrations:
            args = ["add", str(path), "--type", "image"]
            for tag in tags:
                args += ["--tag", tag]
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")

    @staticmethod
    def with_status(record, status):
        """返回附加 file_status 字段的预期记录副本。"""
        return {**record, "file_status": status}

    def base_query_args(self, *, exclude_archived):
        """any 命中 demo 或 ui；按需排除 archived。"""
        args = [
            "query",
            "--tag-mode",
            "any",
            "--tag",
            "demo",
            "--tag",
            "ui",
        ]
        if exclude_archived:
            args += ["--exclude-tag", "archived"]
        return args

    def assertExportAndBytesIntact(self):
        """新进程 export 核对全部登记记录及顺序；数据库与源文件字节不变。"""
        result = self.run_cli("export")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        exported = json.loads(result.stdout)
        self.assertEqual(
            exported,
            [self.expected_a, self.expected_b, self.expected_c],
        )
        # 显式锁定首次登记顺序：失败的查询不得删改或重排任何记录。
        self.assertEqual(
            [record["path"] for record in exported],
            [
                self.expected_a["path"],
                self.expected_b["path"],
                self.expected_c["path"],
            ],
        )
        for record in exported:
            self.assertEqual(set(record), {"path", "type", "tags"})

        self.assertEqual(
            self.db_path.read_bytes(), self.db_bytes_after_register
        )
        for path, content in self.file_bytes_after_register.items():
            self.assertEqual(Path(path).read_bytes(), content)
        # 三个源文件始终是普通文件（错误只来自注入，不改变文件本身）。
        for path in self.file_bytes_after_register:
            self.assertTrue(os.path.isfile(path))

    def test_excluded_path_status_error_does_not_affect_query(self):
        """排除 archived 后 B 不进入候选：其 PermissionError 不影响查询。"""
        present_a = self.with_status(self.expected_a, "present")
        present_c = self.with_status(self.expected_c, "present")

        # 三种文件状态选项用法分别核对：是否附加字段、字段取值、记录形状。
        cases = [
            (
                "仅 --check-files：附加 present，不筛选",
                ["--check-files"],
                [present_a, present_c],
                {"path", "type", "tags", "file_status"},
            ),
            (
                "仅 --file-status present：筛选但不附加字段",
                ["--file-status", "present"],
                [self.expected_a, self.expected_c],
                {"path", "type", "tags"},
            ),
            (
                "--check-files 与 --file-status present 同用",
                ["--check-files", "--file-status", "present"],
                [present_a, present_c],
                {"path", "type", "tags", "file_status"},
            ),
        ]

        for label, extra_args, expected_records, expected_keys in cases:
            with self.subTest(label):
                result = self.run_cli_with_permission_error(
                    self.expected_b["path"],
                    *self.base_query_args(exclude_archived=True),
                    *extra_args,
                )
                # B 的状态错误不可见：退出码 0、标准错误为空。
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")

                data = json.loads(result.stdout)
                self.assertIsInstance(data, list)
                # 比较解析后的 JSON，不依赖对象键序或文本空白。
                self.assertEqual(data, expected_records)
                # 输出按 A、C 的首次登记顺序排列，每条只出现一次。
                self.assertEqual(
                    [record["path"] for record in data],
                    [self.expected_a["path"], self.expected_c["path"]],
                )
                for record in data:
                    self.assertEqual(set(record), expected_keys)
                if "file_status" in expected_keys:
                    self.assertTrue(
                        all(record["file_status"] == "present" for record in data)
                    )
                # 完整标签及其登记顺序原样保留。
                self.assertEqual(data[0]["tags"], ["demo", "ui"])
                self.assertEqual(data[-1]["tags"], ["ui"])
                # B 的路径既不出现在结果中，也不出现在诊断输出中。
                self.assertNotIn(self.expected_b["path"], result.stdout)
                self.assertNotIn(self.expected_b["path"], result.stderr)

            # 每个用例结束后由新进程核对全部登记记录、顺序与字节。
            self.assertExportAndBytesIntact()

    def test_without_exclusion_status_error_fails_whole_query(self):
        """取消 --exclude-tag 作对照：B 的 PermissionError 使整次查询失败。"""
        cases = [
            ("仅 --check-files", ["--check-files"]),
            ("仅 --file-status present", ["--file-status", "present"]),
            (
                "--check-files 与 --file-status present 同用",
                ["--check-files", "--file-status", "present"],
            ),
        ]

        for label, extra_args in cases:
            with self.subTest(label):
                result = self.run_cli_with_permission_error(
                    self.expected_b["path"],
                    *self.base_query_args(exclude_archived=False),
                    *extra_args,
                )
                # 候选顺序为 A、B、C：A 虽已成功读取状态，B 失败时仍整体
                # 失败，退出码 2、标准输出为空，不返回已处理的 A。
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertNotIn(self.expected_a["path"], result.stdout)
                # 标准错误说明状态读取失败、点名 B 的规范路径，且无调用栈。
                self.assertIn("无法确定文件状态", result.stderr)
                self.assertIn(self.expected_b["path"], result.stderr)
                self.assertNotIn("Traceback", result.stderr)

            # 失败用例同样不得改动任何登记记录、数据库或源文件。
            self.assertExportAndBytesIntact()


if __name__ == "__main__":
    unittest.main(verbosity=2)
