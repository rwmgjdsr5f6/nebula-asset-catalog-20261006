"""export --check-files 对登记路径变为符号链接的专项回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

覆盖：
- 登记路径原位置被替换为指向未登记普通文件 T 的符号链接后，
  ``export --check-files`` 按链接目标判定状态：A、B 均报告 present，
  输出 path 仍保留登记值，T 不成为目录记录；
- 删除 T 形成断链后 A 报告 missing、B 仍为 present，两条记录均保留；
  在原目标位置重建 T 后，新导出进程重新报告 A 为 present，无需再次登记；
- 独立样例中登记路径被替换为指向临时目录的符号链接时 A 报告 not_file、
  B 仍为 present，结果顺序与元数据不变；
- 每种状态下不带 --check-files 的 export 仍返回两条原元数据记录，
  不含 file_status；
- 每次导出前后样例数据库字节、B 与仍存在的 T 的内容以及链接指向均不变
  （样例准备中主动删除或重建的内容除外）。

环境不支持创建符号链接时仅跳过相关用例并说明原因，其他错误使测试失败；
跳过不计为行为验证通过。

每个用例使用独立的临时目录与数据库，只创建/清理自己的样例文件，
不访问真实素材或网络。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ExportCheckFilesSymlinkRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "A.bin"
        self.file_b = self.tmp_dir / "B.bin"
        self.target_t = self.tmp_dir / "T.bin"
        self.file_a.write_text("demo asset A\n", encoding="utf-8")
        self.file_b.write_text("demo asset B\n", encoding="utf-8")
        self.target_t.write_text("unregistered target T\n", encoding="utf-8")
        self.db_path = self.tmp_dir / "catalog.sqlite"

        self.expected_a = {
            "path": os.path.realpath(str(self.file_a)),
            "type": "image",
            "tags": ["demo", "ui"],
        }
        self.expected_b = {
            "path": os.path.realpath(str(self.file_b)),
            "type": "audio",
            "tags": ["music"],
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
        """按验收顺序登记 A（image: demo, ui）与 B（audio: music）。"""
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

        result_b = self.run_cli(
            "add",
            str(self.file_b),
            "--type",
            "audio",
            "--tag",
            "music",
        )
        self.assertEqual(result_b.returncode, 0, result_b.stderr)
        self.assertEqual(result_b.stderr, "")

    def replace_a_with_symlink(self, target):
        """删除 A 原路径的普通文件，替换为指向 target 的符号链接。

        环境不支持创建符号链接时跳过当前用例并说明原因；
        其他错误（如删除原文件失败）使测试失败。
        """
        self.file_a.unlink()
        try:
            os.symlink(str(target), str(self.file_a))
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"当前环境不支持创建符号链接: {exc}")
        self.assertTrue(self.file_a.is_symlink())
        self.assertEqual(os.readlink(str(self.file_a)), str(target))

    def assertExportCheckFilesOk(self, expected):
        """export --check-files：退出码 0、标准错误为空、
        标准输出仅一行 JSON 数组且解析后与 expected 一致。"""
        result = self.run_cli("export", "--check-files")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertTrue(result.stdout.endswith("\n"))
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        self.assertEqual(data, expected)
        return data

    def assertExportPlainOk(self):
        """不带 --check-files 的 export：仍返回 A、B 两条原元数据记录，
        不含 file_status。"""
        result = self.run_cli("export")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertEqual(data, [self.expected_a, self.expected_b])
        for record in data:
            self.assertNotIn("file_status", record)
        return data

    def assertLinkPointsTo(self, target):
        """A 原路径仍是指向 target 的符号链接（断链时同样成立）。"""
        self.assertTrue(self.file_a.is_symlink())
        self.assertEqual(os.readlink(str(self.file_a)), str(target))

    def test_symlink_to_unregistered_file_present_missing_present_cycle(self):
        self.register_samples()
        db_before = self.db_path.read_bytes()
        b_before = self.file_b.read_bytes()
        t_before = self.target_t.read_bytes()

        # 准备：A 原路径替换为指向未登记普通文件 T 的符号链接。
        self.replace_a_with_symlink(self.target_t)

        # 状态按链接目标判断：T 存在，A、B 均报告 present，各出现一次。
        data = self.assertExportCheckFilesOk(
            [
                {**self.expected_a, "file_status": "present"},
                {**self.expected_b, "file_status": "present"},
            ]
        )
        self.assertEqual(len(data), 2)
        # A 的 path、type、tags 保持登记值；T 不成为目录记录。
        self.assertEqual(data[0]["path"], self.expected_a["path"])
        self.assertEqual(data[0]["type"], "image")
        self.assertEqual(data[0]["tags"], ["demo", "ui"])
        target_real = os.path.realpath(str(self.target_t))
        for record in data:
            self.assertNotEqual(record["path"], target_real)

        # 同一状态下不带 --check-files：两条原元数据记录，无 file_status。
        self.assertExportPlainOk()

        # 导出前后数据库字节、B 与 T 的内容、链接指向均不变。
        self.assertEqual(self.db_path.read_bytes(), db_before)
        self.assertEqual(self.file_b.read_bytes(), b_before)
        self.assertEqual(self.target_t.read_bytes(), t_before)
        self.assertLinkPointsTo(self.target_t)

        # 删除 T 形成断链：A 报告 missing，B 仍为 present，两条记录均保留。
        self.target_t.unlink()
        self.assertFalse(self.target_t.exists())
        data = self.assertExportCheckFilesOk(
            [
                {**self.expected_a, "file_status": "missing"},
                {**self.expected_b, "file_status": "present"},
            ]
        )
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["path"], self.expected_a["path"])

        # 断链状态下不带 --check-files：仍返回两条原元数据记录。
        self.assertExportPlainOk()

        # 数据库字节、B 的内容与链接指向不变（T 由本用例主动删除）。
        self.assertEqual(self.db_path.read_bytes(), db_before)
        self.assertEqual(self.file_b.read_bytes(), b_before)
        self.assertLinkPointsTo(self.target_t)

        # 在原目标位置重建 T：新导出进程重新报告 A 为 present，无需再次登记。
        self.target_t.write_bytes(t_before)
        data = self.assertExportCheckFilesOk(
            [
                {**self.expected_a, "file_status": "present"},
                {**self.expected_b, "file_status": "present"},
            ]
        )
        self.assertEqual(len(data), 2)
        self.assertExportPlainOk()

        # 全程数据库字节、B 与重建后 T 的内容、链接指向保持一致。
        self.assertEqual(self.db_path.read_bytes(), db_before)
        self.assertEqual(self.file_b.read_bytes(), b_before)
        self.assertEqual(self.target_t.read_bytes(), t_before)
        self.assertLinkPointsTo(self.target_t)

    def test_symlink_to_directory_reports_not_file(self):
        self.register_samples()
        db_before = self.db_path.read_bytes()
        b_before = self.file_b.read_bytes()

        # 独立样例：A 原路径替换为指向临时目录的符号链接。
        link_dir = self.tmp_dir / "linked_dir"
        link_dir.mkdir()
        self.replace_a_with_symlink(link_dir)

        # 状态按链接目标判断：目标是目录，A 报告 not_file，B 仍为 present；
        # 结果顺序与 A、B 的元数据不变。
        data = self.assertExportCheckFilesOk(
            [
                {**self.expected_a, "file_status": "not_file"},
                {**self.expected_b, "file_status": "present"},
            ]
        )
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["path"], self.expected_a["path"])
        self.assertEqual(data[0]["type"], "image")
        self.assertEqual(data[0]["tags"], ["demo", "ui"])

        # 同一状态下不带 --check-files：两条原元数据记录，无 file_status。
        self.assertExportPlainOk()

        # 导出前后数据库字节、B 的内容与链接指向均不变。
        self.assertEqual(self.db_path.read_bytes(), db_before)
        self.assertEqual(self.file_b.read_bytes(), b_before)
        self.assertLinkPointsTo(link_dir)


if __name__ == "__main__":
    unittest.main(verbosity=2)
