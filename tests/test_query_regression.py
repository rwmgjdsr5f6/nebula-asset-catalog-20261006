"""query 标签精确查询的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

覆盖：
- 标签去空白、去重、大小写保留与完整标签顺序；
- 查询的完整匹配、大小写敏感、按登记顺序返回且每个素材只出现一次；
- 查询成功时退出码 0、标准输出仅为 JSON 数组、标准错误为空；
- 登记与查询分别由独立进程完成，记录经 SQLite 持久化后可被新进程读取；
- 源文件删除后目录记录保留；
- 缺少 --tag、标签仅含空白时退出码 2、标准输出为空、标准错误说明原因且无调用栈；
- 出错后再次查询，原有结果不变。

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


class QueryRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "sample_a.bin"
        self.file_b = self.tmp_dir / "sample_b.bin"
        self.file_a.write_text("sample asset A\n", encoding="utf-8")
        self.file_b.write_text("sample asset B\n", encoding="utf-8")
        self.db_path = self.tmp_dir / "catalog.sqlite"

        self.expected_a = {
            "path": os.path.realpath(str(self.file_a)),
            "type": "image",
            "tags": ["demo", "ui", "Demo"],
        }
        self.expected_b = {
            "path": os.path.realpath(str(self.file_b)),
            "type": "audio",
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

    def register_samples(self):
        """用 add 准备固定样例（add 仅用于准备数据，非本次测试对象）。"""
        result_a = self.run_cli(
            "add",
            str(self.file_a),
            "--type",
            "image",
            "--tag",
            " demo ",
            "--tag",
            "ui",
            "--tag",
            "demo",
            "--tag",
            "Demo",
        )
        self.assertEqual(result_a.returncode, 0, result_a.stderr)
        self.assertEqual(result_a.stderr, "")

        result_b = self.run_cli(
            "add",
            str(self.file_b),
            "--type",
            "audio",
            "--tag",
            "ui",
            "--tag",
            "demo",
        )
        self.assertEqual(result_b.returncode, 0, result_b.stderr)
        self.assertEqual(result_b.stderr, "")

    def assertQueryOk(self, tag, expected):
        """查询成功：退出码 0、标准错误为空、标准输出为预期 JSON 数组。"""
        result = self.run_cli("query", "--tag", tag)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        self.assertEqual(data, expected)
        return data

    def test_query_exact_tag_matching_and_full_tags(self):
        self.register_samples()

        # " demo " 去除首尾空白后精确匹配 demo：A、B 按登记顺序各出现一次。
        data = self.assertQueryOk(" demo ", [self.expected_a, self.expected_b])
        self.assertEqual(len(data), 2)
        # 完整标签及其顺序，而不仅是命中的标签。
        self.assertEqual(data[0]["tags"], ["demo", "ui", "Demo"])
        self.assertEqual(data[1]["tags"], ["ui", "demo"])
        # 路径为规范绝对路径，类型保持登记值。
        self.assertEqual(data[0]["path"], self.expected_a["path"])
        self.assertTrue(os.path.isabs(data[0]["path"]))
        self.assertEqual(data[0]["type"], "image")
        self.assertEqual(data[1]["type"], "audio")

        # 大小写区分：Demo 只命中 A。
        self.assertQueryOk("Demo", [self.expected_a])
        # 完整匹配：不做子串匹配，de 无结果。
        self.assertQueryOk("de", [])

    def test_records_read_by_fresh_process_after_registration(self):
        self.register_samples()
        # run_cli 每次启动全新解释器进程；此处显式再次以新进程读取同一数据库。
        result = self.run_cli("query", "--tag", "demo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout), [self.expected_a, self.expected_b]
        )

    def test_records_remain_after_source_file_deleted(self):
        self.register_samples()
        self.file_a.unlink()
        self.assertFalse(self.file_a.exists())

        # 源文件删除不影响已保存的目录记录，元数据保持原样。
        self.assertQueryOk("demo", [self.expected_a, self.expected_b])

    def test_missing_or_blank_tag_errors_leave_records_intact(self):
        self.register_samples()

        # 缺少 --tag：退出码 2，标准输出为空，标准错误说明原因且无调用栈。
        missing = self.run_cli("query")
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(missing.stdout, "")
        self.assertNotEqual(missing.stderr.strip(), "")
        self.assertNotIn("Traceback", missing.stderr)

        # 标签仅含空白：同样的错误规则。
        blank = self.run_cli("query", "--tag", "   ")
        self.assertEqual(blank.returncode, 2)
        self.assertEqual(blank.stdout, "")
        self.assertNotEqual(blank.stderr.strip(), "")
        self.assertNotIn("Traceback", blank.stderr)

        # 错误后再次查询 demo，原有结果不变。
        self.assertQueryOk("demo", [self.expected_a, self.expected_b])


if __name__ == "__main__":
    unittest.main(verbosity=2)
