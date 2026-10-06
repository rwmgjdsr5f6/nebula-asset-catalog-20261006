"""query --check-files 按需文件状态提示的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 标准库（unittest / subprocess / tempfile / sqlite3），无需第三方依赖。

覆盖：
- 固定样例验收：A、B 均以 image 类型、demo 标签登记；保留 A 删除 B 后，
  ``query --tag demo --type image --check-files`` 按 A、B 顺序各返回一次，
  状态分别为 present、missing；B 原路径改为目录后只把 B 变为 not_file；
  去掉开关后两条记录恢复 path/type/tags 三字段；
- file_status 仅作为追加字段，不改变筛选范围、首次登记顺序与完整标签顺序；
  无匹配记录时输出 []，成功时退出码 0、标准错误为空；
- 符号链接按指向对象判断：指向普通文件为 present、断开为 missing、
  指向目录为 not_file；输出 path 始终保留登记值；
- 普通文件内容变化仍是 present；目录与 FIFO 等非普通文件为 not_file；
- 状态只读取文件状态（stat），不写入数据库：数据库结构与记录保持不变，
  且兼容开关出现之前创建的数据库；
- 只检查满足标签及可选类型条件的记录，不检查其他记录或搜索其他目录；
- 权限不足导致状态无法确定时：退出码 2、标准输出为空、标准错误说明失败路径
  与原因且不含调用栈，不输出部分结果、不伪装成 missing；恢复后查询正常。

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

CONTENT_A = "sample asset A\n"
CONTENT_B = "sample asset B\n"


class QueryCheckFilesRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "sample_a.bin"
        self.file_b = self.tmp_dir / "sample_b.bin"
        self.file_a.write_text(CONTENT_A, encoding="utf-8")
        self.file_b.write_text(CONTENT_B, encoding="utf-8")
        # 登记值在 add 时固化；之后 file_b 被替换为符号链接时不能重新 realpath。
        self.path_a = os.path.realpath(str(self.file_a))
        self.path_b = os.path.realpath(str(self.file_b))
        self.db_path = self.tmp_dir / "catalog.sqlite"
        # 记录需要恢复权限的目录，避免临时目录清理失败。
        self._locked_dirs = []

    def tearDown(self):
        for locked in self._locked_dirs:
            try:
                os.chmod(locked, 0o755)
            except OSError:
                pass
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
        return subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )

    def register_ab(self):
        """按验收规则依次登记 A、B：类型均为 image、标签均为 demo。"""
        for path in (self.file_a, self.file_b):
            result = self.run_cli(
                "add",
                str(path),
                "--type",
                "image",
                "--tag",
                "demo",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")

    def query_check(self, *extra_args):
        return self.run_cli(
            "query", "--tag", "demo", "--type", "image", "--check-files", *extra_args
        )

    def assertChecked(self, expected_statuses):
        """查询成功并返回 {登记路径末段: file_status}，同时校验顺序与字段。"""
        result = self.query_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        self.assertEqual(
            [record["path"] for record in data],
            [self.path_a, self.path_b],
        )
        statuses = {
            os.path.basename(record["path"]): record["file_status"]
            for record in data
        }
        self.assertEqual(statuses, expected_statuses)
        for record in data:
            # 仅追加 file_status，原有三字段及标签顺序保持不变。
            self.assertEqual(
                set(record.keys()), {"path", "type", "tags", "file_status"}
            )
            self.assertEqual(record["type"], "image")
            self.assertEqual(record["tags"], ["demo"])
        return data

    def test_fixed_sample_present_then_missing_then_not_file(self):
        self.register_ab()

        # 保留 A、删除 B：A present，B missing（缺失文件仍随匹配记录返回）。
        self.file_b.unlink()
        self.assertFalse(self.file_b.exists())
        self.assertChecked({"sample_a.bin": "present", "sample_b.bin": "missing"})

        # 在 B 的原路径创建目录：只有 B 的状态变为 not_file。
        self.file_b.mkdir()
        self.assertChecked({"sample_a.bin": "present", "sample_b.bin": "not_file"})

    def test_without_switch_restores_three_fields(self):
        self.register_ab()
        self.file_b.unlink()
        self.file_b.mkdir()

        # 去掉 --check-files：两条记录恢复原有三字段输出，无 file_status。
        result = self.run_cli(
            "query", "--tag", "demo", "--type", "image"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertEqual(
            data,
            [
                {
                    "path": self.path_a,
                    "type": "image",
                    "tags": ["demo"],
                },
                {
                    "path": self.path_b,
                    "type": "image",
                    "tags": ["demo"],
                },
            ],
        )
        for record in data:
            self.assertNotIn("file_status", record)

    def test_no_match_outputs_empty_array(self):
        self.register_ab()
        self.file_b.unlink()

        # 标签无匹配：[]；即使开关开启也不检查任何路径。
        result = self.run_cli("query", "--tag", "nope", "--check-files")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.strip(), "[]")

        # 类型无匹配同样为 []。
        result = self.run_cli(
            "query", "--tag", "demo", "--type", "audio", "--check-files"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.strip(), "[]")

    def test_symlinks_follow_target_and_path_preserved(self):
        self.register_ab()

        cases = [
            (str(self.tmp_dir / "outside_target.bin"), "present"),
            (str(self.tmp_dir / "does_not_exist"), "missing"),
            (str(self.tmp_dir / "a_directory"), "not_file"),
        ]
        (self.tmp_dir / "outside_target.bin").write_text("link target\n")
        (self.tmp_dir / "a_directory").mkdir()

        for target, expected in cases:
            with self.subTest(target=target):
                # B 可能是普通文件、目录或上一轮的符号链接，统一移除后重建。
                if self.file_b.is_symlink():
                    self.file_b.unlink()
                elif self.file_b.is_dir():
                    self.file_b.rmdir()
                else:
                    self.file_b.unlink()
                os.symlink(target, str(self.file_b))

                data = self.assertChecked(
                    {"sample_a.bin": "present", "sample_b.bin": expected}
                )
                # 输出 path 保留登记值（仍是 B，而不是链接目标）。
                b_record = data[1]
                self.assertEqual(b_record["path"], self.path_b)

    def test_content_change_still_present_and_non_files(self):
        self.register_ab()

        # 普通文件即使内容已变仍是 present（检查不读取素材内容）。
        with open(self.file_a, "a", encoding="utf-8") as handle:
            handle.write("content changed after registration\n")
        self.assertChecked({"sample_a.bin": "present", "sample_b.bin": "present"})
        self.assertEqual(
            self.file_a.read_text(encoding="utf-8"),
            CONTENT_A + "content changed after registration\n",
        )

        # B 替换为 FIFO：存在但非普通文件，not_file。
        self.file_b.unlink()
        os.mkfifo(str(self.file_b))
        self.assertChecked({"sample_a.bin": "present", "sample_b.bin": "not_file"})

    def test_status_not_persisted_and_database_compatible(self):
        self.register_ab()
        self.file_b.unlink()
        self.query_check()

        # 数据库表结构不变，记录里没有任何状态列或状态数据。
        conn = sqlite3.connect(str(self.db_path))
        try:
            asset_cols = [
                row[1]
                for row in conn.execute("PRAGMA table_info(asset)").fetchall()
            ]
            self.assertEqual(asset_cols, ["id", "path", "type"])
            rows = conn.execute("SELECT path, type FROM asset ORDER BY id").fetchall()
            self.assertEqual(
                rows,
                [
                    (self.path_a, "image"),
                    (self.path_b, "image"),
                ],
            )
        finally:
            conn.close()

        # 开关出现之前创建的数据库（同一份文件）再次查询，行为不变。
        result = self.query_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [record["file_status"] for record in json.loads(result.stdout)],
            ["present", "missing"],
        )

    @unittest.skipIf(
        os.geteuid() == 0 if hasattr(os, "geteuid") else False,
        "root 绕过目录执行位限制，无法构造权限不足场景",
    )
    def test_only_matching_records_are_checked(self):
        self.register_ab()

        # 另有一条不匹配标签 demo 的记录，位于不可进入的目录中。
        locked = self.tmp_dir / "locked"
        locked.mkdir()
        secret = locked / "secret.bin"
        secret.write_text("secret\n", encoding="utf-8")
        result = self.run_cli(
            "add", str(secret), "--type", "image", "--tag", "secret"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        os.chmod(locked, 0o000)
        self._locked_dirs.append(str(locked))
        try:
            # 查询 demo：secret 不满足标签条件，不应被检查，查询照常成功。
            result = self.query_check()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(len(json.loads(result.stdout)), 2)

            # 查询 secret：该记录满足条件且状态无法确定，必须整体失败。
            result = self.run_cli(
                "query", "--tag", "secret", "--check-files"
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn(os.path.realpath(str(secret)), result.stderr)
            self.assertNotIn("Traceback", result.stderr)
        finally:
            os.chmod(locked, 0o755)
            self._locked_dirs.remove(str(locked))

    @unittest.skipIf(
        os.geteuid() == 0 if hasattr(os, "geteuid") else False,
        "root 绕过目录执行位限制，无法构造权限不足场景",
    )
    def test_permission_error_fails_whole_command_without_partial_output(self):
        self.register_ab()
        locked = self.tmp_dir / "locked"
        locked.mkdir()
        denied = locked / "denied.bin"
        denied.write_text("denied\n", encoding="utf-8")
        result = self.run_cli(
            "add", str(denied), "--type", "image", "--tag", "demo"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        os.chmod(locked, 0o000)
        self._locked_dirs.append(str(locked))
        try:
            # 三条匹配记录中最后一条无法 stat：退出码 2、标准输出为空，
            # 标准错误说明失败路径与原因且无调用栈，不输出部分结果。
            result = self.query_check()
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn(os.path.realpath(str(denied)), result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertNotIn("present", result.stderr)
        finally:
            os.chmod(locked, 0o755)
            self._locked_dirs.remove(str(locked))

        # 权限恢复后再次查询：A、B、denied 三条全部成功，B 已删除仍为 missing。
        self.file_b.unlink()
        result = self.query_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(
            [record["file_status"] for record in data],
            ["present", "missing", "present"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
