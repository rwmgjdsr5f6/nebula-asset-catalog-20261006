"""add 拒绝损坏或不兼容数据库的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile / sqlite3），
无需第三方依赖。sqlite3 仅用于在测试准备阶段构造样例数据库，
被测行为一律从公开命令行入口观察，不直接调用内部函数。

覆盖：
- 数据库文件内容为固定的非 SQLite 文本：add 退出码 2、标准输出为空、
  标准错误说明数据库原因且不含调用栈；
- 数据库为含其他业务表及一条固定记录的 SQLite 文件：同样被拒绝，
  标准错误说明结构原因；
- 数据库具有 asset 与 asset_tag 表但 asset 缺少 type 列：同样被拒绝，
  标准错误说明结构原因；
- 每种拒绝场景下，数据库文件的字节内容在失败前后完全一致，
  样例素材内容不变——已有文件不被覆盖、补表或重建；
- 对照样例：正常目录下先登记素材 A（image，标签 demo），
  再向同一数据库登记路径不同、类型为 audio 的素材 B（标签 demo），
  第二次登记退出码 0、标准错误为空、标准输出为 B 的规范路径、类型与完整标签；
  重新启动 query 查询 demo 按登记顺序返回 A、B，原记录不变。

每个用例使用独立的临时目录与数据库，只创建/清理自己的样例文件。
每次登记均提供存在的普通文件、非空类型与有效标签，
避免把素材参数错误混入数据库保护检查。
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

SAMPLE_CONTENT = "add database protection regression sample\n"
NOT_SQLITE_CONTENT = "this is a fixed plain-text file, not a SQLite database\n"


class AddDatabaseProtectionTest(unittest.TestCase):
    def setUp(self):
        # 临时目录建在项目根目录下；目录、样例与数据库均由本用例自建，
        # tearDown 时整体清理，不接触已有素材。
        self._tmp = tempfile.TemporaryDirectory(dir=PROJECT_ROOT)
        self.tmp_dir = Path(self._tmp.name)
        self.sample_file = self.tmp_dir / "sample.bin"
        self.sample_file.write_text(SAMPLE_CONTENT, encoding="utf-8")
        self.db_path = self.tmp_dir / "catalog.sqlite"

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
        return subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )

    def add_sample(self, path=None, asset_type="image", tags=("demo",)):
        """登记一个存在的普通文件：非空类型、有效标签，返回 CompletedProcess。"""
        args = [
            "add",
            str(path if path is not None else self.sample_file),
            "--type",
            asset_type,
        ]
        for tag in tags:
            args += ["--tag", tag]
        return self.run_cli(*args)

    def assertAddRejectedForDatabase(self, label):
        """登记被拒绝：退出码 2、标准输出为空、标准错误说明数据库/结构原因、
        单行且不含调用栈；样例素材内容不变。"""
        result = self.add_sample()
        self.assertEqual(
            result.returncode,
            2,
            f"{label}: 期望退出码 2，实际 {result.returncode}，"
            f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )
        self.assertEqual(
            result.stdout,
            "",
            f"{label}: 期望标准输出为空，实际 {result.stdout!r}",
        )
        self.assertNotEqual(
            result.stderr.strip(),
            "",
            f"{label}: 标准错误应说明拒绝原因",
        )
        self.assertEqual(
            len(result.stderr.strip().splitlines()),
            1,
            f"{label}: 标准错误应为单行说明，实际 {result.stderr!r}",
        )
        self.assertIn(
            "数据库",
            result.stderr,
            f"{label}: 标准错误未说明数据库或结构原因: {result.stderr!r}",
        )
        self.assertNotIn(
            "Traceback",
            result.stderr,
            f"{label}: 标准错误含调用栈: {result.stderr!r}",
        )
        # 样例素材内容不变。
        self.assertEqual(
            self.sample_file.read_text(encoding="utf-8"),
            SAMPLE_CONTENT,
            f"{label}: 样例素材内容被修改",
        )
        return result

    def assertDatabaseBytesUnchanged(self, before, label):
        """失败前后数据库文件的字节内容完全一致。"""
        after = self.db_path.read_bytes()
        self.assertEqual(
            before,
            after,
            f"{label}: 数据库文件内容被修改（覆盖、补表或重建）",
        )

    def test_non_sqlite_text_database_rejected(self):
        label = "内容为固定非 SQLite 文本的数据库文件"
        self.db_path.write_text(NOT_SQLITE_CONTENT, encoding="utf-8")
        before = self.db_path.read_bytes()

        self.assertAddRejectedForDatabase(label)
        self.assertDatabaseBytesUnchanged(before, label)

    def test_sqlite_with_foreign_tables_rejected(self):
        label = "含其他业务表及一条固定记录的 SQLite 数据库"
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "CREATE TABLE invoice (id INTEGER PRIMARY KEY, title TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO invoice(id, title) VALUES (1, 'fixed-record')")
        conn.commit()
        conn.close()
        before = self.db_path.read_bytes()

        self.assertAddRejectedForDatabase(label)
        self.assertDatabaseBytesUnchanged(before, label)

        # 原有业务表与固定记录仍在，未被补表或重建。
        conn = sqlite3.connect(str(self.db_path))
        rows = conn.execute("SELECT id, title FROM invoice").fetchall()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        conn.close()
        self.assertEqual(rows, [(1, "fixed-record")], f"{label}: 原有记录被修改")
        self.assertNotIn("asset", tables, f"{label}: 数据库被补建 asset 表")
        self.assertNotIn("asset_tag", tables, f"{label}: 数据库被补建 asset_tag 表")

    def test_incompatible_schema_missing_type_column_rejected(self):
        label = "asset 表缺少 type 列的 SQLite 数据库"
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "CREATE TABLE asset (id INTEGER PRIMARY KEY, path TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE asset_tag ("
            "asset_id INTEGER NOT NULL, tag TEXT NOT NULL, position INTEGER NOT NULL)"
        )
        conn.commit()
        conn.close()
        before = self.db_path.read_bytes()

        self.assertAddRejectedForDatabase(label)
        self.assertDatabaseBytesUnchanged(before, label)

    def test_valid_database_accepts_second_asset_in_order(self):
        """对照样例：正常目录下 A、B 依次登记成功，查询按登记顺序返回。"""
        file_a = self.tmp_dir / "asset_a.bin"
        file_b = self.tmp_dir / "asset_b.bin"
        file_a.write_text(SAMPLE_CONTENT, encoding="utf-8")
        file_b.write_text(SAMPLE_CONTENT, encoding="utf-8")
        canonical_a = os.path.realpath(str(file_a))
        canonical_b = os.path.realpath(str(file_b))
        expected_a = {"path": canonical_a, "type": "image", "tags": ["demo"]}
        expected_b = {"path": canonical_b, "type": "audio", "tags": ["demo"]}

        # 第一次登记：素材 A，类型 image，标签 demo。
        result_a = self.add_sample(path=file_a, asset_type="image", tags=("demo",))
        self.assertEqual(result_a.returncode, 0, result_a.stderr)
        self.assertEqual(result_a.stderr, "")
        self.assertEqual(json.loads(result_a.stdout), expected_a)

        # 第二次登记：同一数据库、路径不同的素材 B，类型 audio，标签 demo。
        result_b = self.add_sample(path=file_b, asset_type="audio", tags=("demo",))
        self.assertEqual(result_b.returncode, 0, result_b.stderr)
        self.assertEqual(result_b.stderr, "")
        payload_b = json.loads(result_b.stdout)
        self.assertIsInstance(payload_b, dict)
        self.assertEqual(payload_b, expected_b)
        self.assertTrue(os.path.isabs(payload_b["path"]))
        self.assertEqual(payload_b["type"], "audio")
        self.assertEqual(payload_b["tags"], ["demo"])

        # 重新启动 query 进程查询 demo：按登记顺序返回 A、B，原记录不变。
        result_q = self.run_cli("query", "--tag", "demo")
        self.assertEqual(result_q.returncode, 0, result_q.stderr)
        self.assertEqual(result_q.stderr, "")
        self.assertEqual(json.loads(result_q.stdout), [expected_a, expected_b])


if __name__ == "__main__":
    unittest.main(verbosity=2)
