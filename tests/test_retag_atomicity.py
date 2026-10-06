"""retag 写入中途失败的原子性专项回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / sqlite3 / tempfile），
无需第三方依赖、网络或特殊权限。

固定场景：
- 两个临时演示文件按 A.bin、B.bin 顺序登记：A 类型 image、标签 demo、ui；
  B 类型 audio、标签 demo。
- 在保持现有兼容表结构（asset / asset_tag 及索引）的前提下，仅给样例库
  增加一个 BEFORE INSERT 触发器作为“固定拒绝条件”：只拒绝 blocked 标签
  写入，允许 project 等其他标签写入；既有读取功能不变。
- 执行 ``retag A.bin --tag project --tag blocked``：DELETE 旧标签后，第一个
  新标签 project 写入成功、第二个新标签 blocked 被拒，失败来自第二个新标签
  的写入。参数与数据库读取均有效，预期退出码 2、标准输出为空、标准错误
  说明数据库读写失败且不含调用栈，且事务整体回滚：
  A 的完整旧标签及顺序仍为 ["demo", "ui"]，B 的记录与两条素材的登记顺序
  不变；query project / blocked 均返回 []，query demo 仍按原顺序返回 A、B；
  两个素材文件内容不变。
- 在同一场景中撤去拒绝条件（DROP TRIGGER）后再次提交相同替换命令：
  退出码 0、标准错误为空，输出既有格式的 A 记录，标签为
  ["project", "blocked"]；新进程 export 仍得到 A、B，只有 A 的标签改变。

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

# 固定条件：仅拒绝 blocked 标签写入，错误信息点名 blocked，
# 用于确认失败确实来自第二个新标签的写入；其他标签（如 project）允许写入。
CREATE_REJECT_TRIGGER_SQL = f"""
CREATE TRIGGER {REJECT_TRIGGER}
BEFORE INSERT ON asset_tag
WHEN NEW.tag = 'blocked'
BEGIN
    SELECT RAISE(ABORT, 'rejected by fixed condition: blocked');
END;
"""

DROP_REJECT_TRIGGER_SQL = f"DROP TRIGGER {REJECT_TRIGGER}"


class RetagAtomicityTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "A.bin"
        self.file_b = self.tmp_dir / "B.bin"
        self.content_a = b"demo asset A\n"
        self.content_b = b"demo asset B\n"
        self.file_a.write_bytes(self.content_a)
        self.file_b.write_bytes(self.content_b)
        self.db_path = self.tmp_dir / "catalog.sqlite"

        self.path_a = os.path.realpath(str(self.file_a))
        self.path_b = os.path.realpath(str(self.file_b))
        self.record_a_old = {
            "path": self.path_a,
            "type": "image",
            "tags": ["demo", "ui"],
        }
        self.record_b = {
            "path": self.path_b,
            "type": "audio",
            "tags": ["demo"],
        }
        self.record_a_new = {
            "path": self.path_a,
            "type": "image",
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

    def test_mid_write_failure_keeps_old_tags_then_succeeds_when_condition_removed(
        self,
    ):
        # ---- 准备样例：add 仅用于登记 A、B（A 先、B 后）----
        result = self.run_cli(
            "add", str(self.file_a), "--type", "image", "--tag", "demo", "--tag", "ui"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout), self.record_a_old, "add A 的输出记录与样例不符"
        )

        result = self.run_cli(
            "add", str(self.file_b), "--type", "audio", "--tag", "demo"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout), self.record_b, "add B 的输出记录与样例不符"
        )

        # ---- 安装固定拒绝条件：只拒绝 blocked，允许 project ----
        self.install_reject_condition()

        # 表结构仍兼容、读取功能正常：open_database 的结构校验通过，
        # export 原样返回 A、B。
        self.assert_export([self.record_a_old, self.record_b])

        # 直接核对固定条件语义：project 允许写入（回滚不落库），
        # blocked 写入被数据库拒绝。
        conn = self.open_sample_db()
        try:
            asset_id = conn.execute(
                "SELECT id FROM asset WHERE path = ?", (self.path_a,)
            ).fetchone()[0]
            conn.execute("BEGIN")
            conn.execute(
                "INSERT INTO asset_tag(asset_id, tag, position) VALUES (?, ?, ?)",
                (asset_id, "project", 99),
            )
            conn.rollback()
            with self.assertRaises(
                sqlite3.Error, msg="固定条件应拒绝 blocked 标签写入"
            ):
                conn.execute(
                    "INSERT INTO asset_tag(asset_id, tag, position) "
                    "VALUES (?, ?, ?)",
                    (asset_id, "blocked", 99),
                )
            conn.rollback()
        finally:
            conn.close()
        # 直接探测不落库：记录仍是原样。
        self.assert_export([self.record_a_old, self.record_b])

        # ---- 旧标签已进入替换流程后，第二个新标签写入失败 ----
        result = self.run_cli(
            "retag", str(self.file_a), "--tag", "project", "--tag", "blocked"
        )
        self.assertEqual(
            result.returncode,
            2,
            "写入中途失败应退出为 2；"
            f"实际退出码 {result.returncode}，输出: {result.stdout!r}，"
            f"错误: {result.stderr!r}",
        )
        self.assertEqual(result.stdout, "", "失败时标准输出应为空")
        self.assertNotEqual(result.stderr.strip(), "", "失败时标准错误应说明原因")
        self.assertIn(
            "数据库读写失败",
            result.stderr,
            f"标准错误应说明数据库读写失败，实际为: {result.stderr!r}",
        )
        # 失败来自第二个新标签 blocked 的写入（触发器错误信息点名 blocked）。
        self.assertIn(
            "blocked",
            result.stderr,
            f"失败原因应来自第二个新标签 blocked 的写入，实际为: {result.stderr!r}",
        )
        self.assertNotIn("Traceback", result.stderr, "标准错误不应包含调用栈")

        # ---- 经公开入口重新读取：既有记录完整保留，无部分新标签 ----
        # A 的完整旧标签及顺序不变，B 不变，两条素材登记顺序（A、B）不变。
        self.assert_export([self.record_a_old, self.record_b])
        # 两个新标签均不可查；旧标签 demo 仍按原顺序返回 A、B。
        self.assert_query_tags("project", [])
        self.assert_query_tags("blocked", [])
        self.assert_query_tags("demo", [self.record_a_old, self.record_b])
        # 素材文件内容保持不变。
        self.assertEqual(
            self.file_a.read_bytes(),
            self.content_a,
            "失败回滚后 A.bin 文件内容发生变化",
        )
        self.assertEqual(
            self.file_b.read_bytes(),
            self.content_b,
            "失败回滚后 B.bin 文件内容发生变化",
        )

        # ---- 同一测试场景撤去拒绝条件，再次提交相同替换命令 ----
        self.remove_reject_condition()
        result = self.run_cli(
            "retag", str(self.file_a), "--tag", "project", "--tag", "blocked"
        )
        self.assertEqual(
            result.returncode,
            0,
            "撤去拒绝条件后相同替换应成功；"
            f"实际退出码 {result.returncode}，标准错误: {result.stderr!r}",
        )
        self.assertEqual(result.stderr, "", "成功时标准错误应为空")
        data = json.loads(result.stdout)
        self.assertEqual(
            set(data),
            {"path", "type", "tags"},
            "成功输出应为仅含 path、type、tags 的既有格式 JSON 对象",
        )
        self.assertEqual(
            data,
            self.record_a_new,
            "撤去拒绝条件后 A 的记录应仅把标签替换为 ['project', 'blocked']",
        )

        # ---- 新进程导出：仍得到 A、B，只有 A 的标签改变 ----
        self.assert_export([self.record_a_new, self.record_b])
        # 新标签可查，旧标签 demo 现只命中 B；B 的记录未受影响。
        self.assert_query_tags("project", [self.record_a_new])
        self.assert_query_tags("blocked", [self.record_a_new])
        self.assert_query_tags("demo", [self.record_b])
        # 两个素材文件内容始终不变。
        self.assertEqual(
            self.file_a.read_bytes(),
            self.content_a,
            "替换成功后 A.bin 文件内容发生变化",
        )
        self.assertEqual(
            self.file_b.read_bytes(),
            self.content_b,
            "替换成功后 B.bin 文件内容发生变化",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
