"""add 写入失败归类、恢复成功与真实重复的专项回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以独立子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / sqlite3 / tempfile），
无需第三方依赖、网络或特殊权限。

固定验收场景：
- 先正常登记 A.bin（类型 image，标签依次为 demo、ui）。
- 样例库保持现有兼容表结构（asset / asset_tag 及索引），仅增加一个
  BEFORE INSERT 触发器作为固定拒绝条件：只拒绝 blocked 标签写入，
  拒绝文本固定为 "rejected by fixed condition: blocked"，
  project 等其他标签允许写入。
- 随后登记尚未入库的 B.bin（类型 audio，依次传入 project、blocked）：
  第二个标签被数据库拒绝。该路径此前从未成功登记，因此不得归类为重复登记；
  预期退出码 2、标准输出为空，标准错误说明数据库写入失败并保留数据库提供的
  拒绝文本，不含调用栈，也不声称素材已登记。
- 失败后经新进程核对：export 仍只得到 A 的原始完整记录；
  query project / blocked 均为 []；数据库不残留 B 的素材记录或已写入的
  project 标签；两个源文件内容不变。
- 在同一样例库撤去拒绝条件（DROP TRIGGER）后原样提交 B 的登记：
  退出码 0、标准错误为空，标准输出为仅含 path、type、tags 的单条 JSON 记录，
  标签为 ["project", "blocked"]；新进程 export 按首次登记顺序返回 A、B 各一次。
- 再次登记 B 的等价路径仍按既有重复规则拒绝（退出码 2、标准输出为空、
  标准错误说明重复登记并含冲突的规范绝对路径），原记录不被替换。

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

# 固定条件：仅拒绝 blocked 标签写入，拒绝文本由数据库原样给出，
# 其他标签（如 project）允许写入。
CREATE_REJECT_TRIGGER_SQL = f"""
CREATE TRIGGER {REJECT_TRIGGER}
BEFORE INSERT ON asset_tag
WHEN NEW.tag = 'blocked'
BEGIN
    SELECT RAISE(ABORT, 'rejected by fixed condition: blocked');
END;
"""

DROP_REJECT_TRIGGER_SQL = f"DROP TRIGGER {REJECT_TRIGGER}"


class AddWriteFailureRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "A.bin"
        self.file_b = self.tmp_dir / "B.bin"
        self.content_a = b"fixed content of asset A\n"
        self.content_b = b"fixed content of asset B\n"
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

    def assert_no_trace_of_b_in_database(self):
        """直接核对数据库：不残留 B 的素材记录，也不残留其任何标签。"""
        conn = self.open_sample_db()
        try:
            asset_rows = conn.execute(
                "SELECT id, path, type FROM asset WHERE path = ?", (self.path_b,)
            ).fetchall()
            self.assertEqual(asset_rows, [], "失败后不应残留 B 的素材记录")
            tag_rows = conn.execute(
                """
                SELECT t.tag FROM asset_tag t
                JOIN asset a ON a.id = t.asset_id
                WHERE t.tag IN ('project', 'blocked')
                """
            ).fetchall()
            self.assertEqual(tag_rows, [], "失败后不应残留 project/blocked 标签")
            total_assets = conn.execute("SELECT COUNT(*) FROM asset").fetchone()[0]
            total_tags = conn.execute("SELECT COUNT(*) FROM asset_tag").fetchone()[0]
            self.assertEqual(total_assets, 1, "库中应只保留 A 一条素材记录")
            self.assertEqual(total_tags, 2, "库中应只保留 A 的两个标签")
        finally:
            conn.close()

    def test_write_failure_then_recovery_then_real_duplicate(self):
        # ---- 先正常登记 A：image，标签 demo、ui ----
        result = self.run_cli(
            "add", str(self.file_a), "--type", "image", "--tag", "demo", "--tag", "ui"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout), self.record_a, "add A 的输出记录与样例不符"
        )

        # ---- 安装固定拒绝条件：只拒绝 blocked，允许 project ----
        self.install_reject_condition()

        # 表结构仍兼容、读取功能正常：export 原样返回 A。
        self.assert_export([self.record_a])

        # ---- 登记尚未入库的 B：第二个标签被数据库拒绝 ----
        result = self.run_cli(
            "add",
            str(self.file_b),
            "--type",
            "audio",
            "--tag",
            "project",
            "--tag",
            "blocked",
        )
        self.assertEqual(
            result.returncode,
            2,
            "写入被数据库拒绝时应退出为 2；"
            f"实际退出码 {result.returncode}，输出: {result.stdout!r}，"
            f"错误: {result.stderr!r}",
        )
        self.assertEqual(result.stdout, "", "失败时标准输出应为空")
        self.assertNotEqual(result.stderr.strip(), "", "失败时标准错误应说明原因")
        self.assertIn(
            "数据库写入失败",
            result.stderr,
            f"标准错误应说明数据库写入失败，实际为: {result.stderr!r}",
        )
        self.assertIn(
            "rejected by fixed condition: blocked",
            result.stderr,
            "标准错误应保留数据库提供的拒绝原因，"
            f"实际为: {result.stderr!r}",
        )
        # B 从未成功登记：不得把约束/触发器拒绝误报为素材已登记。
        self.assertNotIn(
            "已登记",
            result.stderr,
            f"未登记路径的写入失败不应被归类为重复登记，实际为: {result.stderr!r}",
        )
        self.assertNotIn("Traceback", result.stderr, "标准错误不应包含调用栈")

        # ---- 失败后经公开入口（新进程）核对状态 ----
        # export 仍只得到 A 的原始完整记录。
        self.assert_export([self.record_a])
        # 查询 project / blocked 都得到 []。
        self.assert_query_tags("project", [])
        self.assert_query_tags("blocked", [])
        # A 的原有标签查询不受影响。
        self.assert_query_tags("demo", [self.record_a])
        # 数据库不残留 B 的素材记录或已写入的 project 标签。
        self.assert_no_trace_of_b_in_database()
        # 两个源文件内容不变。
        self.assertEqual(
            self.file_a.read_bytes(), self.content_a, "失败后 A.bin 内容发生变化"
        )
        self.assertEqual(
            self.file_b.read_bytes(), self.content_b, "失败后 B.bin 内容发生变化"
        )

        # ---- 同一样例库撤去拒绝条件后，原样提交 B 的登记 ----
        self.remove_reject_condition()
        result = self.run_cli(
            "add",
            str(self.file_b),
            "--type",
            "audio",
            "--tag",
            "project",
            "--tag",
            "blocked",
        )
        self.assertEqual(
            result.returncode,
            0,
            "撤去拒绝条件后原样登记 B 应成功；"
            f"实际退出码 {result.returncode}，标准错误: {result.stderr!r}",
        )
        self.assertEqual(result.stderr, "", "成功时标准错误应为空")
        lines = result.stdout.strip().splitlines()
        self.assertEqual(len(lines), 1, "成功标准输出应为单行 JSON 记录")
        data = json.loads(result.stdout)
        self.assertIsInstance(data, dict)
        self.assertEqual(
            set(data),
            {"path", "type", "tags"},
            "成功输出应仅含 path、type、tags",
        )
        self.assertEqual(data, self.record_b, "B 的登记记录与样例不符")

        # ---- 新进程导出：按首次登记顺序返回 A、B，各一次 ----
        self.assert_export([self.record_a, self.record_b])
        self.assert_query_tags("project", [self.record_b])
        self.assert_query_tags("blocked", [self.record_b])
        # 两个源文件内容仍不变。
        self.assertEqual(
            self.file_a.read_bytes(), self.content_a, "恢复成功后 A.bin 内容发生变化"
        )
        self.assertEqual(
            self.file_b.read_bytes(), self.content_b, "恢复成功后 B.bin 内容发生变化"
        )

        # ---- 真实重复：再次登记 B 的等价路径仍按既有规则拒绝 ----
        tmp_rel = os.path.relpath(self.tmp_dir, PROJECT_ROOT)
        tmp_name = os.path.basename(tmp_rel)
        equivalent = os.path.join(tmp_rel, ".", "..", tmp_name, "B.bin")
        result = self.run_cli(
            "add",
            equivalent,
            "--type",
            "video",
            "--tag",
            "replacement",
        )
        self.assertEqual(
            result.returncode,
            2,
            f"重复登记应退出为 2，实际 {result.returncode}，"
            f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )
        self.assertEqual(result.stdout, "", "重复拒绝时标准输出应为空")
        self.assertIn("重复登记", result.stderr, "标准错误应说明重复登记")
        self.assertIn(
            self.path_b, result.stderr, "标准错误应包含冲突的规范绝对路径"
        )
        self.assertNotIn(
            "数据库写入失败",
            result.stderr,
            "真实重复应归类为重复登记而非数据库写入失败",
        )
        self.assertNotIn("Traceback", result.stderr, "标准错误不应包含调用栈")

        # 原记录不被替换：类型仍为 audio，标签仍为 project、blocked，
        # export 仍是 A、B 各一次，replacement 标签查询为空。
        self.assert_export([self.record_a, self.record_b])
        self.assert_query_tags("replacement", [])
        self.assertEqual(
            self.file_b.read_bytes(), self.content_b, "重复登记后 B.bin 内容发生变化"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
