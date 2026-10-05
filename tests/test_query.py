"""标签查询（query）的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以独立子进程观察行为，
只使用 Python 3 标准库。每个用例在独立临时目录中创建自己的样例文件与
数据库，结束后自动清理，不接触仓库内已有目录或素材。

运行方式（在项目根目录）：

    python -m unittest discover -s tests -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class QueryRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp.name
        self.db_path = os.path.join(self.tmpdir, "catalog.sqlite")
        # 测试自行创建的两个普通文件，依次作为素材 A、B。
        self.path_a = os.path.join(self.tmpdir, "sample_a.bin")
        self.path_b = os.path.join(self.tmpdir, "sample_b.bin")
        with open(self.path_a, "w", encoding="utf-8") as f:
            f.write("sample A\n")
        with open(self.path_b, "w", encoding="utf-8") as f:
            f.write("sample B\n")

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args):
        """以全新进程调用 python -m asset_catalog，返回 CompletedProcess。"""
        env = dict(os.environ)
        env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run(
            [sys.executable, "-m", "asset_catalog", "--db", self.db_path, *args],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

    def register_samples(self):
        """用 add 准备样例：先登记 A（image），再登记 B（audio）。"""
        result_a = self.run_cli(
            "add", self.path_a, "--type", "image",
            "--tag", " demo ", "--tag", "ui", "--tag", "demo", "--tag", "Demo",
        )
        self.assertEqual(result_a.returncode, 0, result_a.stderr)
        result_b = self.run_cli(
            "add", self.path_b, "--type", "audio",
            "--tag", "ui", "--tag", "demo",
        )
        self.assertEqual(result_b.returncode, 0, result_b.stderr)

    def query_json(self, tag):
        """成功查询：退出码 0、标准错误为空、标准输出整体为一个 JSON 数组。"""
        result = self.run_cli("query", "--tag", tag)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        return data

    def expected_record(self, path, asset_type, tags):
        return {
            "path": os.path.realpath(path),
            "type": asset_type,
            "tags": tags,
        }

    def test_query_exact_match_case_and_full_tags(self):
        self.register_samples()
        record_a = self.expected_record(
            self.path_a, "image", ["demo", "ui", "Demo"]
        )
        record_b = self.expected_record(self.path_b, "audio", ["ui", "demo"])

        # " demo " 去除首尾空白后精确命中 demo：按登记顺序返回 A、B，各一次；
        # 比较解析后的完整记录（含完整标签及顺序），不依赖 JSON 空白或键顺序。
        self.assertEqual(self.query_json(" demo "), [record_a, record_b])

        # 大小写区分：Demo 只命中 A。
        self.assertEqual(self.query_json("Demo"), [record_a])

        # 完整匹配：de 不做子串匹配，返回空数组。
        self.assertEqual(self.query_json("de"), [])

    def test_new_process_reads_persisted_records(self):
        self.register_samples()
        # 登记结束后由新启动的查询进程读取同一数据库，得到同样的记录。
        record_a = self.expected_record(
            self.path_a, "image", ["demo", "ui", "Demo"]
        )
        record_b = self.expected_record(self.path_b, "audio", ["ui", "demo"])
        self.assertEqual(self.query_json(" demo "), [record_a, record_b])

    def test_deleted_source_keeps_catalog_records(self):
        self.register_samples()
        os.remove(self.path_a)
        # 源文件删除不影响目录记录：元数据仍为登记时的内容。
        record_a = self.expected_record(
            self.path_a, "image", ["demo", "ui", "Demo"]
        )
        record_b = self.expected_record(self.path_b, "audio", ["ui", "demo"])
        self.assertEqual(self.query_json("demo"), [record_a, record_b])

    def assert_query_error(self, *query_args):
        result = self.run_cli("query", *query_args)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")
        self.assertNotIn("Traceback", result.stderr)

    def test_query_error_rules_and_recovery(self):
        self.register_samples()
        record_a = self.expected_record(
            self.path_a, "image", ["demo", "ui", "Demo"]
        )
        record_b = self.expected_record(self.path_b, "audio", ["ui", "demo"])

        # 缺少 --tag：退出码 2、标准输出为空、标准错误说明原因且无调用栈。
        self.assert_query_error()
        # 标签仅含空白：同样的错误规则。
        self.assert_query_error("--tag", "   ")

        # 错误后再次查询 demo，原有结果不变。
        self.assertEqual(self.query_json("demo"), [record_a, record_b])


if __name__ == "__main__":
    unittest.main()
