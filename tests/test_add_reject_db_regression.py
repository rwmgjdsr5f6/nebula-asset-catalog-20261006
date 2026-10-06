"""add 拒绝损坏或不兼容数据库的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile / sqlite3），
无需第三方依赖。

覆盖：
- 数据库文件内容为固定的非 SQLite 文本：add 退出码 2、标准输出为空、
  标准错误说明数据库原因且不含调用栈；
- 数据库文件是含其他业务表（及一条固定记录）的 SQLite 文件：同样被拒绝，
  既不被覆盖也不被补表或重建；
- 数据库文件具有 asset 与 asset_tag 表但 asset 缺少 type 列：同样被拒绝；
- 以上每种场景失败前后，数据库文件的字节内容完全一致，
  待登记的样例素材内容也不变；
- 对照样例：正常数据库先登记类型 image 的素材 A，再向同一数据库登记
  路径不同、类型 audio 的素材 B（两者标签均为 demo），第二次登记退出码 0、
  标准错误为空、标准输出为 B 的规范路径、类型与完整标签；
  随后由新启动的 query 进程查询 demo，按登记顺序返回 A、B，原记录不变。

每次登记都提供存在的普通文件、非空类型与有效标签，
避免把素材参数错误混入数据库保护检查。
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

# 固定的非 SQLite 文本，用于冒充数据库文件内容。
NOT_SQLITE_CONTENT = b"this is not a sqlite database file\n"

# 样例素材的固定内容；登记前后必须保持一致。
SAMPLE_CONTENT = b"add reject-db regression sample\n"


class AddRejectDatabaseRegressionTest(unittest.TestCase):
    def setUp(self):
        # 临时目录建在项目根目录下，与既有回归测试一致；
        # 目录、样例与数据库均由本用例自建，tearDown 时整体清理。
        self._tmp = tempfile.TemporaryDirectory(dir=PROJECT_ROOT)
        self.tmp_dir = Path(self._tmp.name)
        self.db_path = self.tmp_dir / "catalog.sqlite"

        # 每次登记都使用存在的普通文件、非空类型与有效标签。
        self.sample = self.tmp_dir / "sample.bin"
        self.sample.write_bytes(SAMPLE_CONTENT)
        self.canonical_sample = os.path.realpath(str(self.sample))

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

    def add_sample(self, path=None, asset_type="image", tag="demo"):
        """登记一个存在的普通文件，非空类型与有效标签。"""
        return self.run_cli(
            "add",
            str(path if path is not None else self.sample),
            "--type",
            asset_type,
            "--tag",
            tag,
        )

    def assert_add_rejected_and_files_untouched(self, label):
        """add 被拒绝：退出码 2、标准输出为空、标准错误说明数据库或结构原因、
        无调用栈；失败前后数据库文件字节完全一致，样例素材内容不变。"""
        db_bytes_before = self.db_path.read_bytes()

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

        # 已有数据库文件不能被覆盖、补表或重建：字节内容完全一致。
        self.assertTrue(self.db_path.exists(), f"{label}: 数据库文件被删除")
        self.assertEqual(
            self.db_path.read_bytes(),
            db_bytes_before,
            f"{label}: 失败后数据库文件的字节内容发生变化",
        )
        # 待登记的样例素材内容也不变。
        self.assertEqual(
            self.sample.read_bytes(),
            SAMPLE_CONTENT,
            f"{label}: 样例素材内容被修改",
        )
        return result

    def test_rejects_non_sqlite_database_file(self):
        """数据库文件内容为固定的非 SQLite 文本。"""
        self.db_path.write_bytes(NOT_SQLITE_CONTENT)
        self.assert_add_rejected_and_files_untouched("非 SQLite 文本数据库文件")

    def test_rejects_sqlite_with_foreign_business_table(self):
        """SQLite 文件含其他业务表及一条固定记录，不属于本产品结构。"""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "CREATE TABLE business_record ("
            "id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO business_record(id, name) VALUES (1, 'fixed')")
        conn.commit()
        conn.close()

        self.assert_add_rejected_and_files_untouched("含其他业务表的 SQLite 文件")

        # 原有业务表与固定记录仍在（字节一致之外的直接确认）。
        conn = sqlite3.connect(str(self.db_path))
        rows = conn.execute(
            "SELECT id, name FROM business_record ORDER BY id"
        ).fetchall()
        conn.close()
        self.assertEqual(rows, [(1, "fixed")])

    def test_rejects_schema_missing_type_column(self):
        """SQLite 文件具有 asset 与 asset_tag 表，但 asset 缺少 type 列。"""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "CREATE TABLE asset ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE)"
        )
        conn.execute(
            "CREATE TABLE asset_tag ("
            "asset_id INTEGER NOT NULL, tag TEXT NOT NULL, "
            "position INTEGER NOT NULL, PRIMARY KEY (asset_id, tag))"
        )
        conn.commit()
        conn.close()

        self.assert_add_rejected_and_files_untouched("asset 缺少 type 列的 SQLite 文件")

    def test_control_valid_database_registers_two_assets(self):
        """对照样例：正常数据库依次登记 A（image）与 B（audio），标签均为 demo。"""
        sample_b = self.tmp_dir / "sample_b.bin"
        sample_b.write_bytes(SAMPLE_CONTENT)
        canonical_b = os.path.realpath(str(sample_b))

        expected_a = {
            "path": self.canonical_sample,
            "type": "image",
            "tags": ["demo"],
        }
        expected_b = {"path": canonical_b, "type": "audio", "tags": ["demo"]}

        # 第一次登记：A，类型 image。
        result_a = self.add_sample()
        self.assertEqual(result_a.returncode, 0, result_a.stderr)
        self.assertEqual(result_a.stderr, "")
        self.assertEqual(json.loads(result_a.stdout), expected_a)

        # 第二次登记：同一数据库、路径不同、类型 audio 的 B。
        result_b = self.add_sample(path=sample_b, asset_type="audio")
        self.assertEqual(result_b.returncode, 0, result_b.stderr)
        self.assertEqual(result_b.stderr, "")
        payload_b = json.loads(result_b.stdout)
        self.assertEqual(payload_b, expected_b)
        self.assertEqual(payload_b["path"], canonical_b)
        self.assertEqual(payload_b["type"], "audio")
        self.assertEqual(payload_b["tags"], ["demo"])

        # 新启动的 query 进程查询 demo：按登记顺序返回 A、B，原记录不变。
        result_q = self.run_cli("query", "--tag", "demo")
        self.assertEqual(result_q.returncode, 0, result_q.stderr)
        self.assertEqual(result_q.stderr, "")
        self.assertEqual(json.loads(result_q.stdout), [expected_a, expected_b])

        # 样例素材内容在全部操作后保持不变。
        self.assertEqual(self.sample.read_bytes(), SAMPLE_CONTENT)
        self.assertEqual(sample_b.read_bytes(), SAMPLE_CONTENT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
