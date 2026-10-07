"""retag --remove 标签移除的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / sqlite3 / tempfile），
无需第三方依赖、网络或特殊权限。

覆盖：
- 验收场景：按 A.bin、B.bin 顺序登记（A 类型 image、标签 demo、ui、UI；
  B 类型 audio、标签 demo）后，执行
  ``retag A.bin --remove --tag " ui " --tag absent``，
  A 的标签为 ["demo", "UI"]，类型与规范路径不变，B 不变；
  退出码 0、标准错误为空、标准输出仅为含 path、type、tags 的 JSON 对象；
- 移除规则：待移除标签去除首尾空白、按完整文本区分大小写匹配
  （ui 与 UI 是两个标签），重复条件只生效一次；未指定的标签保持原顺序，
  不存在的标签忽略，重复移除同一批标签仍成功；
- 结果持久保存：新进程 export 仍按 A、B 顺序返回；query --tag ui 不再
  返回 A，query --tag UI 仍返回 A；
- 素材至少保留一个标签：移除全部标签时整次拒绝（退出码 2），
  标准错误说明不能移除全部标签，原记录与标签顺序完整保留；
- --remove 与 --append 互斥，同时指定按参数错误拒绝且不创建数据库；
  不传 --remove 时保留原替换和追加行为；
- 等价路径写法（相对路径、含 . 的写法）定位同一记录；
- 源文件已删除或原路径变成目录仍可移除，操作不读取或改写素材内容；
- 缺少素材路径、路径为空、缺少 --tag、任一标签为空或只有空白、传入不支持
  的参数、目标未登记（错误含规范路径）、数据库无法打开或读写、损坏或结构
  不兼容时：退出码 2、标准输出为空、标准错误说明原因且不含调用栈；
  参数错误不创建数据库；失败保留原记录与标签顺序，不留部分标签修改；
- --remove 只属于 retag：add、query、export 收到它按参数错误拒绝；
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

# 固定拒绝条件：仅拒绝 blocked 标签写入，用于验证移除中途失败时整体回滚。
CREATE_REJECT_TRIGGER_SQL = f"""
CREATE TRIGGER {REJECT_TRIGGER}
BEFORE INSERT ON asset_tag
WHEN NEW.tag = 'blocked'
BEGIN
    SELECT RAISE(ABORT, 'rejected by fixed condition: blocked');
END;
"""

DROP_REJECT_TRIGGER_SQL = f"DROP TRIGGER {REJECT_TRIGGER}"


class RetagRemoveRegressionTest(unittest.TestCase):
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
        self.expected_a = {
            "path": self.path_a,
            "type": "image",
            "tags": ["demo", "ui", "UI"],
        }
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
        """按验收顺序登记 A（image: demo, ui, UI）与 B（audio: demo）。"""
        result_a = self.run_cli(
            "add", str(self.file_a), "--type", "image",
            "--tag", "demo", "--tag", "ui", "--tag", "UI",
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

    def test_remove_acceptance_scenario(self):
        self.register_samples()

        # 验收命令：retag A.bin --remove --tag " ui " --tag absent
        expected = {"path": self.path_a, "type": "image", "tags": ["demo", "UI"]}
        self.assertRetagOk(
            expected,
            str(self.file_a), "--remove", "--tag", " ui ", "--tag", "absent",
        )

        # 新进程 export 仍按 A、B 顺序返回，B 不变。
        self.assertExport([expected, self.expected_b])
        # query --tag ui 不再返回 A；query --tag UI 仍返回 A。
        self.assertQuery("ui", [])
        self.assertQuery("UI", [expected])
        self.assertQuery("demo", [expected, self.expected_b])

        # 随后移除 A 的 demo 和 UI：整次拒绝，两个标签保留。
        stderr = self.assertRetagError(
            str(self.file_a), "--remove", "--tag", "demo", "--tag", "UI"
        )
        self.assertIn("不能移除", stderr)
        self.assertExport([expected, self.expected_b])

    def test_repeated_remove_is_idempotent(self):
        self.register_samples()
        args = (str(self.file_a), "--remove", "--tag", " ui ", "--tag", "absent")
        expected = {"path": self.path_a, "type": "image", "tags": ["demo", "UI"]}
        self.assertRetagOk(expected, *args)
        # 重复移除同一批标签（含已不存在的标签）仍成功，标签不减少也不重排。
        self.assertRetagOk(expected, *args)
        self.assertExport([expected, self.expected_b])

    def test_remove_is_case_sensitive_and_dedups_conditions(self):
        self.register_samples()

        # 区分大小写：移除 ui 不影响 UI；重复条件（含去空白后相同）只生效一次。
        expected = {"path": self.path_a, "type": "image", "tags": ["demo", "UI"]}
        self.assertRetagOk(
            expected,
            str(self.file_a), "--remove",
            "--tag", "ui", "--tag", " ui ", "--tag", "ui",
        )

        # 移除 UI 不影响其他标签，剩余标签保持原顺序。
        expected = {"path": self.path_a, "type": "image", "tags": ["demo"]}
        self.assertRetagOk(expected, str(self.file_a), "--remove", "--tag", "UI")
        self.assertExport([expected, self.expected_b])

    def test_remove_preserves_order_of_remaining_tags(self):
        self.register_samples()
        # 从中间移除一个标签，其余标签保持原顺序。
        expected = {"path": self.path_a, "type": "image", "tags": ["demo", "UI"]}
        self.assertRetagOk(expected, str(self.file_a), "--remove", "--tag", "ui")
        # 从开头移除，剩余顺序不变。
        expected = {"path": self.path_a, "type": "image", "tags": ["UI"]}
        self.assertRetagOk(expected, str(self.file_a), "--remove", "--tag", "demo")
        self.assertExport([expected, self.expected_b])

    def test_remove_all_tags_rejected_and_record_kept(self):
        self.register_samples()
        # 一次指定全部标签（含重复与空白写法）：整次拒绝。
        stderr = self.assertRetagError(
            str(self.file_a), "--remove",
            "--tag", "demo", "--tag", " ui ", "--tag", "UI", "--tag", "demo",
        )
        self.assertIn("不能移除", stderr)
        # 单标签素材移除唯一标签同样拒绝。
        stderr = self.assertRetagError(str(self.file_b), "--remove", "--tag", "demo")
        self.assertIn("不能移除", stderr)
        # 原记录与标签顺序完整保留。
        self.assertExport([self.expected_a, self.expected_b])
        self.assertQuery("demo", [self.expected_a, self.expected_b])
        self.assertQuery("ui", [self.expected_a])

    def test_remove_and_append_are_mutually_exclusive(self):
        self.register_samples()
        self.assertRetagError(str(self.file_a), "--append", "--remove", "--tag", "x")
        self.assertRetagError(str(self.file_a), "--remove", "--append", "--tag", "x")
        # 既有记录不变。
        self.assertExport([self.expected_a, self.expected_b])

    def test_mutually_exclusive_modes_do_not_create_database(self):
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())
        self.assertRetagError(
            str(self.file_a), "--append", "--remove", "--tag", "x", db=fresh
        )
        self.assertFalse(fresh.exists())

    def test_remove_equivalent_path_spellings_resolve_same_record(self):
        self.register_samples()
        relative = os.path.relpath(self.path_a, PROJECT_ROOT)
        dotted = str(self.tmp_dir / "." / "A.bin")

        for spelling in (relative, dotted):
            data = self.assertRetagOk(
                {"path": self.path_a, "type": "image", "tags": ["demo", "UI"]},
                spelling, "--remove", "--tag", "ui",
            )
            self.assertEqual(data["path"], self.path_a)
            # 用完整替换还原原标签及顺序，以便下一种写法验证同一记录。
            self.assertRetagOk(
                self.expected_a,
                spelling, "--tag", "demo", "--tag", "ui", "--tag", "UI",
            )

    def test_remove_deleted_or_directory_source_still_works(self):
        self.register_samples()

        # 源文件已删除仍可移除。
        self.file_a.unlink()
        self.assertRetagOk(
            {"path": self.path_a, "type": "image", "tags": ["demo", "UI"]},
            str(self.file_a), "--remove", "--tag", "ui",
        )

        # 原路径变成目录仍可移除（移除后 B 只剩一个标签，先追加再移除）。
        self.file_b.unlink()
        os.mkdir(self.path_b)
        self.assertRetagOk(
            {"path": self.path_b, "type": "audio", "tags": ["demo", "dir"]},
            str(self.file_b), "--append", "--tag", "dir",
        )
        self.assertRetagOk(
            {"path": self.path_b, "type": "audio", "tags": ["demo"]},
            str(self.file_b), "--remove", "--tag", "dir",
        )

    def test_remove_does_not_touch_source_file_content(self):
        self.register_samples()
        self.assertRetagOk(
            {"path": self.path_a, "type": "image", "tags": ["demo", "UI"]},
            str(self.file_a), "--remove", "--tag", "ui",
        )
        self.assertEqual(self.file_a.read_bytes(), self.content_a)
        self.assertEqual(self.file_b.read_bytes(), self.content_b)

    def test_retag_without_remove_keeps_replace_and_append_behavior(self):
        self.register_samples()
        # 不传 --remove 与 --append 时仍执行既有完整替换规则。
        expected = {"path": self.path_a, "type": "image", "tags": ["project"]}
        self.assertRetagOk(expected, str(self.file_a), "--tag", "project")
        # --append 行为不变。
        expected = {
            "path": self.path_a,
            "type": "image",
            "tags": ["project", "extra"],
        }
        self.assertRetagOk(expected, str(self.file_a), "--append", "--tag", "extra")
        self.assertExport([expected, self.expected_b])

    def test_remove_argument_errors_rejected(self):
        self.register_samples()
        # 缺少路径
        self.assertRetagError("--remove", "--tag", "x")
        # 缺少 --tag
        self.assertRetagError(str(self.file_a), "--remove")
        # 路径为空
        self.assertRetagError("", "--remove", "--tag", "x")
        # 标签为空或只有空白
        self.assertRetagError(str(self.file_a), "--remove", "--tag", "")
        self.assertRetagError(str(self.file_a), "--remove", "--tag", "   ")
        self.assertRetagError(
            str(self.file_a), "--remove", "--tag", "ui", "--tag", " "
        )
        # 不支持的参数
        self.assertRetagError(
            str(self.file_a), "--remove", "--tag", "ui", "--type", "image"
        )
        self.assertRetagError(str(self.file_a), "--remove", "--tag", "ui", "extra")

        # 失败不改变已有记录。
        self.assertExport([self.expected_a, self.expected_b])

    def test_remove_argument_errors_do_not_create_database(self):
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())
        self.assertRetagError(str(self.file_a), "--remove", "--tag", " ", db=fresh)
        self.assertRetagError(str(self.file_a), "--remove", db=fresh)
        self.assertFalse(fresh.exists())

    def test_remove_unregistered_target_rejected_with_canonical_path(self):
        self.register_samples()
        stderr = self.assertRetagError(
            str(self.tmp_dir / "C.bin"), "--remove", "--tag", "x"
        )
        self.assertIn(os.path.realpath(str(self.tmp_dir / "C.bin")), stderr)
        self.assertExport([self.expected_a, self.expected_b])

    def test_remove_fresh_database_created_then_unregistered_reported(self):
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())
        stderr = self.assertRetagError(
            str(self.file_a), "--remove", "--tag", "x", db=fresh
        )
        self.assertIn(self.path_a, stderr)
        # 沿用自动创建规则：空库已创建且结构可用。
        self.assertTrue(fresh.exists())
        self.assertExport([], db=fresh)

    def test_remove_missing_parent_directory_is_not_created(self):
        missing = self.tmp_dir / "no" / "such" / "catalog.sqlite"
        self.assertRetagError(str(self.file_a), "--remove", "--tag", "x", db=missing)
        self.assertFalse((self.tmp_dir / "no").exists())

    def test_remove_database_open_and_schema_errors_rejected(self):
        self.register_samples()

        # 数据库路径指向目录。
        target = self.tmp_dir / "a_directory"
        target.mkdir()
        self.assertRetagError(str(self.file_a), "--remove", "--tag", "x", db=target)

        # 非 SQLite 文件：拒绝且字节不变。
        non_sqlite = self.tmp_dir / "broken.sqlite"
        non_sqlite.write_text("not a sqlite database", encoding="utf-8")
        before = non_sqlite.read_bytes()
        self.assertRetagError(
            str(self.file_a), "--remove", "--tag", "x", db=non_sqlite
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
        self.assertRetagError(str(self.file_a), "--remove", "--tag", "x", db=foreign)
        self.assertEqual(foreign.read_bytes(), before)

        # 失败不影响既有数据库中的记录。
        self.assertExport([self.expected_a, self.expected_b])

    def test_remove_option_rejected_by_other_commands(self):
        self.register_samples()
        for args in (
            ("add", str(self.file_a), "--type", "image", "--tag", "x", "--remove"),
            ("query", "--tag", "demo", "--remove"),
            ("export", "--remove"),
        ):
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 2, args)
            self.assertEqual(result.stdout, "")
            self.assertNotIn("Traceback", result.stderr)

        # 既有记录不变。
        self.assertExport([self.expected_a, self.expected_b])

    def test_remove_mid_write_failure_keeps_old_tags_then_recovers(self):
        # 登记含 blocked 标签的素材后再安装固定拒绝条件：
        # 移除其他标签时，剩余 blocked 标签的重新写入被拒，整体回滚。
        result = self.run_cli(
            "add", str(self.file_a), "--type", "image",
            "--tag", "demo", "--tag", "blocked",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        record_a = {
            "path": self.path_a,
            "type": "image",
            "tags": ["demo", "blocked"],
        }

        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(CREATE_REJECT_TRIGGER_SQL)
            conn.commit()
        finally:
            conn.close()

        # 移除 demo：DELETE 后重写 blocked 被拒，整体回滚。
        result = self.run_retag(str(self.file_a), "--remove", "--tag", "demo")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("数据库读写失败", result.stderr)
        self.assertIn("blocked", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

        # 原记录与标签顺序完整保留，不留部分标签修改。
        self.assertExport([record_a])
        self.assertQuery("demo", [record_a])
        self.assertQuery("blocked", [record_a])

        # 撤去拒绝条件后同一移除命令成功。
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(DROP_REJECT_TRIGGER_SQL)
            conn.commit()
        finally:
            conn.close()

        expected = {"path": self.path_a, "type": "image", "tags": ["blocked"]}
        self.assertRetagOk(expected, str(self.file_a), "--remove", "--tag", "demo")
        self.assertExport([expected])
        self.assertQuery("demo", [])
        self.assertQuery("blocked", [expected])


if __name__ == "__main__":
    unittest.main(verbosity=2)
