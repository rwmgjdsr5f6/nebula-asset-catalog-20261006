"""retag --append 标签追加的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / sqlite3 / tempfile），
无需第三方依赖、网络或特殊权限。

固定场景：两个临时演示文件按 A.bin、B.bin 顺序登记——A 类型 image、
标签依次为 demo、ui；B 类型 audio、标签 demo。

覆盖：
- 追加成功：``retag A.bin --append --tag " ui " --tag project --tag demo``
  退出码 0、标准错误为空，标准输出仅为含 path、type、tags 的 JSON 对象，
  tags 为 ["demo", "ui", "project"]：新标签去除首尾空白、按输入顺序去重，
  原标签及顺序保留，已存在的标签不移动，新标签接在末尾；
- 重复追加相同命令仍成功，标签不增加也不重排（幂等）；
- 比较区分大小写：ui 与 UI 是两个标签；
- 等价路径（含 . 与 .. 的写法）定位同一记录；
- 源文件已删除或原路径变成目录时仍能追加，素材内容不被读取或改写；
- 不带 --append 时仍执行既有完整替换规则；
- 新进程 export 仍按 A、B 顺序返回；query --tag project 只返回 A，
  query --tag demo 仍返回两条素材；
- 追加写入中途失败（固定触发器拒绝第二个新标签）时整体回滚：
  原记录与标签顺序完整保留，不留下部分新增标签；
- 缺少素材路径、缺少 --tag、任一标签为空或只有空白、传入不支持的参数、
  目标未登记（错误含规范路径）、数据库无法打开时：退出码 2、标准输出
  为空、标准错误说明原因且不含调用栈；参数错误不创建数据库；
  数据库不存在但父目录存在时创建空库后报告未登记，父目录缺失时不补建目录；
- --append 只属于 retag：add / query / export 收到它按参数错误拒绝。

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

# 固定拒绝条件：仅拒绝 blocked 标签写入，用于验证追加中途失败的整体回滚。
CREATE_REJECT_TRIGGER_SQL = """
CREATE TRIGGER reject_blocked_tag_insert
BEFORE INSERT ON asset_tag
WHEN NEW.tag = 'blocked'
BEGIN
    SELECT RAISE(ABORT, 'rejected by fixed condition: blocked');
END;
"""

DROP_REJECT_TRIGGER_SQL = "DROP TRIGGER reject_blocked_tag_insert"


class RetagAppendRegressionTest(unittest.TestCase):
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
        self.record_a_appended = {
            "path": self.path_a,
            "type": "image",
            "tags": ["demo", "ui", "project"],
        }

        # 每个用例都从相同的已登记样例出发。
        self._add(str(self.file_a), "image", ["demo", "ui"], self.record_a_old)
        self._add(str(self.file_b), "audio", ["demo"], self.record_b)

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args, db=None):
        """以全新进程运行 python -m asset_catalog，返回 CompletedProcess。"""
        cmd = [
            sys.executable,
            "-m",
            "asset_catalog",
            "--db",
            str(db if db is not None else self.db_path),
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

    def _add(self, path, asset_type, tags, expected):
        cmd = ["add", path, "--type", asset_type]
        for tag in tags:
            cmd += ["--tag", tag]
        result = self.run_cli(*cmd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected, "add 输出与样例不符")

    def assert_success_record(self, result, expected):
        self.assertEqual(
            result.returncode,
            0,
            f"应成功退出，实际退出码 {result.returncode}，标准错误: {result.stderr!r}",
        )
        self.assertEqual(result.stderr, "", "成功时标准错误应为空")
        data = json.loads(result.stdout)
        self.assertEqual(
            set(data),
            {"path", "type", "tags"},
            "成功输出应为仅含 path、type、tags 的既有格式 JSON 对象",
        )
        self.assertEqual(data, expected, "输出记录与预期不符")

    def assert_failure(self, result, *fragments):
        self.assertEqual(
            result.returncode,
            2,
            f"应退出为 2，实际退出码 {result.returncode}，输出: {result.stdout!r}",
        )
        self.assertEqual(result.stdout, "", "失败时标准输出应为空")
        self.assertNotEqual(result.stderr.strip(), "", "失败时标准错误应说明原因")
        for fragment in fragments:
            self.assertIn(
                fragment, result.stderr, f"标准错误应包含 {fragment!r}，实际为: {result.stderr!r}"
            )
        self.assertNotIn("Traceback", result.stderr, "标准错误不应包含调用栈")

    def assert_export(self, expected_records):
        result = self.run_cli("export")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            expected_records,
            "export 的完整目录记录或登记顺序与预期不符",
        )

    def assert_query_tags(self, tag, expected_records):
        result = self.run_cli("query", "--tag", tag)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            expected_records,
            f"query {tag!r} 的持久化记录与预期不符",
        )

    def assert_files_untouched(self):
        self.assertEqual(self.file_a.read_bytes(), self.content_a, "A.bin 内容被改写")
        self.assertEqual(self.file_b.read_bytes(), self.content_b, "B.bin 内容被改写")

    # ---- 验收主场景 ----

    def test_append_acceptance_scenario(self):
        # 追加：" ui " 去空白后已存在不移动，project 接在末尾，demo 已存在。
        result = self.run_cli(
            "retag",
            str(self.file_a),
            "--append",
            "--tag",
            " ui ",
            "--tag",
            "project",
            "--tag",
            "demo",
        )
        self.assert_success_record(result, self.record_a_appended)

        # 重复追加仍成功，标签不增加也不重排。
        result = self.run_cli(
            "retag",
            str(self.file_a),
            "--append",
            "--tag",
            " ui ",
            "--tag",
            "project",
            "--tag",
            "demo",
        )
        self.assert_success_record(result, self.record_a_appended)

        # 新进程读取：export 仍按 A、B 顺序；project 只命中 A；demo 仍命中两条。
        self.assert_export([self.record_a_appended, self.record_b])
        self.assert_query_tags("project", [self.record_a_appended])
        self.assert_query_tags("demo", [self.record_a_appended, self.record_b])
        self.assert_query_tags("ui", [self.record_a_appended])
        self.assert_files_untouched()

    def test_append_is_case_sensitive(self):
        # ui 与 UI 是两个标签：追加 UI 接在末尾，再追加 ui 不增加也不移动。
        result = self.run_cli("retag", str(self.file_a), "--append", "--tag", "UI")
        expected = {**self.record_a_old, "tags": ["demo", "ui", "UI"]}
        self.assert_success_record(result, expected)

        result = self.run_cli("retag", str(self.file_a), "--append", "--tag", "ui")
        self.assert_success_record(result, expected)
        self.assert_export([expected, self.record_b])

    def test_append_dedupes_within_input_and_keeps_input_order(self):
        # 输入内按首次出现顺序去重；全部为新标签时按输入顺序接在末尾。
        result = self.run_cli(
            "retag",
            str(self.file_a),
            "--append",
            "--tag",
            " z ",
            "--tag",
            "a",
            "--tag",
            "z",
        )
        self.assert_success_record(
            result, {**self.record_a_old, "tags": ["demo", "ui", "z", "a"]}
        )

    def test_equivalent_path_locates_same_record(self):
        equivalent = str(self.tmp_dir / "sub" / ".." / "A.bin")
        result = self.run_cli(
            "retag", equivalent, "--append", "--tag", "project"
        )
        self.assert_success_record(result, self.record_a_appended)
        self.assert_export([self.record_a_appended, self.record_b])

    def test_deleted_or_directory_source_still_appendable(self):
        # 源文件已删除仍可追加。
        self.file_a.unlink()
        result = self.run_cli("retag", str(self.file_a), "--append", "--tag", "project")
        self.assert_success_record(result, self.record_a_appended)

        # 原路径变成目录仍可追加。
        self.file_b.unlink()
        self.file_b.mkdir()
        result = self.run_cli("retag", str(self.file_b), "--append", "--tag", "gone")
        self.assert_success_record(
            result, {**self.record_b, "tags": ["demo", "gone"]}
        )
        self.assert_export(
            [self.record_a_appended, {**self.record_b, "tags": ["demo", "gone"]}]
        )

    def test_replace_mode_unchanged_without_append(self):
        result = self.run_cli(
            "retag", str(self.file_a), "--tag", "project", "--tag", "project"
        )
        self.assert_success_record(
            result, {**self.record_a_old, "tags": ["project"]}
        )
        self.assert_export([{**self.record_a_old, "tags": ["project"]}, self.record_b])
        self.assert_query_tags("demo", [self.record_b])

    # ---- 追加失败的整体回滚 ----

    def test_mid_append_failure_rolls_back_then_succeeds_when_condition_removed(self):
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(CREATE_REJECT_TRIGGER_SQL)
            conn.commit()
        finally:
            conn.close()

        # 第一个新标签 project 允许写入、第二个新标签 blocked 被拒：
        # 失败来自追加流程中途，整体回滚，不留部分新增标签。
        result = self.run_cli(
            "retag",
            str(self.file_a),
            "--append",
            "--tag",
            "project",
            "--tag",
            "blocked",
        )
        self.assert_failure(result, "数据库读写失败", "blocked")

        self.assert_export([self.record_a_old, self.record_b])
        self.assert_query_tags("project", [])
        self.assert_query_tags("blocked", [])
        self.assert_query_tags("demo", [self.record_a_old, self.record_b])
        self.assert_files_untouched()

        # 撤去拒绝条件后相同命令成功：新标签按输入顺序接在末尾。
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(DROP_REJECT_TRIGGER_SQL)
            conn.commit()
        finally:
            conn.close()
        result = self.run_cli(
            "retag",
            str(self.file_a),
            "--append",
            "--tag",
            "project",
            "--tag",
            "blocked",
        )
        expected = {**self.record_a_old, "tags": ["demo", "ui", "project", "blocked"]}
        self.assert_success_record(result, expected)
        self.assert_export([expected, self.record_b])
        self.assert_files_untouched()

    # ---- 参数与数据错误 ----

    def test_missing_path_or_tag_or_unknown_argument(self):
        self.assert_failure(self.run_cli("retag", "--append", "--tag", "x"))
        self.assert_failure(self.run_cli("retag", str(self.file_a), "--append"))
        self.assert_failure(
            self.run_cli("retag", str(self.file_a), "--append", "--tag", "   ")
        )
        self.assert_failure(
            self.run_cli(
                "retag", str(self.file_a), "--append", "--tag", "x", "--bogus"
            )
        )
        # 参数错误不创建数据库：对全新库路径提交参数错误的追加，
        # 库文件不应出现。
        fresh_db = self.tmp_dir / "param_error.sqlite"
        self.assert_failure(
            self.run_cli(
                "retag", str(self.file_a), "--append", "--tag", "  ", db=fresh_db
            )
        )
        self.assertFalse(fresh_db.exists(), "参数错误不应创建数据库")
        # 全部失败之后既有记录保持不变。
        self.assert_export([self.record_a_old, self.record_b])

    def test_unregistered_target_reports_canonical_path(self):
        missing = self.tmp_dir / "nope.bin"
        result = self.run_cli("retag", str(missing), "--append", "--tag", "x")
        self.assert_failure(result, "素材未登记", os.path.realpath(str(missing)))

    def test_fresh_db_created_then_unregistered(self):
        db = self.tmp_dir / "fresh.sqlite"
        result = self.run_cli("retag", "whatever", "--append", "--tag", "x", db=db)
        self.assert_failure(result, "素材未登记")
        self.assertTrue(db.exists(), "父目录存在时应创建空库")

    def test_missing_parent_dir_not_created(self):
        db = self.tmp_dir / "no_such_dir" / "catalog.sqlite"
        result = self.run_cli("retag", "whatever", "--append", "--tag", "x", db=db)
        self.assert_failure(result, "无法打开数据库")
        self.assertFalse(db.parent.exists(), "父目录缺失时不应补建目录")

    def test_append_rejected_on_other_commands(self):
        for args in (
            ("add", str(self.file_a), "--type", "t", "--tag", "a", "--append"),
            ("query", "--tag", "demo", "--append"),
            ("export", "--append"),
        ):
            self.assert_failure(self.run_cli(*args))
        # 其他命令拒绝 --append 不影响既有记录。
        self.assert_export([self.record_a_old, self.record_b])


if __name__ == "__main__":
    unittest.main(verbosity=2)
