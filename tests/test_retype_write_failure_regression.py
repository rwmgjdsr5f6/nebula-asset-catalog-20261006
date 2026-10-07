"""retype 类型写入被数据库拒绝时的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / sqlite3 / tempfile），
无需第三方依赖、网络或特殊权限。

固定场景：
- 两个内容固定、路径不同的临时演示文件 A.bin、B.bin。
- 先正常登记 A：类型 image，标签依次为 demo、ui；再登记 B：类型 audio，
  标签为 demo。
- 样例库保持原有兼容表结构（asset / asset_tag 及索引），仅附加一个
  BEFORE UPDATE 触发器作为固定拒绝条件：只拒绝把类型更新为 texture，
  拒绝文本固定为 ``retype blocked``，其他类型允许写入。

覆盖：
- 失败归类：对 A 的登记路径提交 retype --type texture 被触发器拒绝时，
  退出码 2、标准输出为空，标准错误说明数据库读写失败并保留数据库给出的
  拒绝文本 retype blocked，不输出调用栈；
- 失败原子性：失败后由新进程 export 仍按登记顺序返回 A、B 的原始完整
  记录（完整路径、类型与标签顺序不变）；query --tag demo --type image
  只返回 A，--type texture 返回 []；两个源文件内容不变；
- 恢复成功：在同一样例库撤去拒绝条件后原样提交同一修改，退出码 0、
  标准错误为空，标准输出仅为含 path、type、tags 的 A 记录，类型为
  texture、标签仍为 demo、ui；新进程 export 仍按 A、B 顺序返回且只有
  A 的类型变化；query --tag demo --type texture 只命中 A，
  --type image 返回 []，B 保持原样；两个源文件内容仍不变。

样例库、素材文件均由测试在独立临时目录中准备并自动清理。
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

REJECT_TRIGGER = "reject_texture_retype"
REJECT_MESSAGE = "retype blocked"

# 固定条件：仅拒绝把类型更新为 texture，拒绝文本固定为 retype blocked；
# 其他类型允许写入。
CREATE_REJECT_TRIGGER_SQL = f"""
CREATE TRIGGER {REJECT_TRIGGER}
BEFORE UPDATE ON asset
WHEN NEW.type = 'texture'
BEGIN
    SELECT RAISE(ABORT, '{REJECT_MESSAGE}');
END;
"""

DROP_REJECT_TRIGGER_SQL = f"DROP TRIGGER {REJECT_TRIGGER}"


class RetypeWriteFailureRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "A.bin"
        self.file_b = self.tmp_dir / "B.bin"
        self.content_a = b"retype write-failure sample A\n"
        self.content_b = b"retype write-failure sample B\n"
        self.file_a.write_bytes(self.content_a)
        self.file_b.write_bytes(self.content_b)
        self.db_path = self.tmp_dir / "catalog.sqlite"

        self.path_a = os.path.realpath(str(self.file_a))
        self.path_b = os.path.realpath(str(self.file_b))
        self.record_a = {
            "path": self.path_a,
            "type": "image",
            "tags": ["demo", "ui"],
        }
        self.record_b = {
            "path": self.path_b,
            "type": "audio",
            "tags": ["demo"],
        }
        self.record_a_retyped = {
            "path": self.path_a,
            "type": "texture",
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
        # 显式把项目根放入 PYTHONPATH，使验收命令在任意工作目录下均可解析
        # asset_catalog 包；子进程仍是全新的独立进程。
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            env=env,
        )

    def open_sample_db(self):
        return sqlite3.connect(str(self.db_path))

    def install_reject_condition(self):
        """加入仅拒绝把类型更新为 texture 的固定条件（触发器），并提交。"""
        conn = self.open_sample_db()
        try:
            conn.execute(CREATE_REJECT_TRIGGER_SQL)
            conn.commit()
        finally:
            conn.close()

    def remove_reject_condition(self):
        """撤去固定拒绝条件（删除触发器），并提交。"""
        conn = self.open_sample_db()
        try:
            conn.execute(DROP_REJECT_TRIGGER_SQL)
            conn.commit()
        finally:
            conn.close()

    def register_samples(self):
        """按验收顺序登记 A（image: demo, ui）与 B（audio: demo）。"""
        result_a = self.run_cli(
            "add", str(self.file_a), "--type", "image",
            "--tag", "demo", "--tag", "ui",
        )
        self.assertEqual(result_a.returncode, 0, result_a.stderr)
        self.assertEqual(result_a.stderr, "")
        self.assertEqual(json.loads(result_a.stdout), self.record_a)

        result_b = self.run_cli(
            "add", str(self.file_b), "--type", "audio", "--tag", "demo"
        )
        self.assertEqual(result_b.returncode, 0, result_b.stderr)
        self.assertEqual(result_b.stderr, "")
        self.assertEqual(json.loads(result_b.stdout), self.record_b)

    def retype_a_to_texture(self):
        """对 A 的登记路径提交 retype --type texture。"""
        return self.run_cli("retype", str(self.file_a), "--type", "texture")

    def assert_query(self, extra_args, expected_records):
        result = self.run_cli("query", "--tag", "demo", *extra_args)
        self.assertEqual(
            result.returncode,
            0,
            f"query {extra_args!r} 应成功退出，实际退出码 {result.returncode}，"
            f"标准错误: {result.stderr!r}",
        )
        self.assertEqual(result.stderr, "", f"query {extra_args!r} 的标准错误应为空")
        self.assertEqual(
            json.loads(result.stdout),
            expected_records,
            f"query {extra_args!r} 的持久化记录与预期不符",
        )

    def assert_export(self, expected_records):
        result = self.run_cli("export")
        self.assertEqual(
            result.returncode,
            0,
            f"export 应成功退出，实际退出码 {result.returncode}，"
            f"标准错误: {result.stderr!r}",
        )
        self.assertEqual(result.stderr, "", "export 的标准错误应为空")
        self.assertEqual(
            json.loads(result.stdout),
            expected_records,
            "export 的完整目录记录或登记顺序与预期不符",
        )

    def assert_files_unchanged(self):
        self.assertEqual(
            self.file_a.read_bytes(), self.content_a, "A.bin 文件内容发生变化"
        )
        self.assertEqual(
            self.file_b.read_bytes(), self.content_b, "B.bin 文件内容发生变化"
        )

    def assert_rejection_preserved_originals(self):
        """核对失败后的持久化状态：A、B 原记录完整保留，顺序不变。"""
        # 新进程 export 仍按登记顺序返回 A、B 的原始完整记录。
        self.assert_export([self.record_a, self.record_b])
        # query --tag demo --type image 只返回 A。
        self.assert_query(["--type", "image"], [self.record_a])
        # query --tag demo --type texture 返回 []。
        self.assert_query(["--type", "texture"], [])
        # 两个源文件的字节内容不变。
        self.assert_files_unchanged()

    def test_rejection_reported_as_write_failure_and_preserves_records(self):
        """类型写入被触发器拒绝：报数据库读写失败并保留拒绝文本，旧记录完整保留。"""
        self.register_samples()
        self.install_reject_condition()

        # 表结构仍兼容、读取功能正常：export 原样返回 A、B 的完整记录。
        self.assert_export([self.record_a, self.record_b])

        # 对 A 的登记路径提交 retype --type texture，被固定条件拒绝。
        result = self.retype_a_to_texture()
        self.assertEqual(
            result.returncode,
            2,
            "写入被拒应退出为 2；"
            f"实际退出码 {result.returncode}，输出: {result.stdout!r}，"
            f"错误: {result.stderr!r}",
        )
        self.assertEqual(result.stdout, "", "失败时标准输出应为空")
        self.assertIn(
            "数据库读写失败",
            result.stderr,
            f"标准错误应说明数据库读写失败，实际为: {result.stderr!r}",
        )
        # 保留数据库提供的拒绝原因（触发器拒绝文本）。
        self.assertIn(
            REJECT_MESSAGE,
            result.stderr,
            f"标准错误应保留数据库给出的拒绝文本，实际为: {result.stderr!r}",
        )
        self.assertNotIn("Traceback", result.stderr, "标准错误不应包含调用栈")

        # 失败后旧记录完整保留：登记顺序、完整路径、类型与标签顺序不变。
        self.assert_rejection_preserved_originals()

        # 数据库中 A 的类型与标签未被部分修改。
        conn = self.open_sample_db()
        try:
            asset_rows = conn.execute(
                "SELECT path, type FROM asset ORDER BY id ASC"
            ).fetchall()
            self.assertEqual(
                asset_rows,
                [(self.path_a, "image"), (self.path_b, "audio")],
                "数据库中素材记录或类型发生变化",
            )
            tag_rows = conn.execute(
                "SELECT asset_id, tag, position FROM asset_tag "
                "ORDER BY asset_id, position"
            ).fetchall()
            self.assertEqual(
                [(row[1], row[2]) for row in tag_rows],
                [("demo", 0), ("ui", 1), ("demo", 0)],
                "数据库中标签记录或顺序发生变化",
            )
        finally:
            conn.close()

    def test_retype_succeeds_after_condition_removed(self):
        """同一场景撤去拒绝条件后原样提交同一修改成功，仅 A 的类型变化。"""
        self.register_samples()
        self.install_reject_condition()

        # 先确认带拒绝条件时同一修改失败且旧记录完整保留。
        failed = self.retype_a_to_texture()
        self.assertEqual(failed.returncode, 2, failed.stderr)
        self.assertEqual(failed.stdout, "")
        self.assertIn(REJECT_MESSAGE, failed.stderr)
        self.assert_rejection_preserved_originals()

        # 同一样例库撤去拒绝条件，原样提交同一修改。
        self.remove_reject_condition()
        result = self.retype_a_to_texture()
        self.assertEqual(
            result.returncode,
            0,
            f"撤去拒绝条件后同一修改应成功，标准错误: {result.stderr!r}",
        )
        self.assertEqual(result.stderr, "", "成功时标准错误应为空")
        # 标准输出仅为含 path、type、tags 的 A 记录。
        self.assertEqual(len(result.stdout.strip().splitlines()), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(
            set(payload),
            {"path", "type", "tags"},
            "成功输出应为仅含 path、type、tags 的 JSON 对象",
        )
        self.assertEqual(payload, self.record_a_retyped)
        self.assertEqual(payload["type"], "texture")
        self.assertEqual(payload["tags"], ["demo", "ui"])

        # 新进程 export 仍按 A、B 顺序返回，只有 A 的类型变化，B 保持原样。
        self.assert_export([self.record_a_retyped, self.record_b])
        # 按 demo 和 texture 查询只命中 A。
        self.assert_query(["--type", "texture"], [self.record_a_retyped])
        # 按 demo 和 image 查询返回 []。
        self.assert_query(["--type", "image"], [])
        # 两个源文件的字节内容仍不变。
        self.assert_files_unchanged()


if __name__ == "__main__":
    unittest.main(verbosity=2)
