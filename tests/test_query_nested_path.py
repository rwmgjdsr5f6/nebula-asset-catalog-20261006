"""query 在登记路径的中间组件不再是目录时的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

固定样例只含两条素材，按 A、B 顺序登记：两者类型均为 ``image``，
标签顺序均为 ``demo``、``ui``。A 位于临时目录的 ``nested/a.bin``，
B 位于同一临时目录的 ``b.bin``。登记后移除 A 及其父目录 ``nested``，
再在 ``nested`` 原位置创建一个普通文件（中间组件被替换为非目录），
B 保持原样。

覆盖：
- ``query --tag demo --check-files`` 按登记顺序返回 A、B，
  A 的 file_status 为 missing（中间组件不是目录按 missing 归类，
  不判为 not_file，也不构成查询错误），B 为 present；
  路径、类型与完整标签顺序与登记结果一致；
- ``query --tag demo --file-status missing`` 只返回 A 的原元数据，
  不附加 file_status 字段；加 ``--check-files`` 后仍只返回 A 并附加 missing；
- 中间组件被替换期间，不带状态选项的 ``query --tag demo``
  仍返回原有 A、B 两条记录且不含 file_status；
- 恢复 ``nested`` 目录及 ``a.bin``（不重新登记）后，由新进程执行
  missing 筛选得到 ``[]``，带 ``--check-files`` 的标签查询中两条素材
  均显示 present；
- 以上查询退出码均为 0、标准错误为空、标准输出仅为一个 JSON 数组；
- 各次查询不改变登记元数据（export 结果前后一致），
  不改写 B 或替代目录的普通文件内容。

每个用例使用独立的临时目录与数据库，只创建/清理自己的样例文件，
重复执行不依赖上次残留文件。
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
CONTENT_A = "sample asset A\n"
CONTENT_B = "sample asset B\n"
CONTENT_PLACEHOLDER = "placeholder replacing nested dir\n"


class QueryNestedPathRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.nested_dir = self.tmp_dir / "nested"
        self.nested_dir.mkdir()
        self.file_a = self.nested_dir / "a.bin"
        self.file_b = self.tmp_dir / "b.bin"
        self.file_a.write_text(CONTENT_A, encoding="utf-8")
        self.file_b.write_text(CONTENT_B, encoding="utf-8")
        self.db_path = self.tmp_dir / "catalog.sqlite"

        self.expected_a = {
            "path": os.path.realpath(str(self.file_a)),
            "type": "image",
            "tags": ["demo", "ui"],
        }
        self.expected_b = {
            "path": os.path.realpath(str(self.file_b)),
            "type": "image",
            "tags": ["demo", "ui"],
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
        """依次登记 A、B（add 仅用于准备数据，非本次测试对象）。"""
        for path, expected in (
            (self.file_a, self.expected_a),
            (self.file_b, self.expected_b),
        ):
            result = self.run_cli(
                "add",
                str(path),
                "--type",
                "image",
                "--tag",
                "demo",
                "--tag",
                "ui",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            # 登记回执与后续查询应返回一致的元数据。
            self.assertEqual(json.loads(result.stdout), expected)

    def assertQueryOk(self, query_args, expected):
        """查询成功：退出码 0、标准错误为空、标准输出仅为预期 JSON 数组。"""
        result = self.run_cli("query", "--tag", "demo", *query_args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        self.assertEqual(data, expected)
        return data

    def export_records(self):
        """由新进程导出完整目录，用于核对登记元数据未变。"""
        result = self.run_cli("export")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def replace_nested_with_file(self):
        """移除 A 及其父目录 nested，再在 nested 原位置创建普通文件。"""
        self.file_a.unlink()
        self.nested_dir.rmdir()
        self.assertFalse(os.path.exists(self.expected_a["path"]))
        self.nested_dir.write_text(CONTENT_PLACEHOLDER, encoding="utf-8")
        self.assertTrue(self.nested_dir.is_file())

    def restore_nested_dir(self):
        """移除替代文件，恢复 nested 目录及 a.bin（不重新登记）。"""
        self.nested_dir.unlink()
        self.nested_dir.mkdir()
        self.file_a.write_text(CONTENT_A, encoding="utf-8")

    @staticmethod
    def with_status(record, status):
        """返回附加 file_status 字段的预期记录副本。"""
        return {**record, "file_status": status}

    def test_check_files_marks_a_missing_b_present(self):
        self.register_samples()
        self.replace_nested_with_file()

        # 按登记顺序返回 A、B；中间组件不是目录时 A 归为 missing 而非
        # not_file，也不构成查询错误。
        data = self.assertQueryOk(
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
        # 路径、类型与完整标签顺序与登记结果一致。
        for record, expected in zip(data, (self.expected_a, self.expected_b)):
            self.assertEqual(record["path"], expected["path"])
            self.assertEqual(record["type"], expected["type"])
            self.assertEqual(record["tags"], expected["tags"])

        # 重复查询结果一致：查询不改变已登记记录。
        self.assertQueryOk(["--check-files"], data)

    def test_file_status_missing_filter_with_and_without_check_files(self):
        self.register_samples()
        self.replace_nested_with_file()

        # 不带 --check-files：只返回 A 的原元数据，不附加状态字段。
        data = self.assertQueryOk(
            ["--file-status", "missing"], [self.expected_a]
        )
        self.assertEqual(len(data), 1)
        self.assertNotIn("file_status", data[0])

        # 加 --check-files：仍只返回 A，并附加 missing。
        data = self.assertQueryOk(
            ["--file-status", "missing", "--check-files"],
            [self.with_status(self.expected_a, "missing")],
        )
        self.assertEqual(data[0]["file_status"], "missing")

    def test_plain_tag_query_unchanged_while_nested_replaced(self):
        self.register_samples()
        self.replace_nested_with_file()

        # 不带状态选项的标签查询仍返回原有 A、B 两条记录，不含 file_status。
        data = self.assertQueryOk([], [self.expected_a, self.expected_b])
        for record in data:
            self.assertNotIn("file_status", record)

    def test_restore_nested_dir_reports_present_in_fresh_process(self):
        self.register_samples()
        self.replace_nested_with_file()
        self.restore_nested_dir()

        # run_cli 每次启动全新解释器进程：恢复后 missing 筛选得到 []。
        self.assertQueryOk(["--file-status", "missing"], [])

        # 带 --check-files 的标签查询中两条素材都显示 present。
        self.assertQueryOk(
            ["--check-files"],
            [
                self.with_status(self.expected_a, "present"),
                self.with_status(self.expected_b, "present"),
            ],
        )

    def test_queries_do_not_modify_metadata_or_sample_files(self):
        self.register_samples()
        # 登记后的完整目录快照与数据库字节，用于核对查询不产生任何写入。
        records_before = self.export_records()
        db_bytes_before = self.db_path.read_bytes()

        self.replace_nested_with_file()

        # 依次执行本组覆盖的全部查询形态。
        self.assertQueryOk(
            ["--check-files"],
            [
                self.with_status(self.expected_a, "missing"),
                self.with_status(self.expected_b, "present"),
            ],
        )
        self.assertQueryOk(["--file-status", "missing"], [self.expected_a])
        self.assertQueryOk(
            ["--file-status", "missing", "--check-files"],
            [self.with_status(self.expected_a, "missing")],
        )
        self.assertQueryOk([], [self.expected_a, self.expected_b])

        # 登记元数据不变，数据库文件字节不变。
        self.assertEqual(self.export_records(), records_before)
        self.assertEqual(self.db_path.read_bytes(), db_bytes_before)

        # B 与替代目录的普通文件内容不被改写。
        self.assertEqual(self.file_b.read_text(encoding="utf-8"), CONTENT_B)
        self.assertEqual(
            self.nested_dir.read_text(encoding="utf-8"), CONTENT_PLACEHOLDER
        )

        # 恢复 nested 后再次核对：A 内容如旧，查询仍不改动任何状态。
        self.restore_nested_dir()
        self.assertQueryOk(
            ["--check-files"],
            [
                self.with_status(self.expected_a, "present"),
                self.with_status(self.expected_b, "present"),
            ],
        )
        self.assertEqual(self.export_records(), records_before)
        self.assertEqual(self.db_path.read_bytes(), db_bytes_before)
        self.assertEqual(self.file_a.read_text(encoding="utf-8"), CONTENT_A)
        self.assertEqual(self.file_b.read_text(encoding="utf-8"), CONTENT_B)


if __name__ == "__main__":
    unittest.main(verbosity=2)
