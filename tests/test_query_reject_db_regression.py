"""query 拒绝损坏或不兼容数据库的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile / sqlite3），
无需第三方依赖，不访问网络。

覆盖：
- 数据库文件内容为固定的非 SQLite 文本：query 退出码 2、标准输出为空、
  标准错误说明数据库无法打开或结构不兼容的原因且不含调用栈；
- 数据库文件是只含 business_record 表（及一条固定记录）的 SQLite 文件：
  同样被拒绝，既不被覆盖也不被补表或重建，原有固定记录仍可读出；
- 数据库文件具有 asset 与 asset_tag 表但 asset 缺少 type 列
  （asset_tag 保持原有三列结构）：同样被拒绝；
- 以上每种场景查询前后数据库文件的字节内容完全一致；
  重复查询一次得到相同拒绝结果，原内容继续保留；
- 对照样例：正常数据库依次登记类型 image、标签 demo 与 ui 的素材 A，
  以及类型 audio、标签 demo 的素材 B；由新启动的 query 进程查询 demo，
  退出码 0、标准错误为空，输出 JSON 数组按登记顺序各包含 A、B 一次，
  保留规范绝对路径、类型与完整标签顺序；查询前后数据库字节与
  两个素材的内容不变。

每次查询都使用有效的 ``query --tag demo``，
避免把参数错误混入数据库保护检查。add 仅用于准备合法对照数据。
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

# 样例素材的固定内容；登记与查询前后必须保持一致。
SAMPLE_CONTENT_A = b"query reject-db regression sample A\n"
SAMPLE_CONTENT_B = b"query reject-db regression sample B\n"


class QueryRejectDatabaseRegressionTest(unittest.TestCase):
    def setUp(self):
        # 临时目录建在项目根目录下，与既有回归测试一致；
        # 目录、样例与数据库均由本用例自建，tearDown 时整体清理。
        self._tmp = tempfile.TemporaryDirectory(dir=PROJECT_ROOT)
        self.tmp_dir = Path(self._tmp.name)
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

    def query_demo(self):
        """以有效的 query --tag demo 查询，避免参数错误掩盖数据库错误。"""
        return self.run_cli("query", "--tag", "demo")

    def assert_query_rejected(self, result, label):
        """query 被拒绝：退出码 2、标准输出为空、标准错误说明数据库无法打开
        或结构不兼容的原因、无调用栈。"""
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
            f"{label}: 标准错误未说明数据库无法打开或结构不兼容的原因: "
            f"{result.stderr!r}",
        )
        self.assertNotIn(
            "Traceback",
            result.stderr,
            f"{label}: 标准错误含调用栈: {result.stderr!r}",
        )

    def assert_query_rejected_and_db_untouched(self, label):
        """查询前后数据库文件存在且字节完全一致（不覆盖、不补表、不重建）；
        重复查询一次得到相同拒绝结果，原内容继续保留。"""
        self.assertTrue(
            self.db_path.exists(), f"{label}: 前置数据库文件不存在"
        )
        db_bytes_before = self.db_path.read_bytes()

        result_first = self.query_demo()
        self.assert_query_rejected(result_first, label)

        # 已有数据库文件不能被覆盖、补表或重建：字节内容完全一致。
        self.assertTrue(self.db_path.exists(), f"{label}: 数据库文件被删除")
        self.assertEqual(
            self.db_path.read_bytes(),
            db_bytes_before,
            f"{label}: 查询失败后数据库文件的字节内容发生变化",
        )

        # 重复查询一次：相同拒绝结果，原内容继续保留。
        result_second = self.query_demo()
        self.assert_query_rejected(result_second, f"{label}（重复查询）")
        self.assertEqual(
            result_second.stderr,
            result_first.stderr,
            f"{label}: 重复查询的拒绝结果与首次不一致: "
            f"首次 stderr={result_first.stderr!r} "
            f"重复 stderr={result_second.stderr!r}",
        )
        self.assertTrue(
            self.db_path.exists(), f"{label}: 重复查询后数据库文件被删除"
        )
        self.assertEqual(
            self.db_path.read_bytes(),
            db_bytes_before,
            f"{label}: 重复查询后数据库文件的字节内容发生变化",
        )
        return result_first

    def test_rejects_non_sqlite_database_file(self):
        """数据库文件内容为固定的非 SQLite 文本。"""
        self.db_path.write_bytes(NOT_SQLITE_CONTENT)
        self.assert_query_rejected_and_db_untouched("非 SQLite 文本数据库文件")

    def test_rejects_sqlite_with_foreign_business_table(self):
        """SQLite 文件只含 business_record 表及一条固定记录，不属于本产品结构。"""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "CREATE TABLE business_record ("
            "id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO business_record(id, name) VALUES (1, 'fixed')")
        conn.commit()
        conn.close()

        self.assert_query_rejected_and_db_untouched("含其他业务表的 SQLite 文件")

        # 原有业务表与固定记录仍在（字节一致之外的直接确认）。
        conn = sqlite3.connect(str(self.db_path))
        rows = conn.execute(
            "SELECT id, name FROM business_record ORDER BY id"
        ).fetchall()
        conn.close()
        self.assertEqual(
            rows,
            [(1, "fixed")],
            "含其他业务表的 SQLite 文件: 原有固定记录无法重新读出",
        )

    def test_rejects_schema_missing_type_column(self):
        """SQLite 文件具有 asset 与 asset_tag 表，但 asset 缺少 type 列；
        asset_tag 保持原有 asset_id / tag / position 三列结构。"""
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

        self.assert_query_rejected_and_db_untouched(
            "asset 缺少 type 列的 SQLite 文件"
        )

    def test_control_valid_database_query_returns_registered_assets(self):
        """对照样例：正常数据库依次登记 A（image，标签 demo 与 ui）与
        B（audio，标签 demo），新 query 进程查询 demo 按登记顺序返回 A、B。"""
        sample_a = self.tmp_dir / "sample_a.bin"
        sample_a.write_bytes(SAMPLE_CONTENT_A)
        canonical_a = os.path.realpath(str(sample_a))
        sample_b = self.tmp_dir / "sample_b.bin"
        sample_b.write_bytes(SAMPLE_CONTENT_B)
        canonical_b = os.path.realpath(str(sample_b))

        expected_a = {
            "path": canonical_a,
            "type": "image",
            "tags": ["demo", "ui"],
        }
        expected_b = {"path": canonical_b, "type": "audio", "tags": ["demo"]}

        # add 仅用于准备合法对照数据：A，类型 image，标签 demo 与 ui。
        result_a = self.run_cli(
            "add",
            str(sample_a),
            "--type",
            "image",
            "--tag",
            "demo",
            "--tag",
            "ui",
        )
        self.assertEqual(result_a.returncode, 0, result_a.stderr)
        self.assertEqual(result_a.stderr, "")
        self.assertEqual(json.loads(result_a.stdout), expected_a)

        # B，类型 audio，标签 demo。
        result_b = self.run_cli(
            "add", str(sample_b), "--type", "audio", "--tag", "demo"
        )
        self.assertEqual(result_b.returncode, 0, result_b.stderr)
        self.assertEqual(result_b.stderr, "")
        self.assertEqual(json.loads(result_b.stdout), expected_b)

        db_bytes_before = self.db_path.read_bytes()

        # 新启动的 query 进程查询 demo：退出码 0、标准错误为空，
        # 按登记顺序各包含 A、B 一次，保留规范绝对路径、类型与完整标签顺序。
        result_q = self.query_demo()
        self.assertEqual(result_q.returncode, 0, result_q.stderr)
        self.assertEqual(result_q.stderr, "")
        # 比较解析后的 JSON 内容，不依赖空白或对象键顺序。
        self.assertEqual(
            json.loads(result_q.stdout),
            [expected_a, expected_b],
            f"对照样例: 查询结果与登记内容不一致: {result_q.stdout!r}",
        )

        # 查询前后数据库字节与两个素材的内容不变。
        self.assertEqual(
            self.db_path.read_bytes(),
            db_bytes_before,
            "对照样例: 查询后数据库文件的字节内容发生变化",
        )
        self.assertEqual(sample_a.read_bytes(), SAMPLE_CONTENT_A)
        self.assertEqual(sample_b.read_bytes(), SAMPLE_CONTENT_B)


if __name__ == "__main__":
    unittest.main(verbosity=2)
