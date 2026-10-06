"""query 多标签（重复 --tag）交集查询的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

覆盖：
- 同时传入 demo 与 ui 时只返回同时具备两标签的 A、C，各出现一次且保持
  登记顺序，只有 demo 的 B 不入选；
- 交换条件顺序、重复传入 demo、给任一条件增加首尾空白，结果与原查询相同；
- 每条结果保留登记时的规范绝对路径、类型与完整标签：A 的 Demo 标签保留，
  C 的标签顺序不被查询条件改变；
- 大小写区分（Demo + ui 只返回 A）、完整匹配（de + ui 返回 []）、
  交集语义（demo + 未登记标签 absent 返回 []）；
- 成功查询退出码 0、标准错误为空、标准输出仅为一个 JSON 数组，
  比较解析后的内容，不依赖 JSON 空白或对象键顺序；
- 有效条件之后混入空字符串或仅含空白的标签：整次查询退出码 2、
  标准输出为空、标准错误指出 --tag 问题且无调用栈，不返回部分结果；
- 每次失败后重新查询 demo 与 ui 仍得到原有 A、C，样例文件内容不变。

每个用例使用独立的临时目录与数据库，只创建/清理自己的样例文件；
登记与查询分别由独立进程完成。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

CONTENT_A = "sample asset A\n"
CONTENT_B = "sample asset B\n"
CONTENT_C = "sample asset C\n"


class QueryMultiTagRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "sample_a.bin"
        self.file_b = self.tmp_dir / "sample_b.bin"
        self.file_c = self.tmp_dir / "sample_c.bin"
        self.file_a.write_text(CONTENT_A, encoding="utf-8")
        self.file_b.write_text(CONTENT_B, encoding="utf-8")
        self.file_c.write_text(CONTENT_C, encoding="utf-8")
        self.db_path = self.tmp_dir / "catalog.sqlite"

        self.expected_a = {
            "path": os.path.realpath(str(self.file_a)),
            "type": "image",
            "tags": ["demo", "ui", "Demo"],
        }
        self.expected_b = {
            "path": os.path.realpath(str(self.file_b)),
            "type": "audio",
            "tags": ["demo"],
        }
        self.expected_c = {
            "path": os.path.realpath(str(self.file_c)),
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
        """按 A、B、C 顺序登记固定样例（add 仅用于准备数据，非测试对象）。"""
        result_a = self.run_cli(
            "add",
            str(self.file_a),
            "--type",
            "image",
            "--tag",
            "demo",
            "--tag",
            "ui",
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
            "demo",
        )
        self.assertEqual(result_b.returncode, 0, result_b.stderr)
        self.assertEqual(result_b.stderr, "")

        result_c = self.run_cli(
            "add",
            str(self.file_c),
            "--type",
            "audio",
            "--tag",
            "ui",
            "--tag",
            "demo",
        )
        self.assertEqual(result_c.returncode, 0, result_c.stderr)
        self.assertEqual(result_c.stderr, "")

    def assertQueryOk(self, tags, expected):
        """多标签查询成功：退出码 0、标准错误为空、标准输出为预期 JSON 数组。"""
        args = ["query"]
        for tag in tags:
            args += ["--tag", tag]
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        # 比较解析后的数据，不依赖对象键顺序或 JSON 空白。
        self.assertEqual(data, expected)
        return data

    def assertSampleContentsUnchanged(self):
        self.assertEqual(self.file_a.read_text(encoding="utf-8"), CONTENT_A)
        self.assertEqual(self.file_b.read_text(encoding="utf-8"), CONTENT_B)
        self.assertEqual(self.file_c.read_text(encoding="utf-8"), CONTENT_C)

    def test_multi_tag_intersection_returns_assets_with_all_tags(self):
        self.register_samples()

        # demo + ui：A、C 同时具备两标签，各出现一次且保持登记顺序；
        # 只有 demo 的 B 不入选。
        data = self.assertQueryOk(
            ["demo", "ui"], [self.expected_a, self.expected_c]
        )
        self.assertEqual(len(data), 2)
        paths = [record["path"] for record in data]
        self.assertEqual(paths, [self.expected_a["path"], self.expected_c["path"]])
        self.assertNotIn(self.expected_b["path"], paths)

        # 每条结果保留登记时的规范绝对路径、类型与完整标签。
        for record in data:
            self.assertTrue(os.path.isabs(record["path"]))
        self.assertEqual(data[0]["type"], "image")
        self.assertEqual(data[1]["type"], "audio")
        # A 的 Demo 标签保留；C 的标签顺序不被查询条件改变。
        self.assertEqual(data[0]["tags"], ["demo", "ui", "Demo"])
        self.assertEqual(data[1]["tags"], ["ui", "demo"])

    def test_condition_order_repetition_and_whitespace_are_equivalent(self):
        self.register_samples()
        expected = [self.expected_a, self.expected_c]

        # 原查询作为基准。
        self.assertQueryOk(["demo", "ui"], expected)
        # 交换两个条件的顺序，结果相同。
        self.assertQueryOk(["ui", "demo"], expected)
        # 重复传入 demo 等同于只传一次。
        self.assertQueryOk(["demo", "demo", "ui"], expected)
        self.assertQueryOk(["demo", "ui", "demo"], expected)
        # 给任一条件增加首尾空白，去除空白后结果相同。
        self.assertQueryOk([" demo ", "ui"], expected)
        self.assertQueryOk(["demo", "  ui  "], expected)
        self.assertQueryOk(["  demo  ", "  ui  "], expected)

    def test_case_sensitive_exact_match_and_intersection_semantics(self):
        self.register_samples()

        # 大小写区分：Demo + ui 只返回 A（C 的 demo 不匹配 Demo）。
        self.assertQueryOk(["Demo", "ui"], [self.expected_a])
        # 完整匹配：de 不是任何标签的完整值，de + ui 返回 []。
        self.assertQueryOk(["de", "ui"], [])
        # 交集语义：absent 未登记，demo + absent 无素材同时具备，返回 []。
        self.assertQueryOk(["demo", "absent"], [])

    def test_empty_tag_condition_rejected_and_state_unchanged(self):
        self.register_samples()

        invalid_invocations = [
            ("query", "--tag", "demo", "--tag", ""),  # 空字符串标签
            ("query", "--tag", "demo", "--tag", "   "),  # 仅含空白的标签
        ]
        for args in invalid_invocations:
            with self.subTest(args=args):
                result = self.run_cli(*args)
                # 整次查询失败：退出码 2，标准输出为空（不返回部分结果）。
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                # 标准错误指出 --tag 的问题，不固定整段错误文字，且无调用栈。
                self.assertIn("--tag", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

                # 每次失败后重新查询 demo 与 ui，仍得到原有 A、C。
                self.assertQueryOk(
                    ["demo", "ui"], [self.expected_a, self.expected_c]
                )
                # 样例文件内容保持不变。
                self.assertSampleContentsUnchanged()


if __name__ == "__main__":
    unittest.main(verbosity=2)
