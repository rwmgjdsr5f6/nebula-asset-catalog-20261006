"""retag 写入中途失败的原子性专项回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile / sqlite3），
无需第三方依赖、网络或特殊权限。

固定场景：

1. 用 add 按 A.bin、B.bin 顺序登记两个临时演示素材：A 类型为 image、
   标签依次为 demo、ui；B 类型为 audio、标签为 demo。
2. 在保持现有兼容表结构（asset / asset_tag）不变的前提下，向样例库
   安装一个固定条件：仅拒绝写入 blocked 标签（BEFORE INSERT 触发器），
   project 等其他标签的写入不受影响；安装后目录的 query / export 等
   正常读取功能保持可用。
3. 执行 ``retag A.bin --tag project --tag blocked``：旧标签删除与
   第一个新标签 project 写入已进入替换流程，第二个新标签 blocked
   触发固定条件而失败。断言退出码 2、标准输出为空、标准错误说明
   数据库读写失败、原因来自 blocked 写入且不含调用栈。
4. 由全新进程重新读取目录：A 的完整旧标签及顺序仍是 ["demo", "ui"]，
   B 的记录与 A、B 的登记顺序均不变；query project / blocked 均为 []，
   query demo 仍按原顺序返回 A、B；两个素材文件内容不变。
5. 在同一测试场景中撤去该固定条件后再次提交完全相同的替换命令：
   退出码 0、标准错误为空，标准输出为既有格式的 A 记录，标签为
   ["project", "blocked"]；新进程 export 仍得到 A、B，只有 A 的标签
   改变；素材文件内容仍保持不变。

所有样例文件与数据库均在临时目录中自行准备并清理；重复执行结果一致。
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

TRIGGER_NAME = "reject_blocked_tag_only"
# 固定条件的拒绝原因文本：失败时应能在标准错误中看到 blocked，
# 以确认失败来自第二个新标签的写入而非参数或读取阶段。
BLOCKED_REASON = "固定条件拒绝写入 blocked 标签"

CONTENT_A = "temporary demo asset A\n"
CONTENT_B = "temporary demo asset B\n"


class RetagAtomicityTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "A.bin"
        self.file_b = self.tmp_dir / "B.bin"
        self.file_a.write_text(CONTENT_A, encoding="utf-8")
        self.file_b.write_text(CONTENT_B, encoding="utf-8")
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

        self._register_samples_in_order()

    def tearDown(self):
        self._tmp.cleanup()

    # ------------------------------------------------------------------ helpers

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

    def _register_samples_in_order(self):
        """add 仅用于准备样例：先登记 A（image: demo, ui），再登记 B（audio: demo）。"""
        result_a = self.run_cli(
            "add", str(self.file_a),
            "--type", "image", "--tag", "demo", "--tag", "ui",
        )
        self.assertEqual(
            result_a.returncode, 0,
            f"准备样例 A 的 add 应成功：{result_a.stderr}",
        )
        self.assertEqual(result_a.stderr, "", "准备样例 A 时标准错误应为空")
        self.assertEqual(
            json.loads(result_a.stdout), self.record_a_old,
            "add A 的输出应与其登记记录一致",
        )

        result_b = self.run_cli(
            "add", str(self.file_b),
            "--type", "audio", "--tag", "demo",
        )
        self.assertEqual(
            result_b.returncode, 0,
            f"准备样例 B 的 add 应成功：{result_b.stderr}",
        )
        self.assertEqual(result_b.stderr, "", "准备样例 B 时标准错误应为空")
        self.assertEqual(
            json.loads(result_b.stdout), self.record_b,
            "add B 的输出应与其登记记录一致",
        )

    def _connect(self):
        return sqlite3.connect(str(self.db_path))

    def install_block_condition(self):
        """安装固定条件：仅拒绝 blocked 标签写入，其他标签一律放行。

        只新增触发器，不修改 asset / asset_tag 的兼容表结构与既有数据。
        """
        conn = self._connect()
        try:
            conn.execute(
                f"""
                CREATE TRIGGER {TRIGGER_NAME}
                BEFORE INSERT ON asset_tag
                WHEN NEW.tag = 'blocked'
                BEGIN
                    SELECT RAISE(ABORT, '{BLOCKED_REASON}');
                END
                """
            )
            conn.commit()
        finally:
            conn.close()

    def remove_block_condition(self):
        """撤去固定条件：删除触发器，表结构与数据不做其他改动。"""
        conn = self._connect()
        try:
            conn.execute(f"DROP TRIGGER {TRIGGER_NAME}")
            conn.commit()
        finally:
            conn.close()

    def probe_tag_write_allowed(self, tag):
        """在独立事务中试探某标签能否写入 asset_tag，探测后立即回滚。

        用于在 retag 之前确认：参数与数据库读写均有效、project 可写而
        blocked 被固定条件拒绝；探测本身不留下任何数据。
        """
        conn = self._connect()
        try:
            asset_id = conn.execute(
                "SELECT id FROM asset WHERE path = ?", (self.path_a,)
            ).fetchone()[0]
            try:
                conn.execute(
                    "INSERT INTO asset_tag(asset_id, tag, position) "
                    "VALUES (?, ?, ?)",
                    (asset_id, tag, 999),
                )
            except sqlite3.Error:
                conn.rollback()
                return False
            conn.rollback()
            return True
        finally:
            conn.close()

    def query_tags(self, *tags):
        """新进程 query --tag ...，返回 (returncode, parsed_records, stderr)。"""
        result = self.run_cli("query", *[part for tag in tags for part in ("--tag", tag)])
        self.assertEqual(
            result.returncode, 0,
            f"query {tags} 应成功：{result.stderr}",
        )
        self.assertEqual(result.stderr, "", f"query {tags} 的标准错误应为空")
        return json.loads(result.stdout)

    def export_records(self):
        """新进程 export，返回解析后的完整目录记录。"""
        result = self.run_cli("export")
        self.assertEqual(
            result.returncode, 0,
            f"export 应成功：{result.stderr}",
        )
        self.assertEqual(result.stderr, "", "export 的标准错误应为空")
        return json.loads(result.stdout)

    # ------------------------------------------------------------------ test

    def test_mid_write_failure_is_atomic_and_retry_succeeds_after_condition_removed(self):
        # 安装“仅拒绝 blocked”的固定条件。
        self.install_block_condition()
        self.addCleanup(self._drop_trigger_if_exists)

        # 安装条件后样例库仍可正常读取：A、B 记录与登记顺序不变。
        self.assertEqual(
            self.export_records(),
            [self.record_a_old, self.record_b],
            "安装固定条件不应改变兼容表结构中的既有记录",
        )

        # 直接在库内确认条件语义：project 允许写入、blocked 被拒绝；
        # 两次探测都回滚，不影响既有数据。说明随后失败只可能来自
        # 替换流程中第二个新标签的写入，而非参数无效或数据库不可读写。
        self.assertTrue(
            self.probe_tag_write_allowed("project"),
            "固定条件应允许 project 标签写入",
        )
        self.assertFalse(
            self.probe_tag_write_allowed("blocked"),
            "固定条件应拒绝 blocked 标签写入",
        )
        self.assertEqual(
            self.export_records(),
            [self.record_a_old, self.record_b],
            "条件探测不应在库中留下任何标签",
        )

        # 旧标签（demo/ui）的删除与第一个新标签 project 的写入已进入
        # 同一替换事务，第二个新标签 blocked 写入时触发固定条件失败。
        failed = self.run_cli(
            "retag", str(self.file_a),
            "--tag", "project", "--tag", "blocked",
        )
        self.assertEqual(
            failed.returncode, 2,
            f"写入中途失败应退出为 2，实际 {failed.returncode}；"
            f"stdout={failed.stdout!r} stderr={failed.stderr!r}",
        )
        self.assertEqual(
            failed.stdout, "",
            "失败时标准输出应为空，不能输出部分替换后的记录",
        )
        stderr_lines = failed.stderr.strip().splitlines()
        self.assertEqual(
            len(stderr_lines), 1,
            f"失败时标准错误应为单行说明，实际为 {stderr_lines!r}",
        )
        self.assertIn(
            "数据库读写失败", failed.stderr,
            f"标准错误应说明数据库读写失败，实际为 {failed.stderr!r}",
        )
        self.assertIn(
            "blocked", failed.stderr,
            f"失败原因应来自第二个新标签 blocked 的写入，实际为 {failed.stderr!r}",
        )
        self.assertNotIn(
            "Traceback", failed.stderr,
            "标准错误不应包含调用栈",
        )

        # —— 通过公开入口核对：失败后既有记录完整保留、无部分新标签 ——
        # A 的完整旧标签及顺序不变，B 不变，A、B 登记顺序不变。
        self.assertEqual(
            self.export_records(),
            [self.record_a_old, self.record_b],
            "写入中途失败后，旧标签与其他素材记录应完整保留，"
            "不得留下部分新标签或改变登记顺序",
        )
        # 两个新标签都不得使任何素材入选。
        self.assertEqual(
            self.query_tags("project"), [],
            "失败后 query project 应返回 []，不得留下已进入流程的第一个新标签",
        )
        self.assertEqual(
            self.query_tags("blocked"), [],
            "失败后 query blocked 应返回 []",
        )
        # 旧标签查询仍按原顺序返回 A、B，A 的标签顺序仍是 demo、ui。
        self.assertEqual(
            self.query_tags("demo"),
            [self.record_a_old, self.record_b],
            "失败后 query demo 仍应按原登记顺序返回 A、B",
        )

        # 素材文件内容保持不变（retag 只读元数据）。
        self.assertEqual(
            self.file_a.read_bytes(), CONTENT_A.encode("utf-8"),
            "失败后 A.bin 文件内容应保持不变",
        )
        self.assertEqual(
            self.file_b.read_bytes(), CONTENT_B.encode("utf-8"),
            "失败后 B.bin 文件内容应保持不变",
        )

        # —— 同一测试场景中撤去固定条件，再次提交完全相同的替换命令 ——
        self.remove_block_condition()
        self.assertTrue(
            self.probe_tag_write_allowed("blocked"),
            "撤去条件后 blocked 标签应可正常写入",
        )

        retried = self.run_cli(
            "retag", str(self.file_a),
            "--tag", "project", "--tag", "blocked",
        )
        self.assertEqual(
            retried.returncode, 0,
            f"撤去条件后重试应退出为 0，实际 {retried.returncode}；"
            f"stderr={retried.stderr!r}",
        )
        self.assertEqual(
            retried.stderr, "",
            f"成功时标准错误应为空，实际为 {retried.stderr!r}",
        )
        new_record = json.loads(retried.stdout)
        self.assertEqual(
            set(new_record), {"path", "type", "tags"},
            "成功输出应保持既有记录格式，仅含 path、type、tags",
        )
        self.assertEqual(
            new_record, self.record_a_new,
            "重试成功后 A 的记录标签应为 ['project', 'blocked']，"
            "路径与类型不变",
        )

        # 新进程导出：仍得到 A、B，只有 A 的标签改变，顺序不变。
        self.assertEqual(
            self.export_records(),
            [self.record_a_new, self.record_b],
            "撤去条件成功替换后，export 应只反映 A 的标签变化",
        )
        # 新进程查询：新标签命中 A，被移除的旧标签只命中 B。
        self.assertEqual(
            self.query_tags("project"), [self.record_a_new],
            "替换成功后 query project 应只返回 A 的新记录",
        )
        self.assertEqual(
            self.query_tags("blocked"), [self.record_a_new],
            "替换成功后 query blocked 应只返回 A 的新记录",
        )
        self.assertEqual(
            self.query_tags("demo"), [self.record_b],
            "替换成功后 query demo 应只返回标签未变的 B",
        )

        # 素材文件内容在成功替换后仍保持不变。
        self.assertEqual(
            self.file_a.read_bytes(), CONTENT_A.encode("utf-8"),
            "成功替换后 A.bin 文件内容应保持不变",
        )
        self.assertEqual(
            self.file_b.read_bytes(), CONTENT_B.encode("utf-8"),
            "成功替换后 B.bin 文件内容应保持不变",
        )

    def _drop_trigger_if_exists(self):
        """清理钩子：无论测试结果如何都撤去固定条件（临时库随之删除）。"""
        if not self.db_path.exists():
            return
        conn = self._connect()
        try:
            conn.execute(f"DROP TRIGGER IF EXISTS {TRIGGER_NAME}")
            conn.commit()
        except sqlite3.Error:
            pass
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
