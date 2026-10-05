"""add 规范路径去重规则的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖，
不解码素材内容，不访问网络。

覆盖：
- 以相对路径首次登记：退出码 0、标准错误为空、标准输出为包含规范绝对路径、
  原类型与完整标签顺序的单个 JSON 对象；
- 用首次登记的路径写法、同一文件的绝对路径、含 ``.`` 与 ``..`` 的等价路径
  再次登记（不同类型与标签）：退出码 2、标准输出为空、
  标准错误说明重复登记且不含调用栈；
- 每次重复登记被拒绝后，查询原标签仍只得到首次登记的记录，
  查询重复尝试传入的新标签得到空数组（原有记录保持不变，未被覆盖/合并/更新）；
- 内容相同但规范路径不同的另一文件可正常登记，查询按首次登记顺序各返回一次，
  证明去重依据是规范路径而非文件内容；
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

SAMPLE_CONTENT = "identical sample content\n"


class AddDedupRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        # 样例文件放在独立子目录中，便于用相对路径与含 . / .. 的路径登记。
        self.work_dir = self.tmp_dir / "work"
        self.work_dir.mkdir()
        (self.work_dir / "subdir").mkdir()

        # A 为固定样例；B 与 A 内容完全相同，仅规范路径不同（对照组）。
        self.file_a = self.work_dir / "sample_a.bin"
        self.file_b = self.work_dir / "sample_b.bin"
        self.file_a.write_text(SAMPLE_CONTENT, encoding="utf-8")
        self.file_b.write_text(SAMPLE_CONTENT, encoding="utf-8")

        self.db_path = self.tmp_dir / "catalog.sqlite"

        self.canonical_a = os.path.realpath(str(self.file_a))
        self.canonical_b = os.path.realpath(str(self.file_b))
        self.assertNotEqual(self.canonical_a, self.canonical_b)

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

    def run_cli(self, *args, cwd=None):
        """以全新进程运行 python -m asset_catalog，返回 CompletedProcess。"""
        cmd = [
            sys.executable,
            "-m",
            "asset_catalog",
            "--db",
            str(self.db_path),
            *args,
        ]
        # 工作目录可能是临时样例目录，通过 PYTHONPATH 保证模块可导入。
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(PROJECT_ROOT), env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
        return subprocess.run(
            cmd,
            cwd=str(cwd or PROJECT_ROOT),
            capture_output=True,
            text=True,
            env=env,
        )

    def register_a_relative(self):
        """以相对路径首次登记 A：image，标签依次为 demo、ui。"""
        return self.run_cli(
            "add",
            "sample_a.bin",
            "--type",
            "image",
            "--tag",
            "demo",
            "--tag",
            "ui",
            cwd=self.work_dir,
        )

    def assertAddOk(self, result, expected):
        """登记成功：退出码 0、标准错误为空、标准输出为预期的单个 JSON 对象。"""
        self.assertEqual(result.returncode, 0, f"标准错误: {result.stderr}")
        self.assertEqual(result.stderr, "")
        # 标准输出是单个 JSON 对象（单行），不是数组或其他结构。
        self.assertEqual(result.stdout.strip().count("\n"), 0)
        data = json.loads(result.stdout)
        self.assertIsInstance(data, dict)
        self.assertEqual(data, expected)

    def assertDuplicateRejected(self, result, canonical_path):
        """重复登记被拒绝：退出码 2、标准输出为空、标准错误说明原因且无调用栈。"""
        self.assertEqual(result.returncode, 2, f"标准输出: {result.stdout}")
        self.assertEqual(result.stdout, "")
        self.assertIn("重复登记", result.stderr)
        self.assertIn(canonical_path, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def assertQueryOk(self, tag, expected):
        """查询成功：退出码 0、标准错误为空、标准输出为预期 JSON 数组。"""
        result = self.run_cli("query", "--tag", tag)
        self.assertEqual(result.returncode, 0, f"标准错误: {result.stderr}")
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        self.assertEqual(data, expected)
        return data

    def assertSampleContentsUnchanged(self):
        """两个样例文件的内容与登记前一致（登记/查询不改动源文件）。"""
        self.assertEqual(
            self.file_a.read_text(encoding="utf-8"), SAMPLE_CONTENT
        )
        self.assertEqual(
            self.file_b.read_text(encoding="utf-8"), SAMPLE_CONTENT
        )

    def test_first_registration_with_relative_path(self):
        result = self.register_a_relative()
        self.assertAddOk(result, self.expected_a)

        # 输出中的路径是规范绝对路径，类型与标签顺序保持登记值。
        data = json.loads(result.stdout)
        self.assertTrue(os.path.isabs(data["path"]))
        self.assertEqual(data["path"], self.canonical_a)
        self.assertEqual(data["type"], "image")
        self.assertEqual(data["tags"], ["demo", "ui"])

        # 登记与查询由不同进程完成；新进程查询同一数据库应看到该记录。
        self.assertQueryOk("demo", [self.expected_a])
        self.assertQueryOk("ui", [self.expected_a])

        self.assertSampleContentsUnchanged()

    def test_duplicate_registrations_rejected_and_record_unchanged(self):
        self.assertAddOk(self.register_a_relative(), self.expected_a)

        # 同一规范路径的三种等价写法：首次登记的路径写法、绝对路径、
        # 含 . 与 .. 的等价路径。每次重复尝试都传入不同的类型与标签。
        duplicate_attempts = [
            (("sample_a.bin",), self.work_dir),
            ((str(self.file_a),), None),
            (("subdir/.././sample_a.bin",), self.work_dir),
        ]
        for index, (path_args, cwd) in enumerate(duplicate_attempts, start=1):
            with self.subTest(attempt=index, path=path_args[0]):
                result = self.run_cli(
                    "add",
                    *path_args,
                    "--type",
                    "audio",
                    "--tag",
                    "replacement",
                    cwd=cwd,
                )
                self.assertDuplicateRejected(result, self.canonical_a)

                # 每次失败后重新查询：demo 仍只得到 A 的原始记录，
                # 未被覆盖、合并或更新；replacement 从未生效，得到空数组。
                self.assertQueryOk("demo", [self.expected_a])
                self.assertQueryOk("replacement", [])

        self.assertSampleContentsUnchanged()

    def test_same_content_different_path_registered_separately(self):
        self.assertAddOk(self.register_a_relative(), self.expected_a)

        # B 与 A 内容相同但规范路径不同：同样的类型与标签应登记成功，
        # 证明去重依据是规范路径而非文件内容。
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
        self.assertAddOk(result_b, self.expected_b)

        # 新进程查询 demo：A、B 各出现一次，保持首次登记顺序。
        data = self.assertQueryOk("demo", [self.expected_a, self.expected_b])
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["path"], self.canonical_a)
        self.assertEqual(data[1]["path"], self.canonical_b)

        self.assertSampleContentsUnchanged()


if __name__ == "__main__":
    unittest.main(verbosity=2)
