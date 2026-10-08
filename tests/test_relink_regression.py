"""relink 重新关联本地路径的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

覆盖：
- 固定样例：按 A.old、B.bin 顺序登记（A 为 image，标签依次为 demo、ui；
  B 为 audio，标签为 demo），把 A 移到 A.new 后执行 relink A.old A.new，
  show A.new 返回 A 的完整记录，query --tag demo 仍依次返回 A、B，
  只有 A 的路径变化，show A.old 报告未登记；
- 原路径按 show 的规则解析定位（相对路径、. / .. 等价写法），原文件已删除
  或原位置变成目录也允许关联；新路径按 add 的规则解析，要求当前存在且为
  普通文件，符号链接按目标解析并保存目标的规范绝对路径；
- 操作只更新目录中的路径：不移动、复制、删除或改写文件，不读取素材内容；
  类型、完整标签及顺序、首次登记顺序与其他记录保持不变，重启后可读取；
- 新旧规范路径相同且目标仍为普通文件时成功返回原记录；
- 原路径未登记、新文件不存在或不是普通文件、目标已登记（不合并或覆盖）、
  目标为目录数据库时：退出码 2、标准输出为空、标准错误说明原因并含相关
  规范路径、不含调用栈；
- 路径缺失或为空、缺少 --db、数据库路径为空、不支持的参数：退出码 2，
  不创建数据库；数据库不存在且父目录存在时初始化空库后报告原路径未登记，
  父目录缺失时不补建目录；
- 数据库打不开、损坏、结构不兼容或写入失败时退出码 2 并说明数据库原因，
  已有库不被覆盖或重建，原记录完整保留。

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

# 固定拒绝条件：只拒绝把路径更新为 blocked 目标的写入，其他写入与读取不受影响。
CREATE_REJECT_TRIGGER_SQL = """
CREATE TRIGGER reject_relink_to_blocked
BEFORE UPDATE ON asset
WHEN NEW.path LIKE '%blocked%'
BEGIN
    SELECT RAISE(ABORT, 'relink blocked');
END;
"""


class RelinkRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a_old = self.tmp_dir / "A.old"
        self.file_a_new = self.tmp_dir / "A.new"
        self.file_b = self.tmp_dir / "B.bin"
        self.file_a_old.write_text("demo asset A\n", encoding="utf-8")
        self.file_b.write_text("demo asset B\n", encoding="utf-8")
        self.db_path = self.tmp_dir / "catalog.sqlite"

        self.path_a_old = os.path.realpath(str(self.file_a_old))
        self.path_a_new = os.path.realpath(str(self.file_a_new))
        self.path_b = os.path.realpath(str(self.file_b))
        self.expected_a = {
            "path": self.path_a_old,
            "type": "image",
            "tags": ["demo", "ui"],
        }
        self.expected_b = {"path": self.path_b, "type": "audio", "tags": ["demo"]}
        self.expected_a_relinked = {
            "path": self.path_a_new,
            "type": "image",
            "tags": ["demo", "ui"],
        }

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
        """按验收顺序登记 A.old（image: demo, ui）与 B.bin（audio: demo）。"""
        result_a = self.run_cli(
            "add",
            str(self.file_a_old),
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
            "add", str(self.file_b), "--type", "audio", "--tag", "demo"
        )
        self.assertEqual(result_b.returncode, 0, result_b.stderr)
        self.assertEqual(result_b.stderr, "")

    def move_a_to_new(self):
        """把 A.old 移到 A.new（样例准备，不经过被测命令）。"""
        os.rename(self.path_a_old, self.path_a_new)

    def run_relink(self, *args, db=None):
        return self.run_cli("relink", *args, db=db)

    def assertRelinkOk(self, expected, *args, db=None):
        result = self.run_relink(*args, db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        # 标准输出仅一行 JSON 对象，只含 path、type、tags。
        self.assertEqual(len(result.stdout.strip().splitlines()), 1)
        data = json.loads(result.stdout)
        self.assertEqual(set(data), {"path", "type", "tags"})
        self.assertEqual(data, expected)
        return data

    def assertRelinkError(self, *args, db=None):
        result = self.run_relink(*args, db=db)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr.strip(), "")
        self.assertNotIn("Traceback", result.stderr)
        return result.stderr

    def assertShow(self, expected, path):
        result = self.run_cli("show", path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected)

    def assertShowUnregistered(self, path, canonical):
        result = self.run_cli("show", path)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(canonical, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def assertQueryDemo(self, expected):
        result = self.run_cli("query", "--tag", "demo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected)

    def assertExport(self, expected):
        result = self.run_cli("export")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected)

    def test_acceptance_move_then_relink(self):
        self.register_samples()
        self.move_a_to_new()

        # 验收场景：relink A.old A.new
        self.assertRelinkOk(
            self.expected_a_relinked, str(self.file_a_old), str(self.file_a_new)
        )

        # show A.new 返回 A 的完整记录；show A.old 报告未登记。
        self.assertShow(self.expected_a_relinked, str(self.file_a_new))
        self.assertShowUnregistered(str(self.file_a_old), self.path_a_old)

        # query --tag demo 仍依次返回 A、B，只有 A 的路径变化。
        self.assertQueryDemo([self.expected_a_relinked, self.expected_b])
        self.assertExport([self.expected_a_relinked, self.expected_b])

    def test_relink_persists_across_processes_and_other_commands(self):
        self.register_samples()
        self.move_a_to_new()
        self.assertRelinkOk(
            self.expected_a_relinked, str(self.file_a_old), str(self.file_a_new)
        )

        # 新进程的其他命令均读到新路径；旧路径不再命中任何命令。
        result = self.run_cli("query", "--tag", "demo", "--type", "image")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [self.expected_a_relinked])

        # retag / retype 通过新路径照常工作。
        result = self.run_cli("retag", str(self.file_a_new), "--tag", "ui2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"path": self.path_a_new, "type": "image", "tags": ["ui2"]},
        )
        result = self.run_cli(
            "retype", str(self.file_a_new), "--type", "texture"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        # add 拒绝重复登记新规范路径。
        result = self.run_cli(
            "add", str(self.file_a_new), "--type", "image", "--tag", "other"
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

    def test_relink_does_not_touch_files(self):
        self.register_samples()
        self.move_a_to_new()
        before_a = self.file_a_new.read_bytes()
        before_b = self.file_b.read_bytes()
        self.assertRelinkOk(
            self.expected_a_relinked, str(self.file_a_old), str(self.file_a_new)
        )
        # 不移动、复制、删除或改写文件：A.new 与 B 内容不变，A.old 未被重建。
        self.assertEqual(self.file_a_new.read_bytes(), before_a)
        self.assertEqual(self.file_b.read_bytes(), before_b)
        self.assertFalse(self.file_a_old.exists())

    def test_deleted_or_directory_old_location_still_relinkable(self):
        self.register_samples()

        # 原文件已删除（不重建）仍允许关联。
        self.file_a_old.unlink()
        self.file_a_new.write_text("relocated A\n", encoding="utf-8")
        self.assertRelinkOk(
            self.expected_a_relinked, str(self.file_a_old), str(self.file_a_new)
        )

        # 原位置变成目录也允许关联。
        self.file_b.unlink()
        os.mkdir(self.path_b)
        path_b_new = os.path.realpath(str(self.tmp_dir / "B.new"))
        Path(self.tmp_dir / "B.new").write_text("relocated B\n", encoding="utf-8")
        self.assertRelinkOk(
            {"path": path_b_new, "type": "audio", "tags": ["demo"]},
            str(self.file_b),
            str(self.tmp_dir / "B.new"),
        )

    def test_equivalent_old_path_spellings_resolve_same_record(self):
        self.register_samples()
        self.move_a_to_new()
        relative = os.path.relpath(self.path_a_old, PROJECT_ROOT)
        dotted = str(self.tmp_dir / "." / "A.old")

        for spelling in (relative, dotted):
            data = self.assertRelinkOk(
                self.expected_a_relinked, spelling, str(self.file_a_new)
            )
            self.assertEqual(data["path"], self.path_a_new)
            # 还原以便下一种写法再次定位（样例准备，不经过被测命令）。
            conn = sqlite3.connect(str(self.db_path))
            conn.execute(
                "UPDATE asset SET path = ? WHERE path = ?",
                (self.path_a_old, self.path_a_new),
            )
            conn.commit()
            conn.close()

    def test_same_canonical_path_returns_original_record(self):
        self.register_samples()
        # 新旧规范路径相同且目标仍为普通文件：成功返回原记录，含等价写法。
        dotted = str(self.tmp_dir / "." / "A.old")
        self.assertRelinkOk(self.expected_a, str(self.file_a_old), dotted)
        self.assertRelinkOk(
            self.expected_a, str(self.file_a_old), str(self.file_a_old)
        )
        self.assertExport([self.expected_a, self.expected_b])

    def test_new_path_symlink_stores_target_canonical_path(self):
        self.register_samples()
        target = self.tmp_dir / "target.bin"
        target.write_text("link target\n", encoding="utf-8")
        link = self.tmp_dir / "link.bin"
        try:
            os.symlink(str(target), str(link))
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"环境不支持创建符号链接: {exc}")

        path_target = os.path.realpath(str(target))
        self.assertRelinkOk(
            {"path": path_target, "type": "image", "tags": ["demo", "ui"]},
            str(self.file_a_old),
            str(link),
        )
        # 记录保存目标的规范绝对路径：show 目标路径命中；经链接路径查看时
        # 符号链接按目标解析，同样定位到该记录且输出目标规范路径。
        self.assertShow(
            {"path": path_target, "type": "image", "tags": ["demo", "ui"]},
            str(target),
        )
        self.assertShow(
            {"path": path_target, "type": "image", "tags": ["demo", "ui"]},
            str(link),
        )

    def test_unregistered_old_path_rejected_with_canonical_path(self):
        self.register_samples()
        missing = self.tmp_dir / "C.bin"
        stderr = self.assertRelinkError(str(missing), str(self.file_b))
        self.assertIn(os.path.realpath(str(missing)), stderr)
        # 失败不改变已有记录。
        self.assertExport([self.expected_a, self.expected_b])

    def test_new_file_missing_or_not_regular_rejected(self):
        self.register_samples()
        missing_new = self.tmp_dir / "absent.bin"
        stderr = self.assertRelinkError(str(self.file_a_old), str(missing_new))
        self.assertIn(os.path.realpath(str(missing_new)), stderr)

        # 新路径为目录：不是普通文件。
        stderr = self.assertRelinkError(str(self.file_a_old), str(self.tmp_dir))
        self.assertIn(os.path.realpath(str(self.tmp_dir)), stderr)

        self.assertExport([self.expected_a, self.expected_b])

    def test_new_path_registered_to_another_record_rejected(self):
        self.register_samples()
        # 新路径属于另一条登记记录：拒绝，不合并或覆盖。
        stderr = self.assertRelinkError(str(self.file_a_old), str(self.file_b))
        self.assertIn(self.path_b, stderr)
        self.assertExport([self.expected_a, self.expected_b])
        self.assertQueryDemo([self.expected_a, self.expected_b])

    def test_new_path_equal_to_database_rejected(self):
        self.register_samples()
        before = self.db_path.read_bytes()
        stderr = self.assertRelinkError(str(self.file_a_old), str(self.db_path))
        self.assertIn(os.path.realpath(str(self.db_path)), stderr)
        # 数据库文件字节不变，既有记录保留。
        self.assertEqual(self.db_path.read_bytes(), before)
        self.assertExport([self.expected_a, self.expected_b])

    def test_new_path_equal_to_empty_db_file_not_initialized(self):
        empty = self.tmp_dir / "empty.sqlite"
        empty.write_bytes(b"")
        # 目标与 --db 相同且为已有空文件：拒绝且不因此初始化。
        stderr = self.assertRelinkError(
            str(self.file_a_old), str(empty), db=empty
        )
        self.assertIn(os.path.realpath(str(empty)), stderr)
        self.assertEqual(empty.read_bytes(), b"")

    def test_missing_or_empty_path_arguments_rejected(self):
        self.register_samples()
        # 缺少路径参数
        self.assertRelinkError()
        self.assertRelinkError(str(self.file_a_old))
        # 多余位置参数与不支持选项
        self.assertRelinkError(
            str(self.file_a_old), str(self.file_a_new), "extra"
        )
        self.assertRelinkError(
            str(self.file_a_old), str(self.file_a_new), "--tag", "x"
        )
        # 路径为空字符串
        self.assertRelinkError("", str(self.file_a_new))
        self.assertRelinkError(str(self.file_a_old), "")
        # 缺少 --db
        result = subprocess.run(
            [sys.executable, "-m", "asset_catalog", "relink", "a", "b"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)
        # 数据库路径为空
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "asset_catalog",
                "--db",
                "",
                "relink",
                str(self.file_a_old),
                str(self.file_a_new),
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

        self.assertExport([self.expected_a, self.expected_b])

    def test_argument_errors_do_not_create_database(self):
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())
        self.assertRelinkError(str(self.file_a_old), db=fresh)
        self.assertRelinkError("", str(self.file_a_new), db=fresh)
        self.assertRelinkError(str(self.file_a_old), "", db=fresh)
        # 新文件不存在属于参数/路径校验，在打开数据库之前拒绝。
        self.assertRelinkError(
            str(self.file_a_old), str(self.tmp_dir / "absent.bin"), db=fresh
        )
        self.assertFalse(fresh.exists())

    def test_fresh_database_created_then_unregistered_reported(self):
        fresh = self.tmp_dir / "fresh.sqlite"
        self.file_a_new.write_text("relocated A\n", encoding="utf-8")
        self.assertFalse(fresh.exists())
        stderr = self.assertRelinkError(
            str(self.file_a_old), str(self.file_a_new), db=fresh
        )
        self.assertIn(self.path_a_old, stderr)
        # 沿用自动创建规则：空库已创建且结构可用。
        self.assertTrue(fresh.exists())
        result = self.run_cli("export", db=fresh)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

    def test_missing_parent_directory_is_not_created(self):
        missing = self.tmp_dir / "no" / "such" / "catalog.sqlite"
        self.file_a_new.write_text("relocated A\n", encoding="utf-8")
        self.assertRelinkError(
            str(self.file_a_old), str(self.file_a_new), db=missing
        )
        self.assertFalse((self.tmp_dir / "no").exists())

    def test_database_open_and_schema_errors_rejected(self):
        self.register_samples()
        self.file_a_new.write_text("relocated A\n", encoding="utf-8")

        # 数据库路径指向目录。
        target = self.tmp_dir / "a_directory"
        target.mkdir()
        self.assertRelinkError(
            str(self.file_a_old), str(self.file_a_new), db=target
        )

        # 非 SQLite 文件：拒绝且字节不变。
        non_sqlite = self.tmp_dir / "broken.sqlite"
        non_sqlite.write_text("not a sqlite database", encoding="utf-8")
        before = non_sqlite.read_bytes()
        self.assertRelinkError(
            str(self.file_a_old), str(self.file_a_new), db=non_sqlite
        )
        self.assertEqual(non_sqlite.read_bytes(), before)

        # 结构不兼容：拒绝且原有记录保留。
        foreign = self.tmp_dir / "foreign.sqlite"
        conn = sqlite3.connect(str(foreign))
        conn.execute(
            "CREATE TABLE business_record(id INTEGER PRIMARY KEY, note TEXT)"
        )
        conn.execute("INSERT INTO business_record(note) VALUES ('fixed record')")
        conn.commit()
        conn.close()
        before = foreign.read_bytes()
        self.assertRelinkError(
            str(self.file_a_old), str(self.file_a_new), db=foreign
        )
        self.assertEqual(foreign.read_bytes(), before)

        # 失败不影响既有数据库中的记录。
        self.assertExport([self.expected_a, self.expected_b])

    def test_write_failure_rolls_back_then_recovers(self):
        self.register_samples()
        self.move_a_to_new()

        # 附加固定拒绝条件：拒绝把路径更新为 A.new 所在目录以外的 blocked 目标。
        blocked_new = self.tmp_dir / "A.blocked"
        blocked_new.write_text("blocked target\n", encoding="utf-8")
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(CREATE_REJECT_TRIGGER_SQL)
        conn.commit()
        conn.close()

        stderr = self.assertRelinkError(
            str(self.file_a_old), str(blocked_new)
        )
        self.assertIn("数据库读写失败", stderr)
        self.assertIn("relink blocked", stderr)

        # 失败后原记录完整保留：路径、类型、标签及登记顺序不变。
        self.assertExport([self.expected_a, self.expected_b])
        self.assertShow(self.expected_a, str(self.file_a_old))

        # 撤去拒绝条件后同一输入成功。
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("DROP TRIGGER reject_relink_to_blocked")
        conn.commit()
        conn.close()
        path_blocked = os.path.realpath(str(blocked_new))
        self.assertRelinkOk(
            {"path": path_blocked, "type": "image", "tags": ["demo", "ui"]},
            str(self.file_a_old),
            str(blocked_new),
        )
        self.assertExport(
            [
                {"path": path_blocked, "type": "image", "tags": ["demo", "ui"]},
                self.expected_b,
            ]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
