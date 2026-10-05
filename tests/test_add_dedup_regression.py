"""add 同一规范路径拒绝重复登记的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

覆盖：
- 以相对路径首次登记成功：退出码 0、标准错误为空、标准输出为单个 JSON 对象，
  含规范绝对路径、原类型与完整标签顺序；
- 用同一相对路径写法、绝对路径、含 ``.`` / ``..`` 的等价路径再次登记：
  退出码 2、标准输出为空、标准错误说明重复登记且不含调用栈；
- 每次重复尝试失败后，查询 demo 仍只返回原记录，查询 replacement 返回空数组，
  即重复登记不会覆盖、合并或更新原有记录；
- 内容相同但规范路径不同的另一文件可按相同类型与标签成功登记，
  查询按首次登记顺序各返回一次（去重依据是路径而非内容）；
- 登记与查询分别由独立进程完成，记录经 SQLite 持久化后可被新进程读取；
- 全部操作结束后，两个样例文件的内容与登记前一致。

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

# B 与 A 内容完全相同，仅路径不同，用于证明去重依据是路径而非内容。
SAMPLE_CONTENT = "add dedup regression sample\n"


class AddDedupRegressionTest(unittest.TestCase):
    def setUp(self):
        # 临时目录建在项目根目录下，以便用相对路径从公开入口登记；
        # 目录与数据库均由本用例自建，tearDown 时整体清理。
        self._tmp = tempfile.TemporaryDirectory(dir=PROJECT_ROOT)
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "sample_a.bin"
        self.file_b = self.tmp_dir / "sample_b.bin"
        self.file_a.write_text(SAMPLE_CONTENT, encoding="utf-8")
        self.file_b.write_text(SAMPLE_CONTENT, encoding="utf-8")
        self.db_path = self.tmp_dir / "catalog.sqlite"

        self.rel_a = os.path.relpath(self.file_a, PROJECT_ROOT)
        self.canonical_a = os.path.realpath(str(self.file_a))
        self.canonical_b = os.path.realpath(str(self.file_b))

        self.expected_a = {
            "path": self.canonical_a,
            "type": "image",
            "tags": ["demo", "ui"],
        }
        self.expected_b = {
            "path": self.canonical_b,
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

    def equivalent_spellings(self):
        """同一文件 A 的等价路径写法：原相对路径、绝对路径、含 . / .. 的路径。"""
        tmp_rel = os.path.relpath(self.tmp_dir, PROJECT_ROOT)
        tmp_name = os.path.basename(tmp_rel)
        return [
            ("首次登记的相对路径写法", self.rel_a),
            ("同一文件的绝对路径", str(self.file_a)),
            ("含 . 的等价路径", os.path.join(tmp_rel, ".", "sample_a.bin")),
            (
                "含 .. 的等价路径",
                os.path.join(tmp_rel, "..", tmp_name, "sample_a.bin"),
            ),
            (
                "同时含 . 与 .. 的等价路径",
                os.path.join(tmp_rel, ".", "..", tmp_name, "sample_a.bin"),
            ),
        ]

    def register_a(self):
        """以相对路径首次登记 A：类型 image，标签依次为 demo、ui。"""
        result = self.run_cli(
            "add",
            self.rel_a,
            "--type",
            "image",
            "--tag",
            "demo",
            "--tag",
            "ui",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return result

    def assertQueryOk(self, tag, expected):
        """查询成功：退出码 0、标准错误为空、标准输出为预期 JSON 数组。"""
        result = self.run_cli("query", "--tag", tag)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        self.assertEqual(data, expected)
        return data

    def assertDuplicateRejected(self, spelling, label):
        """重复登记被拒绝：退出码 2、标准输出为空、标准错误说明原因且无调用栈。"""
        result = self.run_cli(
            "add",
            spelling,
            "--type",
            "audio",
            "--tag",
            "replacement",
        )
        self.assertEqual(
            result.returncode,
            2,
            f"{label}: 期望退出码 2，实际 {result.returncode}，"
            f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )
        self.assertEqual(
            result.stdout,
            "",
            f"{label}: 期望标准输出为空，实际 {result.stdout!r}",
        )
        self.assertIn("重复登记", result.stderr, f"{label}: 标准错误未说明重复登记")
        self.assertIn(
            self.canonical_a,
            result.stderr,
            f"{label}: 标准错误未指出冲突的规范路径",
        )
        self.assertNotIn("Traceback", result.stderr, f"{label}: 标准错误含调用栈")

    def assertOriginalRecordIntact(self):
        """每次重复尝试失败后：demo 只命中 A 的原记录，replacement 无结果。"""
        self.assertQueryOk("demo", [self.expected_a])
        self.assertQueryOk("replacement", [])

    def test_add_success_reports_canonical_record(self):
        result = self.register_a()

        # 标准输出是单个 JSON 对象（单行），含规范绝对路径、原类型与完整标签顺序。
        self.assertEqual(len(result.stdout.strip().splitlines()), 1)
        payload = json.loads(result.stdout)
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload, self.expected_a)
        self.assertTrue(os.path.isabs(payload["path"]))
        self.assertEqual(payload["path"], self.canonical_a)
        self.assertEqual(payload["type"], "image")
        self.assertEqual(payload["tags"], ["demo", "ui"])

    def test_duplicate_spellings_rejected_and_original_record_kept(self):
        self.register_a()

        for label, spelling in self.equivalent_spellings():
            with self.subTest(写法=label):
                self.assertDuplicateRejected(spelling, label)
                # 重复登记不覆盖、不合并、不更新：原记录保持，新标签未生效。
                self.assertOriginalRecordIntact()

    def test_same_content_different_path_registered_separately(self):
        self.register_a()

        # B 与 A 内容相同但规范路径不同：按相同类型与标签登记应成功。
        result_b = self.run_cli(
            "add",
            str(self.file_b),
            "--type",
            "image",
            "--tag",
            "demo",
            "--tag",
            "ui",
        )
        self.assertEqual(result_b.returncode, 0, result_b.stderr)
        self.assertEqual(result_b.stderr, "")
        self.assertEqual(json.loads(result_b.stdout), self.expected_b)

        # 查询 demo：A、B 各出现一次，按首次登记顺序排列。
        self.assertQueryOk("demo", [self.expected_a, self.expected_b])

    def test_records_persist_across_processes(self):
        self.register_a()
        for label, spelling in self.equivalent_spellings():
            with self.subTest(写法=label):
                self.assertDuplicateRejected(spelling, label)

        # run_cli 每次启动全新解释器进程；此处显式以新进程读取同一数据库。
        result = self.run_cli("query", "--tag", "demo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [self.expected_a])
        self.assertQueryOk("replacement", [])

    def test_sample_files_unchanged_after_all_operations(self):
        self.register_a()
        for label, spelling in self.equivalent_spellings():
            with self.subTest(写法=label):
                self.assertDuplicateRejected(spelling, label)
        result_b = self.run_cli(
            "add",
            str(self.file_b),
            "--type",
            "image",
            "--tag",
            "demo",
            "--tag",
            "ui",
        )
        self.assertEqual(result_b.returncode, 0, result_b.stderr)
        self.assertQueryOk("demo", [self.expected_a, self.expected_b])

        # 登记与查询均不修改源文件：两个样例的内容与登记前一致。
        self.assertEqual(self.file_a.read_text(encoding="utf-8"), SAMPLE_CONTENT)
        self.assertEqual(self.file_b.read_text(encoding="utf-8"), SAMPLE_CONTENT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
