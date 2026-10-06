"""query --type 类型筛选的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

覆盖：
- --type 与标签条件同时生效，类型完整匹配且区分大小写；
- --type 去除首尾空白后匹配，结果按登记顺序返回且每个素材只出现一次；
- 不传 --type 时查询结果与只按标签查询一致；
- 查询成功时退出码 0、标准输出仅为 JSON 数组、标准错误为空，
  每条记录保留规范绝对路径、原类型与完整标签顺序；
- --type 缺失值、空字符串或仅含空白时退出码 2、标准输出为空、
  标准错误指出类型参数问题且无调用栈；
- 出错后再次查询，原有结果与样例文件内容均不变。

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


class QueryTypeRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "sample_a.bin"
        self.file_b = self.tmp_dir / "sample_b.bin"
        self.file_c = self.tmp_dir / "sample_c.bin"
        self.content_a = "sample asset A\n"
        self.content_b = "sample asset B\n"
        self.content_c = "sample asset C\n"
        self.file_a.write_text(self.content_a, encoding="utf-8")
        self.file_b.write_text(self.content_b, encoding="utf-8")
        self.file_c.write_text(self.content_c, encoding="utf-8")
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

    def register_samples(self):
        """依次登记 A(image)、B(audio)、C(image)（add 仅用于准备数据）。"""
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
            "image",
            "--tag",
            "ui",
            "--tag",
            "demo",
        )
        self.assertEqual(result_c.returncode, 0, result_c.stderr)
        self.assertEqual(result_c.stderr, "")

    def assertQueryOk(self, tag, expected, asset_type=None):
        """查询成功：退出码 0、标准错误为空、标准输出为预期 JSON 数组。"""
        args = ["query", "--tag", tag]
        if asset_type is not None:
            args += ["--type", asset_type]
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        # 比较解析后的数据，不依赖对象键顺序或 JSON 空白。
        self.assertEqual(data, expected)
        return data

    def assertSampleContentsUnchanged(self):
        self.assertEqual(self.file_a.read_text(encoding="utf-8"), self.content_a)
        self.assertEqual(self.file_b.read_text(encoding="utf-8"), self.content_b)
        self.assertEqual(self.file_c.read_text(encoding="utf-8"), self.content_c)

    def test_type_filter_matches_full_type_case_sensitively(self):
        self.register_samples()

        # demo + image：仅 A、C，各出现一次且保持登记顺序。
        data = self.assertQueryOk(
            "demo", [self.expected_a, self.expected_c], asset_type="image"
        )
        self.assertEqual(len(data), 2)
        self.assertEqual(
            [record["path"] for record in data],
            [self.expected_a["path"], self.expected_c["path"]],
        )
        # 每条记录保留规范绝对路径、原类型与完整标签顺序。
        for record in data:
            self.assertTrue(os.path.isabs(record["path"]))
            self.assertEqual(record["type"], "image")
        self.assertEqual(data[0]["tags"], ["demo", "ui", "Demo"])
        self.assertEqual(data[1]["tags"], ["ui", "demo"])

        # 类型去除首尾空白后匹配：" image " 与 image 结果相同。
        self.assertQueryOk(
            "demo", [self.expected_a, self.expected_c], asset_type=" image "
        )

        # demo + audio：仅 B。
        self.assertQueryOk("demo", [self.expected_b], asset_type="audio")

        # 大小写敏感（Image）、完整匹配（ima）、无匹配（model）均为空数组。
        self.assertQueryOk("demo", [], asset_type="Image")
        self.assertQueryOk("demo", [], asset_type="ima")
        self.assertQueryOk("demo", [], asset_type="model")

    def test_type_filter_combines_with_tag_condition(self):
        self.register_samples()

        # Demo 只命中 A；叠加 image 仍为 A，叠加 audio 为空：
        # 标签与类型条件同时生效。
        self.assertQueryOk("Demo", [self.expected_a], asset_type="image")
        self.assertQueryOk("Demo", [], asset_type="audio")

    def test_query_without_type_matches_tag_only_behavior(self):
        self.register_samples()

        # 不传 --type：demo 仍返回 A、B、C，顺序与完整标签不变。
        data = self.assertQueryOk(
            "demo", [self.expected_a, self.expected_b, self.expected_c]
        )
        self.assertEqual(len(data), 3)
        self.assertEqual(data[0]["tags"], ["demo", "ui", "Demo"])
        self.assertEqual(data[1]["tags"], ["demo"])
        self.assertEqual(data[2]["tags"], ["ui", "demo"])

    def test_invalid_type_errors_leave_records_and_files_intact(self):
        self.register_samples()

        invalid_invocations = [
            ("缺失值", ("query", "--tag", "demo", "--type")),
            ("空字符串", ("query", "--tag", "demo", "--type", "")),
            ("仅含空白", ("query", "--tag", "demo", "--type", "   ")),
        ]
        for label, args in invalid_invocations:
            with self.subTest(scenario=label):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                # 标准错误指出类型参数问题，不固定整段错误文字。
                self.assertIn("--type", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

                # 每次失败后重新查询 demo，仍得到原来的三条记录。
                self.assertQueryOk(
                    "demo",
                    [self.expected_a, self.expected_b, self.expected_c],
                )
                # 样例文件内容不变。
                self.assertSampleContentsUnchanged()


if __name__ == "__main__":
    unittest.main(verbosity=2)
