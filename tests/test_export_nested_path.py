"""export 在登记路径的中间组件不再是目录时的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

固定样例只含两条素材，按 A、B 顺序登记：A 位于临时目录的
``nested/A.bin``，类型为 ``image``，标签按 ``demo``、``ui`` 保存；
B 位于同一临时目录的 ``B.bin``，类型为 ``audio``，标签为 ``demo``。
登记后删除 A 及空的 ``nested`` 目录，再在 ``nested`` 原位置放置一个
普通文件（中间组件被替换为非目录），B 保持原样。

覆盖：
- ``export --check-files`` 退出码 0、标准错误为空、标准输出仅一行 JSON
  数组；按首次登记顺序各返回 A、B 一次，A 的 file_status 为 missing
  （中间组件不是目录按 missing 归类，不判为 not_file，也不导致整次导出
  失败），B 为 present；每条记录除追加 file_status 外，规范绝对路径、
  类型、完整标签及其顺序均与登记结果一致；
- 同一状态下不带选项的 ``export`` 仍返回 A、B 两条完整元数据记录，
  且不出现 file_status 字段；
- 移除替代文件、恢复 ``nested`` 目录及 A 的原始内容（不重新登记）后，
  由新进程执行 ``export --check-files``，两条记录均报告 present，
  原有元数据与排列顺序不变；
- 目录被替换之后、导出之前记录数据库、B 与替代文件的字节，确认该阶段
  两种导出都不改写它们；恢复后再次导出也不改变数据库与两个素材文件。

每个用例使用独立的临时目录与数据库，只创建/清理自己的样例文件，
不依赖外部素材、符号链接或权限配置，重复执行结论一致。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 样例文件固定内容：A 与其父目录会被移除再恢复，B 与替代文件始终只读。
CONTENT_A = b"demo asset A (nested)\n"
CONTENT_B = b"demo asset B\n"
CONTENT_PLACEHOLDER = b"placeholder replacing nested dir\n"


class ExportNestedPathRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.nested_dir = self.tmp_dir / "nested"
        self.nested_dir.mkdir()
        self.file_a = self.nested_dir / "A.bin"
        self.file_b = self.tmp_dir / "B.bin"
        self.file_a.write_bytes(CONTENT_A)
        self.file_b.write_bytes(CONTENT_B)
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

    def register_samples(self):
        """按 A、B 顺序登记（add 仅用于准备数据，非本次测试对象）。"""
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
        # 登记回执与后续导出应返回一致的元数据。
        self.assertEqual(json.loads(result_a.stdout), self.expected_a)

        result_b = self.run_cli(
            "add",
            str(self.file_b),
            "--type",
            "audio",
            "--tag",
            "demo",
        )
        self.assertEqual(result_b.returncode, 0, result_b.stderr)
        self.assertEqual(result_b.stderr, "")
        self.assertEqual(json.loads(result_b.stdout), self.expected_b)

    def assertExportOk(self, export_args, expected):
        """导出成功：退出码 0、标准错误为空、标准输出仅一行预期 JSON 数组。"""
        result = self.run_cli("export", *export_args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        # 标准输出仅为单行 JSON（结尾一个换行符）。
        self.assertEqual(result.stdout.count("\n"), 1)
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        self.assertEqual(data, expected)
        return data

    def replace_nested_with_file(self):
        """删除 A 及空的 nested 目录，再在 nested 原位置放置普通文件。"""
        self.file_a.unlink()
        self.nested_dir.rmdir()
        self.assertFalse(os.path.exists(self.expected_a["path"]))
        self.nested_dir.write_bytes(CONTENT_PLACEHOLDER)
        self.assertTrue(self.nested_dir.is_file())

    def restore_nested_dir(self):
        """移除替代文件，恢复 nested 目录及 A 的原始内容（不重新登记）。"""
        self.nested_dir.unlink()
        self.nested_dir.mkdir()
        self.file_a.write_bytes(CONTENT_A)

    @staticmethod
    def with_status(record, status):
        """返回附加 file_status 字段的预期记录副本。"""
        return {**record, "file_status": status}

    def test_check_files_marks_a_missing_b_present(self):
        self.register_samples()
        self.replace_nested_with_file()

        # 按首次登记顺序各返回 A、B 一次；中间组件不是目录时 A 归为
        # missing 而非 not_file，也不导致整次导出失败。
        data = self.assertExportOk(
            ["--check-files"],
            [
                self.with_status(self.expected_a, "missing"),
                self.with_status(self.expected_b, "present"),
            ],
        )
        self.assertEqual(len(data), 2)
        self.assertEqual(
            [record["file_status"] for record in data], ["missing", "present"]
        )
        # 除追加 file_status 外，规范绝对路径、类型、完整标签及其顺序
        # 均与登记结果一致。
        for record, expected in zip(data, (self.expected_a, self.expected_b)):
            self.assertEqual(record["path"], expected["path"])
            self.assertTrue(os.path.isabs(record["path"]))
            self.assertEqual(record["type"], expected["type"])
            self.assertEqual(record["tags"], expected["tags"])

        # 重复执行同一导出结论一致：导出不改变已登记记录。
        self.assertExportOk(["--check-files"], data)

    def test_plain_export_unchanged_while_nested_replaced(self):
        self.register_samples()
        self.replace_nested_with_file()

        # 同一状态下不带选项的导出仍返回 A、B 两条完整元数据记录，
        # 且不出现 file_status 字段。
        data = self.assertExportOk([], [self.expected_a, self.expected_b])
        self.assertEqual(len(data), 2)
        for record in data:
            self.assertEqual(set(record), {"path", "type", "tags"})
            self.assertNotIn("file_status", record)

    def test_restore_nested_dir_reports_present_in_fresh_process(self):
        self.register_samples()
        self.replace_nested_with_file()
        self.restore_nested_dir()

        # run_cli 每次启动全新解释器进程：恢复后两条记录均报告 present，
        # 原有元数据与排列顺序不变。
        data = self.assertExportOk(
            ["--check-files"],
            [
                self.with_status(self.expected_a, "present"),
                self.with_status(self.expected_b, "present"),
            ],
        )
        self.assertEqual(len(data), 2)

        # 不带选项的导出同样保持原有记录与顺序。
        self.assertExportOk([], [self.expected_a, self.expected_b])

    def test_exports_do_not_modify_database_or_sample_files(self):
        self.register_samples()
        self.replace_nested_with_file()

        # 目录被替换之后、导出之前记录数据库、B 与替代文件的字节。
        db_bytes_before = self.db_path.read_bytes()
        b_bytes_before = self.file_b.read_bytes()
        placeholder_bytes_before = self.nested_dir.read_bytes()

        # 该阶段的两种导出都不改写它们。
        self.assertExportOk(
            ["--check-files"],
            [
                self.with_status(self.expected_a, "missing"),
                self.with_status(self.expected_b, "present"),
            ],
        )
        self.assertExportOk([], [self.expected_a, self.expected_b])
        self.assertEqual(self.db_path.read_bytes(), db_bytes_before)
        self.assertEqual(self.file_b.read_bytes(), b_bytes_before)
        self.assertEqual(self.nested_dir.read_bytes(), placeholder_bytes_before)

        # 恢复 nested 目录及 A 的原始内容（不重新登记）后再次导出：
        # 两条记录均报告 present，数据库与两个素材文件均不被改写。
        self.restore_nested_dir()
        self.assertExportOk(
            ["--check-files"],
            [
                self.with_status(self.expected_a, "present"),
                self.with_status(self.expected_b, "present"),
            ],
        )
        self.assertExportOk([], [self.expected_a, self.expected_b])
        self.assertEqual(self.db_path.read_bytes(), db_bytes_before)
        self.assertEqual(self.file_a.read_bytes(), CONTENT_A)
        self.assertEqual(self.file_b.read_bytes(), CONTENT_B)


if __name__ == "__main__":
    unittest.main(verbosity=2)
