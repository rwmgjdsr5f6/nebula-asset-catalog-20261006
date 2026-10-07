"""add 写入被数据库拒绝时的失败原因归类回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / sqlite3 / tempfile），
无需第三方依赖、网络或特殊权限。

固定场景：
- 两个内容固定、路径不同的临时演示文件 A.bin、B.bin。
- 先正常登记 A：类型 image，标签依次为 demo、ui。
- 样例库保持原有兼容表结构（asset / asset_tag 及索引），另加一个
  BEFORE INSERT 触发器作为固定拒绝条件：只拒绝 blocked 标签写入，
  拒绝文本为 ``rejected by fixed condition: blocked``，其他标签允许写入。

覆盖：
- 失败归类：登记尚未入库的 B（类型 audio，标签依次为 project、blocked），
  第二个标签被触发器拒绝时，退出码 2、标准输出为空，标准错误说明
  数据库写入失败并保留数据库给出的拒绝文本，不再声称素材已登记，
  也不输出调用栈；
- 失败原子性：失败后由新进程 export 仍只得到 A 的原始完整记录，
  查询 project 或 blocked 都得到 []，数据库不残留 B 的素材记录或
  已经写入的 project 标签，两个源文件内容不变；
- 恢复成功：在同一样例库撤去拒绝条件后原样提交 B 的登记，退出码 0、
  标准错误为空，标准输出是仅含 path、type、tags 的单条 JSON 记录，
  标签为 ["project", "blocked"]；新进程导出按首次登记顺序返回 A、B，
  各一次；
- 真实重复：恢复成功后再次以等价路径登记 B，仍按既有重复规则拒绝
  （退出码 2、标准输出为空、标准错误说明重复登记并含冲突的规范绝对
  路径），原记录不被替换。

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

REJECT_TRIGGER = "reject_blocked_tag_insert"
REJECT_MESSAGE = "rejected by fixed condition: blocked"

# 固定条件：仅拒绝 blocked 标签写入，拒绝文本点名 blocked；
# 其他标签（如 project）允许写入。
CREATE_REJECT_TRIGGER_SQL = f"""
CREATE TRIGGER {REJECT_TRIGGER}
BEFORE INSERT ON asset_tag
WHEN NEW.tag = 'blocked'
BEGIN
    SELECT RAISE(ABORT, '{REJECT_MESSAGE}');
END;
"""

DROP_REJECT_TRIGGER_SQL = f"DROP TRIGGER {REJECT_TRIGGER}"


class AddWriteFailureRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "A.bin"
        self.file_b = self.tmp_dir / "B.bin"
        self.content_a = b"add write-failure sample A\n"
        self.content_b = b"add write-failure sample B\n"
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
            "tags": ["project", "blocked"],
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
        """加入仅拒绝 blocked 标签写入的固定条件（触发器），并提交。"""
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

    def add_a(self):
        """正常登记 A：类型 image，标签依次为 demo、ui。"""
        result = self.run_cli(
            "add", str(self.file_a), "--type", "image",
            "--tag", "demo", "--tag", "ui",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), self.record_a)

    def add_b(self):
        """原样提交 B 的登记：类型 audio，标签依次为 project、blocked。"""
        return self.run_cli(
            "add", str(self.file_b), "--type", "audio",
            "--tag", "project", "--tag", "blocked",
        )

    def assert_query_tags(self, tag, expected_records):
        result = self.run_cli("query", "--tag", tag)
        self.assertEqual(
            result.returncode,
            0,
            f"query {tag!r} 应成功退出，实际退出码 {result.returncode}，"
            f"标准错误: {result.stderr!r}",
        )
        self.assertEqual(result.stderr, "", f"query {tag!r} 的标准错误应为空")
        self.assertEqual(
            json.loads(result.stdout),
            expected_records,
            f"query {tag!r} 的持久化记录与预期不符",
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

    def test_trigger_rejection_reported_as_write_failure_and_rolls_back(self):
        """尚未登记的路径被触发器拒绝：报数据库写入失败而非已登记，且整体回滚。"""
        self.add_a()
        self.install_reject_condition()

        # 表结构仍兼容、读取功能正常：export 原样返回 A 的完整记录。
        self.assert_export([self.record_a])

        # 登记尚未入库的 B：第一个标签 project 允许写入，
        # 第二个标签 blocked 被固定条件拒绝。
        result = self.add_b()
        self.assertEqual(
            result.returncode,
            2,
            "写入被拒应退出为 2；"
            f"实际退出码 {result.returncode}，输出: {result.stdout!r}，"
            f"错误: {result.stderr!r}",
        )
        self.assertEqual(result.stdout, "", "失败时标准输出应为空")
        self.assertIn(
            "数据库写入失败",
            result.stderr,
            f"标准错误应说明数据库写入失败，实际为: {result.stderr!r}",
        )
        # 保留数据库提供的拒绝原因（触发器拒绝文本）。
        self.assertIn(
            REJECT_MESSAGE,
            result.stderr,
            f"标准错误应保留数据库给出的拒绝文本，实际为: {result.stderr!r}",
        )
        # 不再把约束/触发器拒绝误报为重复登记。
        self.assertNotIn(
            "已登记",
            result.stderr,
            f"未登记路径的写入失败不应声称素材已登记: {result.stderr!r}",
        )
        self.assertNotIn("Traceback", result.stderr, "标准错误不应包含调用栈")

        # 失败后由新进程读取：export 仍只得到 A 的原始完整记录。
        self.assert_export([self.record_a])
        # 查询 project 或 blocked 都得到 []。
        self.assert_query_tags("project", [])
        self.assert_query_tags("blocked", [])
        self.assert_query_tags("demo", [self.record_a])

        # 数据库不残留 B 的素材记录或已经写入的 project 标签。
        conn = self.open_sample_db()
        try:
            asset_rows = conn.execute(
                "SELECT id, path, type FROM asset"
            ).fetchall()
            self.assertEqual(
                [(row[1], row[2]) for row in asset_rows],
                [(self.path_a, "image")],
                "数据库残留 B 的素材记录",
            )
            tag_rows = conn.execute(
                "SELECT asset_id, tag, position FROM asset_tag ORDER BY asset_id, position"
            ).fetchall()
            self.assertEqual(
                [(row[1], row[2]) for row in tag_rows],
                [("demo", 0), ("ui", 1)],
                "数据库残留已写入的 project 标签或其他孤儿标签",
            )
        finally:
            conn.close()

        # 两个源文件的内容不变。
        self.assert_files_unchanged()

    def test_add_succeeds_after_condition_removed(self):
        """撤去拒绝条件后原样提交 B 成功；新进程导出按登记顺序返回 A、B。"""
        self.add_a()
        self.install_reject_condition()

        # 先确认带拒绝条件时同一登记失败。
        failed = self.add_b()
        self.assertEqual(failed.returncode, 2, failed.stderr)
        self.assertEqual(failed.stdout, "")

        # 同一样例库撤去拒绝条件，原样提交 B 的登记。
        self.remove_reject_condition()
        result = self.add_b()
        self.assertEqual(
            result.returncode,
            0,
            f"撤去拒绝条件后原样登记应成功，标准错误: {result.stderr!r}",
        )
        self.assertEqual(result.stderr, "", "成功时标准错误应为空")
        # 标准输出是仅含 path、type、tags 的单条 JSON 记录。
        self.assertEqual(len(result.stdout.strip().splitlines()), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(
            set(payload),
            {"path", "type", "tags"},
            "成功输出应为仅含 path、type、tags 的 JSON 对象",
        )
        self.assertEqual(payload, self.record_b)
        self.assertEqual(payload["tags"], ["project", "blocked"])

        # 新进程导出按首次登记顺序返回 A、B，各一次。
        self.assert_export([self.record_a, self.record_b])
        self.assert_query_tags("project", [self.record_b])
        self.assert_query_tags("blocked", [self.record_b])
        self.assert_query_tags("demo", [self.record_a])
        self.assert_files_unchanged()

    def test_true_duplicate_still_rejected_and_record_not_replaced(self):
        """真实重复的规范路径仍按既有规则拒绝，原记录不被替换。"""
        self.add_a()
        result = self.add_b()
        self.assertEqual(result.returncode, 0, result.stderr)

        # 同一文件的等价路径写法：绝对路径、含 . / .. 的写法。
        spellings = [
            ("绝对路径", str(self.file_b)),
            ("含 . 的等价路径", os.path.join(str(self.tmp_dir), ".", "B.bin")),
            (
                "含 .. 的等价路径",
                os.path.join(
                    str(self.tmp_dir), "..", self.tmp_dir.name, "B.bin"
                ),
            ),
        ]
        for label, spelling in spellings:
            with self.subTest(写法=label):
                dup = self.run_cli(
                    "add", spelling, "--type", "video", "--tag", "replacement",
                )
                self.assertEqual(
                    dup.returncode,
                    2,
                    f"{label}: 期望退出码 2，实际 {dup.returncode}，"
                    f"stdout={dup.stdout!r} stderr={dup.stderr!r}",
                )
                self.assertEqual(
                    dup.stdout, "", f"{label}: 期望标准输出为空"
                )
                self.assertIn(
                    "重复登记",
                    dup.stderr,
                    f"{label}: 标准错误未说明重复登记: {dup.stderr!r}",
                )
                self.assertIn(
                    self.path_b,
                    dup.stderr,
                    f"{label}: 标准错误未指出冲突的规范绝对路径",
                )
                self.assertNotIn(
                    "Traceback", dup.stderr, f"{label}: 标准错误含调用栈"
                )

                # 原记录不被替换：类型、标签与登记顺序均不变。
                self.assert_export([self.record_a, self.record_b])
                self.assert_query_tags("replacement", [])

        self.assert_files_unchanged()


if __name__ == "__main__":
    unittest.main(verbosity=2)
