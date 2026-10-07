"""query 在登记路径的中间组件被普通文件替换时的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

覆盖边界：登记路径的某个中间目录暂时不存在或被普通文件占据时，
``os.stat`` 对完整路径抛出“中间组件不是目录”（NotADirectoryError），
公开规则把这种情况与路径不存在一并归为 ``missing``，
不能判为 ``not_file``，也不能使查询报错。

固定样例只含两条素材，按 A、B 顺序登记（add 仅用于准备数据）：
- A 位于临时目录的 ``nested/a.bin``，类型 image，标签依次为 demo、ui；
- B 位于同一临时目录的 ``b.bin``，类型 image，标签依次为 demo、ui。

登记后移除 A 及其父目录 ``nested``，再在 ``nested`` 原位置创建一个普通文件
（内容固定），B 保持原样。断言：
- ``query --tag demo --check-files`` 退出码 0、标准错误为空，按登记顺序
  返回 A、B，A 的 file_status 为 missing，B 为 present，
  路径、类型与完整标签顺序与登记结果一致；
- ``query --tag demo --file-status missing`` 只返回 A 的原元数据，
  仅含 path、type、tags，不附加状态字段；再加 --check-files
  仍只返回 A 并附加 missing；``--file-status not_file`` 返回 []，
  不把中间组件不是目录判为 not_file，也不是查询错误；
- 中间组件被替换期间，不带状态选项的标签查询仍返回原有两条记录，
  且不含 file_status；
- 用同一样例恢复 nested 目录及 a.bin（不重新登记）后，由新进程执行
  missing 筛选得到 []，带 --check-files 的标签查询中两条素材均为 present；
- 各次查询不改变登记元数据（export 仍原样返回 A、B），不改写 B 或
  替代 nested 的普通文件内容，数据库字节不变。

每个用例使用独立的临时目录与数据库，只创建/清理自己的样例文件，
重复执行不依赖上次残留。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

A_CONTENT = "sample asset A\n"
B_CONTENT = "sample asset B\n"
# 占据 nested 原位置的普通文件的固定内容。
REPLACEMENT_CONTENT = "replaced intermediate component\n"


class QueryNestedPathRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.nested_dir = self.tmp_dir / "nested"
        self.file_a = self.nested_dir / "a.bin"
        self.file_b = self.tmp_dir / "b.bin"

        self.nested_dir.mkdir()
        self.file_a.write_text(A_CONTENT, encoding="utf-8")
        self.file_b.write_text(B_CONTENT, encoding="utf-8")
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
        """先登记 A，再登记 B；两者类型均为 image，标签均为 demo、ui。"""
        for path in (self.file_a, self.file_b):
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

    def break_a_path(self):
        """移除 A 及其父目录 nested，再在 nested 原位置创建普通文件。"""
        self.file_a.unlink()
        self.file_a.parent.rmdir()
        self.assertFalse(self.nested_dir.exists())
        self.nested_dir.write_text(REPLACEMENT_CONTENT, encoding="utf-8")
        self.assertTrue(os.path.isfile(str(self.nested_dir)))
        self.assertFalse(os.path.exists(str(self.file_a)))
        # B 保持原样。
        self.assertTrue(os.path.isfile(str(self.file_b)))

    def restore_a_path(self):
        """用同一样例恢复 nested 目录及 a.bin（不重新登记）。"""
        self.nested_dir.unlink()
        self.nested_dir.mkdir()
        self.file_a.write_text(A_CONTENT, encoding="utf-8")
        self.assertTrue(os.path.isfile(str(self.file_a)))
        self.assertTrue(os.path.isfile(str(self.file_b)))

    @staticmethod
    def with_status(record, status):
        """返回附加 file_status 字段的预期记录副本。"""
        return {**record, "file_status": status}

    def assert_query_ok(self, extra_args, expected):
        """query --tag demo 成功：退出码 0、标准错误为空、输出仅为预期 JSON 数组。"""
        result = self.run_cli("query", "--tag", "demo", *extra_args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        # 标准输出仅为一个 JSON 数组（比较解析后的数据，不依赖空白格式）。
        self.assertTrue(result.stdout.lstrip().startswith("["))
        self.assertTrue(result.stdout.rstrip().endswith("]"))
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        self.assertEqual(data, expected)
        return data

    def test_check_files_reports_missing_when_intermediate_component_is_file(self):
        self.register_samples()
        self.break_a_path()

        expected = [
            self.with_status(self.expected_a, "missing"),
            self.with_status(self.expected_b, "present"),
        ]
        # 按登记顺序返回两条；中间组件不是目录归为 missing 而非 not_file。
        data = self.assert_query_ok(["--check-files"], expected)
        self.assertEqual(
            [record["file_status"] for record in data],
            ["missing", "present"],
        )

        # 路径、类型与完整标签顺序与登记结果一致。
        for record, original in zip(data, (self.expected_a, self.expected_b)):
            self.assertTrue(os.path.isabs(record["path"]))
            self.assertEqual(record["path"], original["path"])
            self.assertEqual(record["type"], original["type"])
            self.assertEqual(record["tags"], original["tags"])

        # 重复查询结论一致：查询不改变登记记录。
        self.assert_query_ok(["--check-files"], expected)

    def test_missing_filter_returns_only_a_original_metadata(self):
        self.register_samples()
        self.break_a_path()

        # 只返回 A 的原元数据，不附加 file_status 字段。
        data = self.assert_query_ok(["--file-status", "missing"], [self.expected_a])
        self.assertEqual(len(data), 1)
        self.assertEqual(set(data[0]), {"path", "type", "tags"})
        self.assertNotIn("file_status", data[0])

        # 加 --check-files 后仍只返回 A，并附加 missing。
        data = self.assert_query_ok(
            ["--file-status", "missing", "--check-files"],
            [self.with_status(self.expected_a, "missing")],
        )
        self.assertEqual(data[0]["file_status"], "missing")

        # 中间组件不是目录不能判为 not_file：not_file 筛选无结果，
        # 且查询本身退出码 0、标准错误为空。
        self.assert_query_ok(["--file-status", "not_file"], [])

    def test_plain_tag_query_keeps_both_records_without_status_while_broken(self):
        self.register_samples()
        self.break_a_path()

        # 不带状态选项：中间组件被替换期间仍返回原有两条记录。
        data = self.assert_query_ok(
            [], [self.expected_a, self.expected_b]
        )
        self.assertEqual(len(data), 2)
        for record in data:
            self.assertNotIn("file_status", record)

    def test_restored_nested_path_reports_present_without_reregistration(self):
        self.register_samples()
        self.break_a_path()
        self.assert_query_ok(["--file-status", "missing"], [self.expected_a])

        # 用同一样例恢复 nested 目录及 a.bin，不重新登记。
        self.restore_a_path()

        # 随后由新进程执行 missing 筛选得到 []。
        self.assert_query_ok(["--file-status", "missing"], [])
        # 带 --check-files 的标签查询中两条素材都显示 present。
        self.assert_query_ok(
            ["--check-files"],
            [
                self.with_status(self.expected_a, "present"),
                self.with_status(self.expected_b, "present"),
            ],
        )
        # 恢复后路径、类型与完整标签顺序仍与登记一致。
        self.assert_query_ok([], [self.expected_a, self.expected_b])

    def test_queries_do_not_change_metadata_or_file_contents(self):
        self.register_samples()
        self.break_a_path()

        # 先做一次普通查询稳定数据库文件状态，再对字节快照。
        self.assert_query_ok([], [self.expected_a, self.expected_b])
        db_bytes_before = self.db_path.read_bytes()

        self.assert_query_ok(
            ["--check-files"],
            [
                self.with_status(self.expected_a, "missing"),
                self.with_status(self.expected_b, "present"),
            ],
        )
        self.assert_query_ok(["--file-status", "missing"], [self.expected_a])
        self.assert_query_ok(
            ["--file-status", "missing", "--check-files"],
            [self.with_status(self.expected_a, "missing")],
        )
        self.assert_query_ok(["--file-status", "not_file"], [])
        self.assert_query_ok([], [self.expected_a, self.expected_b])

        # 各次查询不改变数据库字节与登记元数据：export 仍原样返回 A、B。
        self.assertEqual(self.db_path.read_bytes(), db_bytes_before)
        export = self.run_cli("export")
        self.assertEqual(export.returncode, 0, export.stderr)
        self.assertEqual(export.stderr, "")
        self.assertEqual(
            json.loads(export.stdout), [self.expected_a, self.expected_b]
        )

        # 不改写 B，也不改写替代 nested 的普通文件内容。
        self.assertEqual(
            self.file_b.read_text(encoding="utf-8"), B_CONTENT
        )
        self.assertEqual(
            self.nested_dir.read_text(encoding="utf-8"), REPLACEMENT_CONTENT
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
