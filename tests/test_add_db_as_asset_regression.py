"""add 拒绝把目录数据库自身登记为素材的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile / sqlite3），
无需第三方依赖。

覆盖：
- 正常登记一个 image 素材（标签依次为 demo、ui）后，以目录数据库作为素材
  路径、以 database 类型和 self 标签发起 add：退出码 2、标准输出为空、
  标准错误说明目录数据库不能作为本次素材登记并包含冲突的规范绝对路径，
  不含调用栈；
- 相对路径与绝对路径、含 ``.`` / ``..`` 的等价写法，以及（环境支持时）
  符号链接写法均适用；数据库路径与素材路径哪一侧使用等价写法不影响结论；
- 拒绝后数据库文件字节及既有记录保持不变：query --tag demo 仍只返回
  原素材及完整标签，query --tag self 返回 []；
- 已有空文件与数据库路径相同时同样被拒绝，且不会因此被初始化为目录数据库；
- 已有非 SQLite 文件或不兼容 SQLite 文件在两条路径相同且其他登记参数
  有效时，同样报告路径冲突并保持原内容；
- 对照样例：路径不同的另一份 SQLite 文件仍能登记到该目录数据库，
  不按扩展名或内容一概拒绝素材；
- 登记路径不存在、不是普通文件、类型或标签去空白后为空时，仍沿用原有
  错误规则（退出码 2、标准输出为空、说明对应原因），不创建数据库文件。

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

# 样例素材的固定内容；登记前后必须保持一致。
SAMPLE_CONTENT = b"add db-as-asset regression sample\n"

# 固定的非 SQLite 文本，用于冒充与数据库同路径的文件内容。
NOT_SQLITE_CONTENT = b"this is not a sqlite database file\n"


class AddDbAsAssetRegressionTest(unittest.TestCase):
    def setUp(self):
        # 临时目录建在项目根目录下，与既有回归测试一致；
        # 目录、样例与数据库均由本用例自建，tearDown 时整体清理。
        self._tmp = tempfile.TemporaryDirectory(dir=PROJECT_ROOT)
        self.tmp_dir = Path(self._tmp.name)
        self.db_path = self.tmp_dir / "catalog.sqlite"
        self.canonical_db = os.path.realpath(str(self.db_path))

        self.sample = self.tmp_dir / "sample.png"
        self.sample.write_bytes(SAMPLE_CONTENT)
        self.canonical_sample = os.path.realpath(str(self.sample))

        self.expected_sample = {
            "path": self.canonical_sample,
            "type": "image",
            "tags": ["demo", "ui"],
        }

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args, db=None):
        """以全新进程运行 python -m asset_catalog，返回 CompletedProcess。

        db 缺省时使用本用例的数据库路径；可传入等价写法覆盖。
        """
        cmd = [
            sys.executable,
            "-m",
            "asset_catalog",
            "--db",
            str(db if db is not None else self.db_path),
            *args,
        ]
        return subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )

    def register_sample(self):
        """正常登记 image 素材：标签依次为 demo、ui。"""
        result = self.run_cli(
            "add",
            str(self.sample),
            "--type",
            "image",
            "--tag",
            "demo",
            "--tag",
            "ui",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), self.expected_sample)
        return result

    def add_db_as_asset(self, asset_path, db=None):
        """以 database 类型和 self 标签，把数据库路径作为素材发起 add。"""
        return self.run_cli(
            "add",
            str(asset_path),
            "--type",
            "database",
            "--tag",
            "self",
            db=db,
        )

    def assertConflictRejected(self, result, label):
        """冲突被拒绝：退出码 2、标准输出为空、标准错误说明目录数据库不能
        作为本次素材登记、包含冲突的规范绝对路径、不含调用栈。"""
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
            "目录数据库不能作为本次素材登记",
            result.stderr,
            f"{label}: 标准错误未说明目录数据库不能作为素材登记: {result.stderr!r}",
        )
        self.assertIn(
            self.canonical_db,
            result.stderr,
            f"{label}: 标准错误未包含冲突的规范绝对路径: {result.stderr!r}",
        )
        self.assertNotIn(
            "Traceback",
            result.stderr,
            f"{label}: 标准错误含调用栈: {result.stderr!r}",
        )

    def assertQueryOk(self, tag, expected, db=None):
        """查询成功：退出码 0、标准错误为空、标准输出为预期 JSON 数组。"""
        result = self.run_cli("query", "--tag", tag, db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected)

    def equivalent_db_spellings(self):
        """数据库路径的等价写法（相对、绝对、含 . / ..），均相对项目根目录。"""
        tmp_rel = os.path.relpath(self.tmp_dir, PROJECT_ROOT)
        tmp_name = os.path.basename(tmp_rel)
        db_name = os.path.basename(str(self.db_path))
        return [
            ("相对路径写法", os.path.join(tmp_rel, db_name)),
            ("绝对路径写法", str(self.db_path)),
            ("含 . 的等价路径", os.path.join(tmp_rel, ".", db_name)),
            (
                "含 .. 的等价路径",
                os.path.join(tmp_rel, "..", tmp_name, db_name),
            ),
            (
                "同时含 . 与 .. 的等价路径",
                os.path.join(tmp_rel, ".", "..", tmp_name, db_name),
            ),
        ]

    def test_db_as_asset_rejected_and_state_untouched(self):
        """验收主流程：登记 image 素材后，以数据库为素材的 add 被拒绝。"""
        self.register_sample()
        db_bytes_before = self.db_path.read_bytes()

        result = self.add_db_as_asset(self.db_path)
        self.assertConflictRejected(result, "数据库路径作为素材")

        # 数据库文件字节与既有记录保持不变。
        self.assertEqual(self.db_path.read_bytes(), db_bytes_before)
        self.assertQueryOk("demo", [self.expected_sample])
        self.assertQueryOk("self", [])
        # 样例素材内容不变。
        self.assertEqual(self.sample.read_bytes(), SAMPLE_CONTENT)

    def test_equivalent_spellings_on_both_sides_rejected(self):
        """等价写法：素材侧与数据库侧各自使用相对/绝对/含 . .. 的写法。"""
        self.register_sample()
        db_bytes_before = self.db_path.read_bytes()

        # 素材侧使用等价写法，数据库侧保持原样。
        for label, spelling in self.equivalent_db_spellings():
            with self.subTest(素材侧写法=label):
                result = self.add_db_as_asset(spelling)
                self.assertConflictRejected(result, f"素材侧{label}")

        # 数据库侧使用等价写法，素材侧使用规范绝对路径。
        for label, spelling in self.equivalent_db_spellings():
            with self.subTest(数据库侧写法=label):
                result = self.add_db_as_asset(self.canonical_db, db=spelling)
                self.assertConflictRejected(result, f"数据库侧{label}")

        # 两侧同时使用等价写法。
        both = self.equivalent_db_spellings()
        for (label_a, spelling_a), (label_b, spelling_b) in zip(both, reversed(both)):
            with self.subTest(素材侧=label_a, 数据库侧=label_b):
                result = self.add_db_as_asset(spelling_a, db=spelling_b)
                self.assertConflictRejected(result, f"两侧等价写法 {label_a}/{label_b}")

        # 全部拒绝后：数据库字节与既有记录不变。
        self.assertEqual(self.db_path.read_bytes(), db_bytes_before)
        self.assertQueryOk("demo", [self.expected_sample])
        self.assertQueryOk("self", [])

    def test_symlink_spellings_rejected(self):
        """环境支持符号链接时：链接解析后指向同一规范路径同样被拒绝。"""
        link_to_db = self.tmp_dir / "catalog_link.sqlite"
        try:
            os.symlink(self.db_path, link_to_db)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"当前环境不支持创建符号链接: {exc}")

        self.register_sample()
        db_bytes_before = self.db_path.read_bytes()

        # 素材侧使用指向数据库的符号链接。
        result = self.add_db_as_asset(link_to_db)
        self.assertConflictRejected(result, "素材侧符号链接")

        # 数据库侧使用符号链接，素材侧使用数据库真实路径。
        result = self.add_db_as_asset(self.db_path, db=link_to_db)
        self.assertConflictRejected(result, "数据库侧符号链接")

        self.assertEqual(self.db_path.read_bytes(), db_bytes_before)
        self.assertQueryOk("demo", [self.expected_sample])
        self.assertQueryOk("self", [])

    def test_empty_file_not_initialized(self):
        """已有空文件与数据库路径相同：拒绝登记，且不被初始化为目录数据库。"""
        self.db_path.write_bytes(b"")

        result = self.add_db_as_asset(self.db_path)
        self.assertConflictRejected(result, "已有空文件")

        # 文件仍为空，未被初始化为目录数据库。
        self.assertEqual(self.db_path.read_bytes(), b"")

    def test_non_sqlite_file_same_path_reports_conflict(self):
        """已有非 SQLite 文件与数据库路径相同：报告路径冲突，原内容不变。"""
        self.db_path.write_bytes(NOT_SQLITE_CONTENT)

        result = self.add_db_as_asset(self.db_path)
        self.assertConflictRejected(result, "非 SQLite 文件")

        self.assertEqual(self.db_path.read_bytes(), NOT_SQLITE_CONTENT)

    def test_incompatible_sqlite_file_same_path_reports_conflict(self):
        """已有不兼容 SQLite 文件与数据库路径相同：报告路径冲突，原内容不变。"""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "CREATE TABLE business_record ("
            "id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO business_record(id, name) VALUES (1, 'fixed')")
        conn.commit()
        conn.close()
        db_bytes_before = self.db_path.read_bytes()

        result = self.add_db_as_asset(self.db_path)
        self.assertConflictRejected(result, "不兼容 SQLite 文件")

        self.assertEqual(self.db_path.read_bytes(), db_bytes_before)

    def test_control_other_sqlite_file_still_registered(self):
        """对照样例：路径不同的另一份 SQLite 文件仍能登记到该目录数据库。"""
        self.register_sample()

        other = self.tmp_dir / "other.sqlite"
        conn = sqlite3.connect(str(other))
        conn.execute(
            "CREATE TABLE business_record ("
            "id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
        )
        conn.commit()
        conn.close()
        canonical_other = os.path.realpath(str(other))

        result = self.run_cli(
            "add", str(other), "--type", "database", "--tag", "self"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {"path": canonical_other, "type": "database", "tags": ["self"]},
        )

        # 两条记录都可查询，原素材记录不变。
        self.assertQueryOk("demo", [self.expected_sample])
        self.assertQueryOk(
            "self",
            [{"path": canonical_other, "type": "database", "tags": ["self"]}],
        )

    def test_original_error_rules_unchanged(self):
        """路径不存在、不是普通文件、类型/标签为空：沿用原有错误规则。"""
        missing = self.tmp_dir / "missing.bin"

        # 登记路径不存在：退出码 2、标准输出为空、说明原因，不创建数据库文件。
        result = self.run_cli(
            "add", str(missing), "--type", "image", "--tag", "demo"
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("不存在", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(self.db_path.exists(), "路径不存在时不应创建数据库文件")

        # 登记路径不是普通文件（目录）。
        result = self.run_cli(
            "add", str(self.tmp_dir), "--type", "image", "--tag", "demo"
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("不是普通文件", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(self.db_path.exists(), "路径非普通文件时不应创建数据库文件")

        # 类型去空白后为空。
        result = self.run_cli(
            "add", str(self.sample), "--type", "   ", "--tag", "demo"
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("--type", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(self.db_path.exists(), "类型为空时不应创建数据库文件")

        # 标签去空白后为空。
        result = self.run_cli(
            "add", str(self.sample), "--type", "image", "--tag", "  "
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("--tag", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(self.db_path.exists(), "标签为空时不应创建数据库文件")

        # 缺少必填参数（--tag）：argparse 错误同样退出码 2、标准输出为空。
        result = self.run_cli("add", str(self.sample), "--type", "image")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(self.db_path.exists(), "缺少必填参数时不应创建数据库文件")


if __name__ == "__main__":
    unittest.main(verbosity=2)
