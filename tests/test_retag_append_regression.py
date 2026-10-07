"""retag --append 标签追加的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / sqlite3 / tempfile），
无需第三方依赖、网络或特殊权限。

覆盖：
- 验收场景：按 A.bin、B.bin 顺序登记（A 类型 image、标签 demo、ui；
  B 类型 audio、标签 demo）后，执行
  ``retag A.bin --append --tag " ui " --tag project --tag demo``，
  A 的标签为 ["demo", "ui", "project"]，类型与规范路径不变，B 不变；
  退出码 0、标准错误为空、标准输出仅为含 path、type、tags 的 JSON 对象；
- 追加规则：新标签按输入顺序去除首尾空白并去重，原标签及顺序保留，
  已存在的标签不移动，新标签接在末尾；比较区分大小写（ui 与 UI 是两个标签）；
  重复追加同一批标签仍成功，标签不增加也不重排；
- 结果持久保存：新进程 export 仍按 A、B 顺序返回；query --tag project
  只返回 A，query --tag demo 仍返回 A、B 两条；
- 等价路径写法（相对路径、含 . 的写法）定位同一记录；
- 源文件已删除或原路径变成目录仍可追加，操作不读取或改写素材内容；
- 不传 --append 时仍执行既有完整替换规则；
- 缺少素材路径、路径为空、缺少 --tag、任一标签为空或只有空白、传入不支持
  的参数、目标未登记（错误含规范路径）、数据库无法打开或读写、损坏或结构
  不兼容时：退出码 2、标准输出为空、标准错误说明原因且不含调用栈；
  参数错误不创建数据库；追加失败保留原记录与标签顺序，不留部分新增标签；
- --append 只属于 retag：add、query、export 收到它按参数错误拒绝；
- 数据库文件不存在但父目录存在时创建空库后报告未登记；父目录缺失时不补建目录。

每个用例使用独立的临时目录与数据库，只创建/清理自己的样例文件。
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

# 固定拒绝条件：仅拒绝 blocked 标签写入，用于验证追加中途失败时整体回滚。
CREATE_REJECT_TRIGGER_SQL = f"""
CREATE TRIGGER {REJECT_TRIGGER}
BEFORE INSERT ON asset_tag
WHEN NEW.tag = 'blocked'
BEGIN
    SELECT RAISE(ABORT, 'rejected by fixed condition: blocked');
END;
"""

DROP_REJECT_TRIGGER_SQL = f"DROP TRIGGER {REJECT_TRIGGER}"


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
        self.expected_a = {"path": self.path_a, "type": "image", "tags": ["demo", "ui"]}
        self.expected_b = {"path": self.path_b, "type": "audio", "tags": ["demo"]}

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args, db=None):
        """以全新进程运行 python -m asset_catalog，返回 CompletedProcess。"""
        cmd = [
            sys.executable,
            "-m",
            "asset_catalog",
            "--db",
            str(self.db_path if db is None else db),
            *args,
        ]
        return subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )

    def register_samples(self):
        """按验收顺序登记 A（image: demo, ui）与 B（audio: demo）。"""
        result_a = self.run_cli(
            "add", str(self.file_a), "--type", "image", "--tag", "demo", "--tag", "ui"
        )
        self.assertEqual(result_a.returncode, 0, result_a.stderr)
        self.assertEqual(result_a.stderr, "")

        result_b = self.run_cli(
            "add", str(self.file_b), "--type", "audio", "--tag", "demo"
        )
        self.assertEqual(result_b.returncode, 0, result_b.stderr)
        self.assertEqual(result_b.stderr, "")

    def run_retag(self, *args, db=None):
        return self.run_cli("retag", *args, db=db)

    def assertRetagOk(self, expected, *args, db=None):
        result = self.run_retag(*args, db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertEqual(set(data), {"path", "type", "tags"})
        self.assertEqual(data, expected)
        return data

    def assertRetagError(self, *args, db=None):
        result = self.run_retag(*args, db=db)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr.strip(), "")
        self.assertNotIn("Traceback", result.stderr)
        return result.stderr

    def assertExport(self, expected, db=None):
        result = self.run_cli("export", db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected)

    def assertQuery(self, tag, expected):
        result = self.run_cli("query", "--tag", tag)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected)

    def test_append_acceptance_scenario(self):
        self.register_samples()

        # 验收命令：retag A.bin --append --tag " ui " --tag project --tag demo
        expected = {
            "path": self.path_a,
            "type": "image",
            "tags": ["demo", "ui", "project"],
        }
        self.assertRetagOk(
            expected,
            str(self.file_a), "--append",
            "--tag", " ui ", "--tag", "project", "--tag", "demo",
        )

        # 新进程 export 仍按 A、B 顺序返回，B 不变。
        self.assertExport([expected, self.expected_b])
        # query --tag project 只返回 A；query --tag demo 仍返回 A、B。
        self.assertQuery("project", [expected])
        self.assertQuery("demo", [expected, self.expected_b])

    def test_repeated_append_is_idempotent(self):
        self.register_samples()
        args = (
            str(self.file_a), "--append",
            "--tag", " ui ", "--tag", "project", "--tag", "demo",
        )
        expected = {
            "path": self.path_a,
            "type": "image",
            "tags": ["demo", "ui", "project"],
        }
        self.assertRetagOk(expected, *args)
        # 重复追加同一批标签仍成功，标签不增加也不重排。
        self.assertRetagOk(expected, *args)
        self.assertExport([expected, self.expected_b])

    def test_append_preserves_order_and_is_case_sensitive(self):
        self.register_samples()

        # 新标签按输入顺序接在末尾；已存在的 ui 不移动。
        expected = {
            "path": self.path_a,
            "type": "image",
            "tags": ["demo", "ui", "zeta", "alpha"],
        }
        self.assertRetagOk(
            expected,
            str(self.file_a), "--append",
            "--tag", "zeta", "--tag", "ui", "--tag", "alpha", "--tag", "zeta",
        )

        # 比较区分大小写：UI 与 ui 是两个标签，UI 作为新标签接在末尾。
        expected = {
            "path": self.path_a,
            "type": "image",
            "tags": ["demo", "ui", "zeta", "alpha", "UI"],
        }
        self.assertRetagOk(expected, str(self.file_a), "--append", "--tag", "UI")
        self.assertExport([expected, self.expected_b])

    def test_append_equivalent_path_spellings_resolve_same_record(self):
        self.register_samples()
        relative = os.path.relpath(self.path_a, PROJECT_ROOT)
        dotted = str(self.tmp_dir / "." / "A.bin")

        for spelling in (relative, dotted):
            data = self.assertRetagOk(
                {
                    "path": self.path_a,
                    "type": "image",
                    "tags": ["demo", "ui", "x"],
                },
                spelling, "--append", "--tag", "x",
            )
            self.assertEqual(data["path"], self.path_a)

    def test_append_deleted_or_directory_source_still_works(self):
        self.register_samples()

        # 源文件已删除仍可追加。
        self.file_a.unlink()
        self.assertRetagOk(
            {"path": self.path_a, "type": "image", "tags": ["demo", "ui", "gone"]},
            str(self.file_a), "--append", "--tag", "gone",
        )

        # 原路径变成目录仍可追加。
        self.file_b.unlink()
        os.mkdir(self.path_b)
        self.assertRetagOk(
            {"path": self.path_b, "type": "audio", "tags": ["demo", "dir"]},
            str(self.file_b), "--append", "--tag", "dir",
        )

    def test_append_does_not_touch_source_file_content(self):
        self.register_samples()
        self.assertRetagOk(
            {"path": self.path_a, "type": "image", "tags": ["demo", "ui", "new"]},
            str(self.file_a), "--append", "--tag", "new",
        )
        self.assertEqual(self.file_a.read_bytes(), self.content_a)
        self.assertEqual(self.file_b.read_bytes(), self.content_b)

    def test_retag_without_append_still_replaces_all_tags(self):
        self.register_samples()
        expected = {"path": self.path_a, "type": "image", "tags": ["project"]}
        self.assertRetagOk(expected, str(self.file_a), "--tag", "project")
        self.assertExport([expected, self.expected_b])

    def test_append_argument_errors_rejected(self):
        self.register_samples()
        # 缺少路径
        self.assertRetagError("--append", "--tag", "x")
        # 缺少 --tag
        self.assertRetagError(str(self.file_a), "--append")
        # 路径为空
        self.assertRetagError("", "--append", "--tag", "x")
        # 标签为空或只有空白
        self.assertRetagError(str(self.file_a), "--append", "--tag", "")
        self.assertRetagError(str(self.file_a), "--append", "--tag", "   ")
        self.assertRetagError(
            str(self.file_a), "--append", "--tag", "ok", "--tag", " "
        )
        # 不支持的参数
        self.assertRetagError(
            str(self.file_a), "--append", "--tag", "x", "--type", "image"
        )
        self.assertRetagError(str(self.file_a), "--append", "--tag", "x", "extra")

        # 失败不改变已有记录。
        self.assertExport([self.expected_a, self.expected_b])

    def test_append_argument_errors_do_not_create_database(self):
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())
        self.assertRetagError(str(self.file_a), "--append", "--tag", " ", db=fresh)
        self.assertRetagError(str(self.file_a), "--append", db=fresh)
        self.assertFalse(fresh.exists())

    def test_append_unregistered_target_rejected_with_canonical_path(self):
        self.register_samples()
        stderr = self.assertRetagError(
            str(self.tmp_dir / "C.bin"), "--append", "--tag", "x"
        )
        self.assertIn(os.path.realpath(str(self.tmp_dir / "C.bin")), stderr)
        self.assertExport([self.expected_a, self.expected_b])

    def test_append_fresh_database_created_then_unregistered_reported(self):
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())
        stderr = self.assertRetagError(
            str(self.file_a), "--append", "--tag", "x", db=fresh
        )
        self.assertIn(self.path_a, stderr)
        # 沿用自动创建规则：空库已创建且结构可用。
        self.assertTrue(fresh.exists())
        self.assertExport([], db=fresh)

    def test_append_missing_parent_directory_is_not_created(self):
        missing = self.tmp_dir / "no" / "such" / "catalog.sqlite"
        self.assertRetagError(str(self.file_a), "--append", "--tag", "x", db=missing)
        self.assertFalse((self.tmp_dir / "no").exists())

    def test_append_database_open_and_schema_errors_rejected(self):
        self.register_samples()

        # 数据库路径指向目录。
        target = self.tmp_dir / "a_directory"
        target.mkdir()
        self.assertRetagError(str(self.file_a), "--append", "--tag", "x", db=target)

        # 非 SQLite 文件：拒绝且字节不变。
        non_sqlite = self.tmp_dir / "broken.sqlite"
        non_sqlite.write_text("not a sqlite database", encoding="utf-8")
        before = non_sqlite.read_bytes()
        self.assertRetagError(
            str(self.file_a), "--append", "--tag", "x", db=non_sqlite
        )
        self.assertEqual(non_sqlite.read_bytes(), before)

        # 结构不兼容：拒绝且原有记录保留。
        foreign = self.tmp_dir / "foreign.sqlite"
        conn = sqlite3.connect(str(foreign))
        conn.execute("CREATE TABLE business_record(id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO business_record(note) VALUES ('fixed record')")
        conn.commit()
        conn.close()
        before = foreign.read_bytes()
        self.assertRetagError(str(self.file_a), "--append", "--tag", "x", db=foreign)
        self.assertEqual(foreign.read_bytes(), before)

        # 失败不影响既有数据库中的记录。
        self.assertExport([self.expected_a, self.expected_b])

    def test_append_option_rejected_by_other_commands(self):
        self.register_samples()
        for args in (
            ("add", str(self.file_a), "--type", "image", "--tag", "x", "--append"),
            ("query", "--tag", "demo", "--append"),
            ("export", "--append"),
        ):
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 2, args)
            self.assertEqual(result.stdout, "")
            self.assertNotIn("Traceback", result.stderr)

        # 既有记录不变。
        self.assertExport([self.expected_a, self.expected_b])

    def test_append_mid_write_failure_keeps_old_tags_then_recovers(self):
        self.register_samples()

        # 安装只拒绝 blocked 标签写入的固定条件。
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(CREATE_REJECT_TRIGGER_SQL)
            conn.commit()
        finally:
            conn.close()

        # 追加 project 与 blocked：project 写入成功后 blocked 被拒，整体回滚。
        result = self.run_retag(
            str(self.file_a), "--append", "--tag", "project", "--tag", "blocked"
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("数据库读写失败", result.stderr)
        self.assertIn("blocked", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

        # 原记录与标签顺序完整保留，不留部分新增标签。
        self.assertExport([self.expected_a, self.expected_b])
        self.assertQuery("project", [])
        self.assertQuery("blocked", [])
        self.assertQuery("demo", [self.expected_a, self.expected_b])

        # 撤去拒绝条件后同一追加命令成功。
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(DROP_REJECT_TRIGGER_SQL)
            conn.commit()
        finally:
            conn.close()

        expected = {
            "path": self.path_a,
            "type": "image",
            "tags": ["demo", "ui", "project", "blocked"],
        }
        self.assertRetagOk(
            expected,
            str(self.file_a), "--append", "--tag", "project", "--tag", "blocked",
        )
        self.assertExport([expected, self.expected_b])
        self.assertQuery("project", [expected])
        self.assertQuery("blocked", [expected])
        self.assertQuery("demo", [expected, self.expected_b])


if __name__ == "__main__":
    unittest.main(verbosity=2)
