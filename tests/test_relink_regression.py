"""relink 重新关联本地路径的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

覆盖：
- 验收场景：按 A.old、B.bin 顺序登记后把 A 移到 A.new，
  ``relink A.old A.new`` 成功；``show A.new`` 返回 A 的完整记录，
  ``query --tag demo`` 仍依次返回 A、B，只有 A 的路径变化，
  ``show A.old`` 报告未登记；
- 成功时退出码 0、标准错误为空、标准输出仅为含 path、type、tags 的
  JSON 对象；类型、完整标签及顺序、首次登记顺序与其他记录保持不变，
  更新在重启（新进程）后仍可读取；
- 原路径按 show 的规则解析定位记录：等价写法命中同一记录，
  原文件已删除或原位置变成目录也允许关联；
- 新路径按 add 的规则解析：要求当前存在且为普通文件，符号链接按目标
  解析，记录保存目标的规范绝对路径；
- 新旧规范路径相同且目标仍为普通文件时成功返回原记录；
- 操作只更新目录中的路径：不移动、复制、删除或改写文件，
  不读取素材内容或扫描目录；
- 原路径未登记、新文件不存在或不是普通文件、新路径已被其他素材登记、
  新路径等于 --db 的规范路径时：退出码 2、标准输出为空、标准错误说明
  对应原因并含相关规范路径、不含调用栈，不合并或覆盖任何记录；
- 路径缺失或为空字符串、缺少 --db、数据库路径为空及不支持的参数：
  退出码 2 并指出参数原因，且不创建数据库；
- 数据库不存在且父目录存在时沿用空库初始化后报告原路径未登记；
  父目录缺失时不补建目录；
- 数据库打不开、损坏、结构不兼容或写入失败时退出码 2 并说明数据库原因，
  已有库不被覆盖或重建，原记录完整保留；
- 现有七个命令的行为与旧库兼容性保持不变。

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


class RelinkRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "A.old"
        self.file_b = self.tmp_dir / "B.bin"
        self.file_a.write_text("demo asset A\n", encoding="utf-8")
        self.file_b.write_text("demo asset B\n", encoding="utf-8")
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
        """按验收顺序登记 A.old（image: demo, ui）与 B.bin（audio: demo）。"""
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

    def run_relink(self, *args, db=None):
        return self.run_cli("relink", *args, db=db)

    def assertRelinkOk(self, expected, *args, db=None):
        result = self.run_relink(*args, db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        # 标准输出仅一行 JSON 对象。
        self.assertEqual(result.stdout.count("\n"), 1)
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

    def assertExport(self, expected, db=None):
        result = self.run_cli("export", db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected)

    def test_acceptance_move_a_then_relink(self):
        self.register_samples()

        # 验收场景：把 A 移到 A.new，执行 relink A.old A.new。
        new_a = self.tmp_dir / "A.new"
        self.file_a.rename(new_a)
        path_a_new = os.path.realpath(str(new_a))
        expected_a_new = {"path": path_a_new, "type": "image", "tags": ["demo", "ui"]}

        self.assertRelinkOk(expected_a_new, str(self.file_a), str(new_a))

        # show A.new 返回 A 的完整记录。
        result = self.run_cli("show", str(new_a))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected_a_new)

        # query --tag demo 仍依次返回 A、B，只有 A 的路径变化。
        result = self.run_cli("query", "--tag", "demo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [expected_a_new, self.expected_b])

        # show A.old 报告未登记。
        result = self.run_cli("show", str(self.file_a))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(self.path_a, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

        # export 仍按 A、B 的首次登记顺序返回。
        self.assertExport([expected_a_new, self.expected_b])

    def test_relink_persists_across_processes(self):
        self.register_samples()
        new_a = self.tmp_dir / "A.new"
        self.file_a.rename(new_a)
        path_a_new = os.path.realpath(str(new_a))
        expected_a_new = {"path": path_a_new, "type": "image", "tags": ["demo", "ui"]}
        self.assertRelinkOk(expected_a_new, str(self.file_a), str(new_a))

        # 由全新进程读取：更新在重启后仍可读取，B 的记录不变。
        self.assertExport([expected_a_new, self.expected_b])
        result = self.run_cli("query", "--tag", "demo", "--type", "image")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [expected_a_new])

    def test_deleted_or_directory_source_still_relinkable(self):
        self.register_samples()

        # 原文件已删除也允许关联。
        new_a = self.tmp_dir / "A.new"
        new_a.write_text("replacement A\n", encoding="utf-8")
        self.file_a.unlink()
        self.assertRelinkOk(
            {"path": os.path.realpath(str(new_a)), "type": "image", "tags": ["demo", "ui"]},
            str(self.file_a), str(new_a),
        )

        # 原位置变成目录也允许关联。
        new_b = self.tmp_dir / "B.new"
        new_b.write_text("replacement B\n", encoding="utf-8")
        self.file_b.unlink()
        os.mkdir(self.path_b)
        self.assertRelinkOk(
            {"path": os.path.realpath(str(new_b)), "type": "audio", "tags": ["demo"]},
            str(self.file_b), str(new_b),
        )

    def test_equivalent_old_path_spellings_resolve_same_record(self):
        self.register_samples()
        relative = os.path.relpath(self.path_a, PROJECT_ROOT)
        dotted = str(self.tmp_dir / "." / "A.old")

        # 每种等价写法定位同一记录：关联到新目标后再关联回 A.old，
        # 以便下一种写法仍能通过 A.old 定位该记录。
        for index, spelling in enumerate((relative, dotted)):
            target = self.tmp_dir / f"A.{index}.new"
            target.write_text("replacement\n", encoding="utf-8")
            data = self.assertRelinkOk(
                {
                    "path": os.path.realpath(str(target)),
                    "type": "image",
                    "tags": ["demo", "ui"],
                },
                spelling, str(target),
            )
            self.assertNotEqual(data["path"], self.path_a)
            self.assertRelinkOk(self.expected_a, str(target), str(self.file_a))

    def test_same_canonical_path_returns_original_record(self):
        self.register_samples()
        # 新旧规范路径相同（含 . / .. 等价写法）且目标仍为普通文件：
        # 成功返回原记录，其他记录不变。
        dotted = str(self.tmp_dir / "sub" / ".." / "A.old")
        os.mkdir(str(self.tmp_dir / "sub"))
        self.assertRelinkOk(self.expected_a, str(self.file_a), dotted)
        self.assertRelinkOk(self.expected_a, str(self.file_a), str(self.file_a))
        self.assertExport([self.expected_a, self.expected_b])

    def test_new_path_symlink_resolves_to_target(self):
        self.register_samples()
        target = self.tmp_dir / "real.bin"
        target.write_text("real content\n", encoding="utf-8")
        link = self.tmp_dir / "link.bin"
        try:
            os.symlink(str(target), str(link))
        except (OSError, NotImplementedError):
            self.skipTest("当前环境不支持创建符号链接")

        # 符号链接按目标解析，记录保存目标的规范绝对路径。
        expected = {
            "path": os.path.realpath(str(target)),
            "type": "image",
            "tags": ["demo", "ui"],
        }
        self.assertRelinkOk(expected, str(self.file_a), str(link))
        result = self.run_cli("show", str(target))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), expected)

    def test_relink_does_not_touch_files(self):
        self.register_samples()
        new_a = self.tmp_dir / "A.new"
        new_a.write_text("replacement A\n", encoding="utf-8")
        before_a = self.file_a.read_bytes()
        before_new = new_a.read_bytes()
        before_b = self.file_b.read_bytes()

        self.assertRelinkOk(
            {"path": os.path.realpath(str(new_a)), "type": "image", "tags": ["demo", "ui"]},
            str(self.file_a), str(new_a),
        )

        # 不移动、复制、删除或改写任何文件：原位置与新位置内容均不变。
        self.assertEqual(self.file_a.read_bytes(), before_a)
        self.assertEqual(new_a.read_bytes(), before_new)
        self.assertEqual(self.file_b.read_bytes(), before_b)

    def test_unregistered_old_path_rejected_with_canonical_path(self):
        self.register_samples()
        ghost = self.tmp_dir / "C.bin"
        new_target = self.tmp_dir / "C.new"
        new_target.write_text("new\n", encoding="utf-8")
        stderr = self.assertRelinkError(str(ghost), str(new_target))
        self.assertIn(os.path.realpath(str(ghost)), stderr)
        # 失败不新增素材、不改变已有记录。
        self.assertExport([self.expected_a, self.expected_b])

    def test_missing_or_non_file_new_path_rejected(self):
        self.register_samples()

        # 新文件不存在。
        missing = self.tmp_dir / "missing.bin"
        stderr = self.assertRelinkError(str(self.file_a), str(missing))
        self.assertIn(os.path.realpath(str(missing)), stderr)

        # 新路径是目录，不是普通文件。
        directory = self.tmp_dir / "a_directory"
        directory.mkdir()
        stderr = self.assertRelinkError(str(self.file_a), str(directory))
        self.assertIn(os.path.realpath(str(directory)), stderr)

        # 失败不改变已有记录。
        self.assertExport([self.expected_a, self.expected_b])

    def test_new_path_registered_to_another_asset_rejected(self):
        self.register_samples()
        # 新路径属于另一条登记记录（B）：拒绝，不合并或覆盖。
        stderr = self.assertRelinkError(str(self.file_a), str(self.file_b))
        self.assertIn(self.path_b, stderr)
        self.assertExport([self.expected_a, self.expected_b])

    def test_new_path_equal_to_db_rejected(self):
        self.register_samples()
        # 先创建数据库（登记时已创建），再把新路径指向 --db 自身。
        stderr = self.assertRelinkError(str(self.file_a), str(self.db_path))
        self.assertIn(os.path.realpath(str(self.db_path)), stderr)
        self.assertExport([self.expected_a, self.expected_b])

    def test_argument_errors_rejected_and_no_database_created(self):
        self.register_samples()
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())

        # 缺少路径参数。
        self.assertRelinkError()
        self.assertRelinkError(str(self.file_a))
        # 路径为空字符串。
        self.assertRelinkError("", str(self.file_b), db=fresh)
        self.assertRelinkError(str(self.file_a), "", db=fresh)
        # 不支持的参数。
        self.assertRelinkError(str(self.file_a), str(self.file_b), "extra")
        self.assertRelinkError(str(self.file_a), str(self.file_b), "--tag", "x")

        # 参数错误不创建数据库。
        self.assertFalse(fresh.exists())
        self.assertExport([self.expected_a, self.expected_b])

    def test_missing_db_option_rejected(self):
        cmd = [
            sys.executable, "-m", "asset_catalog",
            "relink", "a", "b",
        ]
        result = subprocess.run(
            cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

    def test_empty_db_path_rejected(self):
        self.register_samples()
        new_a = self.tmp_dir / "A.new"
        new_a.write_text("replacement\n", encoding="utf-8")
        self.assertRelinkError(str(self.file_a), str(new_a), db="")

    def test_fresh_database_created_then_unregistered_reported(self):
        new_a = self.tmp_dir / "A.new"
        new_a.write_text("replacement\n", encoding="utf-8")
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())
        stderr = self.assertRelinkError(str(self.file_a), str(new_a), db=fresh)
        self.assertIn(self.path_a, stderr)
        # 沿用自动创建规则：空库已创建且结构可用。
        self.assertTrue(fresh.exists())
        self.assertExport([], db=fresh)

    def test_missing_parent_directory_is_not_created(self):
        new_a = self.tmp_dir / "A.new"
        new_a.write_text("replacement\n", encoding="utf-8")
        missing = self.tmp_dir / "no" / "such" / "catalog.sqlite"
        self.assertRelinkError(str(self.file_a), str(new_a), db=missing)
        self.assertFalse((self.tmp_dir / "no").exists())

    def test_database_open_and_schema_errors_rejected(self):
        self.register_samples()
        new_a = self.tmp_dir / "A.new"
        new_a.write_text("replacement\n", encoding="utf-8")

        # 数据库路径指向目录。
        target = self.tmp_dir / "a_directory"
        target.mkdir()
        self.assertRelinkError(str(self.file_a), str(new_a), db=target)

        # 非 SQLite 文件：拒绝且字节不变。
        non_sqlite = self.tmp_dir / "broken.sqlite"
        non_sqlite.write_text("not a sqlite database", encoding="utf-8")
        before = non_sqlite.read_bytes()
        self.assertRelinkError(str(self.file_a), str(new_a), db=non_sqlite)
        self.assertEqual(non_sqlite.read_bytes(), before)

        # 结构不兼容：拒绝且原有记录保留。
        foreign = self.tmp_dir / "foreign.sqlite"
        conn = sqlite3.connect(str(foreign))
        conn.execute("CREATE TABLE business_record(id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO business_record(note) VALUES ('fixed record')")
        conn.commit()
        conn.close()
        before = foreign.read_bytes()
        self.assertRelinkError(str(self.file_a), str(new_a), db=foreign)
        self.assertEqual(foreign.read_bytes(), before)

        # 失败不影响既有数据库中的记录。
        self.assertExport([self.expected_a, self.expected_b])

    def test_write_failure_rolls_back_and_keeps_original_record(self):
        self.register_samples()
        new_a = self.tmp_dir / "A.new"
        new_a.write_text("replacement\n", encoding="utf-8")

        # 附加固定拒绝条件：保留原有兼容表结构，仅加触发器拒绝路径更新。
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            """
            CREATE TRIGGER reject_relink
            BEFORE UPDATE OF path ON asset
            BEGIN
                SELECT RAISE(ABORT, 'relink blocked');
            END;
            """
        )
        conn.commit()
        conn.close()

        stderr = self.assertRelinkError(str(self.file_a), str(new_a))
        self.assertIn("relink blocked", stderr)
        # 整体回滚：原记录完整保留，不留部分修改。
        self.assertExport([self.expected_a, self.expected_b])

        # 撤去拒绝条件后同一输入成功。
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("DROP TRIGGER reject_relink")
        conn.commit()
        conn.close()
        expected_a_new = {
            "path": os.path.realpath(str(new_a)),
            "type": "image",
            "tags": ["demo", "ui"],
        }
        self.assertRelinkOk(expected_a_new, str(self.file_a), str(new_a))
        self.assertExport([expected_a_new, self.expected_b])

    def test_other_commands_unchanged_after_relink(self):
        self.register_samples()
        new_a = self.tmp_dir / "A.new"
        new_a.write_text("replacement\n", encoding="utf-8")
        path_a_new = os.path.realpath(str(new_a))
        self.assertRelinkOk(
            {"path": path_a_new, "type": "image", "tags": ["demo", "ui"]},
            str(self.file_a), str(new_a),
        )

        # add 仍拒绝新路径的重复登记。
        result = self.run_cli(
            "add", str(new_a), "--type", "image", "--tag", "other"
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

        # retag / retype 通过新路径照常工作。
        result = self.run_cli("retag", str(new_a), "--tag", "new")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"path": path_a_new, "type": "image", "tags": ["new"]},
        )
        result = self.run_cli("retype", str(new_a), "--type", "texture")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"path": path_a_new, "type": "texture", "tags": ["new"]},
        )

        # tags 统计不受影响。
        result = self.run_cli("tags")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            [{"tag": "demo", "asset_count": 1}, {"tag": "new", "asset_count": 1}],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
