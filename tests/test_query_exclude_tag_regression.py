"""query 按标签排除素材（--exclude-tag）的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

固定验收样例按 A、B、C 顺序登记，三者类型均为 image：
A 标签依次为 demo、ui；B 标签依次为 demo、archived；C 标签只有 ui。

覆盖：
- 验收场景一：query --tag-mode any --tag demo --tag ui --exclude-tag archived
  只按 A、C 顺序输出记录；每条记录仍仅含 path、type、tags；
- 验收场景二：query --tag demo --tag ui --exclude-tag ui 输出 []；
- 排除标签逐个去除首尾空白、重复条件只生效一次、条件顺序不影响结果；
- 按完整文本区分大小写匹配：排除 Archived 不影响 B；
- 不存在的排除标签不影响结果；同一标签同时命中与排除时仍执行排除；
- 与 --type、--check-files、--file-status 组合：被排除素材不检查文件状态，
  剩余范围沿用现有状态规则；
- 排除只作用于本次查询：不修改记录或源文件，之后不带排除的查询结果不变；
- --exclude-tag 缺值、为空或仅有空白：退出码 2、标准输出为空、标准错误
  指出该选项且不含调用栈，不创建数据库，即使其他条件有效；
- 其他子命令（add、export、retag、retype）收到 --exclude-tag 同样按参数
  错误拒绝；
- 不传 --exclude-tag 时保持已有查询行为；
- 成功时退出码 0、标准错误为空，标准输出仅为 JSON 数组。

每个用例使用独立的临时目录与数据库，只创建/清理自己的样例文件；
登记与查询分别由独立进程完成。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

CONTENT_A = "sample asset A\n"
CONTENT_B = "sample asset B\n"
CONTENT_C = "sample asset C\n"


class QueryExcludeTagRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "sample_a.bin"
        self.file_b = self.tmp_dir / "sample_b.bin"
        self.file_c = self.tmp_dir / "sample_c.bin"
        self.file_a.write_text(CONTENT_A, encoding="utf-8")
        self.file_b.write_text(CONTENT_B, encoding="utf-8")
        self.file_c.write_text(CONTENT_C, encoding="utf-8")
        self.db_path = self.tmp_dir / "catalog.sqlite"

        self.expected_a = {
            "path": os.path.realpath(str(self.file_a)),
            "type": "image",
            "tags": ["demo", "ui"],
        }
        self.expected_b = {
            "path": os.path.realpath(str(self.file_b)),
            "type": "image",
            "tags": ["demo", "archived"],
        }
        self.expected_c = {
            "path": os.path.realpath(str(self.file_c)),
            "type": "image",
            "tags": ["ui"],
        }

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args, db_path=None):
        """以全新进程运行 python -m asset_catalog，返回 CompletedProcess。"""
        cmd = [
            sys.executable,
            "-m",
            "asset_catalog",
            "--db",
            str(self.db_path if db_path is None else db_path),
            *args,
        ]
        return subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )

    def register_samples(self):
        """按 A、B、C 顺序登记（add 仅用于准备数据）。

        A：image，标签依次为 demo、ui；
        B：image，标签依次为 demo、archived；
        C：image，只有 ui。
        """
        registrations = [
            (self.file_a, ["demo", "ui"]),
            (self.file_b, ["demo", "archived"]),
            (self.file_c, ["ui"]),
        ]
        for path, tags in registrations:
            args = ["add", str(path), "--type", "image"]
            for tag in tags:
                args += ["--tag", tag]
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")

    def query(self, tags, mode=..., exclude=None, asset_type=None,
              check_files=False, file_status=None):
        """发起 query；mode 为默认哨兵时不传 --tag-mode。"""
        args = ["query"]
        if mode is not ...:
            args += ["--tag-mode", mode]
        for tag in tags:
            args += ["--tag", tag]
        if exclude is not None:
            for tag in exclude:
                args += ["--exclude-tag", tag]
        if asset_type is not None:
            args += ["--type", asset_type]
        if check_files:
            args += ["--check-files"]
        if file_status is not None:
            args += ["--file-status", file_status]
        return self.run_cli(*args)

    def assertQueryOk(self, tags, expected, **kwargs):
        """查询成功：退出码 0、标准错误为空、标准输出为预期 JSON 数组。"""
        result = self.query(tags, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        # 比较解析后的数据，不依赖对象键顺序或 JSON 空白。
        self.assertEqual(data, expected)
        return data

    def assertSampleContentsUnchanged(self):
        self.assertEqual(self.file_a.read_text(encoding="utf-8"), CONTENT_A)
        self.assertEqual(self.file_b.read_text(encoding="utf-8"), CONTENT_B)
        self.assertEqual(self.file_c.read_text(encoding="utf-8"), CONTENT_C)

    def test_acceptance_any_mode_excludes_archived(self):
        self.register_samples()

        # 验收场景一：any 命中 demo/ui 的 A、B、C 中排除带 archived 的 B，
        # 只按 A、C 的首次登记顺序输出，记录仍仅含 path、type、tags。
        data = self.assertQueryOk(
            ["demo", "ui"],
            [self.expected_a, self.expected_c],
            mode="any",
            exclude=["archived"],
        )
        for record in data:
            self.assertEqual(set(record), {"path", "type", "tags"})

    def test_acceptance_all_mode_exclude_overlap_returns_empty(self):
        self.register_samples()

        # 验收场景二：all 命中 demo+ui 的只有 A，排除 ui 后 A 也被排除，
        # 同一标签同时用作命中与排除条件时仍执行排除，不报冲突错误。
        self.assertQueryOk(["demo", "ui"], [], exclude=["ui"])

    def test_exclude_conditions_dedup_order_and_whitespace(self):
        self.register_samples()
        expected = [self.expected_a, self.expected_c]

        # 重复条件只生效一次、条件顺序不影响结果、首尾空白被去除。
        self.assertQueryOk(
            ["demo", "ui"], expected, mode="any",
            exclude=["archived", "archived"],
        )
        self.assertQueryOk(
            ["demo", "ui"], expected, mode="any",
            exclude=[" archived "],
        )
        self.assertQueryOk(
            ["demo", "ui"], expected, mode="any",
            exclude=["absent", "archived"],
        )
        self.assertQueryOk(
            ["demo", "ui"], expected, mode="any",
            exclude=["archived", "absent"],
        )

    def test_exclude_is_case_sensitive_and_exact(self):
        self.register_samples()
        all_three = [self.expected_a, self.expected_b, self.expected_c]

        # Archived（大小写不同）与 archive（子串）都不排除 B。
        self.assertQueryOk(
            ["demo", "ui"], all_three, mode="any", exclude=["Archived"]
        )
        self.assertQueryOk(
            ["demo", "ui"], all_three, mode="any", exclude=["archive"]
        )
        # 不存在的排除标签不影响结果。
        self.assertQueryOk(
            ["demo", "ui"], all_three, mode="any", exclude=["absent"]
        )

    def test_exclude_applies_after_all_and_any_scope(self):
        self.register_samples()

        # all 范围内排除：demo+ui 只命中 A，排除 demo 后为空。
        self.assertQueryOk(["demo", "ui"], [], exclude=["demo"])
        # any 范围内排除多个标签：排除 demo 与 ui 后全部排除。
        self.assertQueryOk(
            ["demo", "ui"], [], mode="any", exclude=["demo", "ui"]
        )
        # any 范围内排除 demo：A、B 都带 demo 被排除，只剩 C。
        self.assertQueryOk(
            ["demo", "ui"],
            [self.expected_c],
            mode="any",
            exclude=["demo"],
        )

    def test_exclude_combined_with_type(self):
        self.register_samples()

        # 与 --type 组合：先在 any+type 范围内确定候选，再排除。
        self.assertQueryOk(
            ["demo", "ui"],
            [self.expected_a, self.expected_c],
            mode="any",
            exclude=["archived"],
            asset_type="image",
        )
        # 类型不匹配时范围为空，排除条件不改变空结果。
        self.assertQueryOk(
            ["demo", "ui"], [], mode="any",
            exclude=["archived"], asset_type="audio",
        )

    def test_excluded_asset_file_status_not_checked(self):
        self.register_samples()

        # 删除被排除素材 B 的源文件：不带状态选项时本就不检查状态。
        self.file_b.unlink()
        self.assertQueryOk(
            ["demo", "ui"],
            [self.expected_a, self.expected_c],
            mode="any",
            exclude=["archived"],
        )

        # --check-files：被排除的 B 不检查状态，剩余记录追加现有状态。
        data = self.assertQueryOk(
            ["demo", "ui"],
            [
                {**self.expected_a, "file_status": "present"},
                {**self.expected_c, "file_status": "present"},
            ],
            mode="any",
            exclude=["archived"],
            check_files=True,
        )
        for record in data:
            self.assertEqual(
                set(record), {"path", "type", "tags", "file_status"}
            )

        # --file-status 筛选作用于排除后的剩余范围。
        self.assertQueryOk(
            ["demo", "ui"],
            [self.expected_a, self.expected_c],
            mode="any",
            exclude=["archived"],
            file_status="present",
        )
        self.assertQueryOk(
            ["demo", "ui"], [], mode="any",
            exclude=["archived"], file_status="missing",
        )

    def test_exclusion_does_not_modify_records_or_files(self):
        self.register_samples()

        self.assertQueryOk(
            ["demo", "ui"],
            [self.expected_a, self.expected_c],
            mode="any",
            exclude=["archived"],
        )

        # 排除只作用于本次查询：不带排除条件的查询与导出结果不变。
        self.assertQueryOk(
            ["demo", "ui"],
            [self.expected_a, self.expected_b, self.expected_c],
            mode="any",
        )
        result = self.run_cli("export")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            [self.expected_a, self.expected_b, self.expected_c],
        )
        self.assertSampleContentsUnchanged()

    def test_blank_exclude_tag_rejected_without_creating_database(self):
        fresh_db = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh_db.exists())

        invalid_invocations = [
            # 缺值：--exclude-tag 后没有值。
            ["query", "--tag", "demo", "--exclude-tag"],
            # 为空。
            ["query", "--tag", "demo", "--exclude-tag", ""],
            # 仅有空白。
            ["query", "--tag", "demo", "--exclude-tag", "   "],
            # 其他条件有效时同样整次拒绝。
            ["query", "--tag-mode", "any", "--tag", "demo", "--tag", "ui",
             "--exclude-tag", "archived", "--exclude-tag", "  "],
        ]
        for args in invalid_invocations:
            with self.subTest(args=args):
                result = self.run_cli(*args, db_path=fresh_db)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("--exclude-tag", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                # 参数错误不创建数据库。
                self.assertFalse(fresh_db.exists())

    def test_blank_exclude_tag_leaves_existing_records_intact(self):
        self.register_samples()

        result = self.query(["demo"], exclude=[" "])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--exclude-tag", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

        # 失败后既有记录不变，正常查询结果不变。
        self.assertQueryOk(
            ["demo"], [self.expected_a, self.expected_b], mode="any"
        )
        self.assertSampleContentsUnchanged()

    def test_exclude_tag_rejected_by_other_subcommands(self):
        self.register_samples()

        rejections = [
            ["add", str(self.tmp_dir / "extra.bin"), "--type", "image",
             "--tag", "demo", "--exclude-tag", "archived"],
            ["export", "--exclude-tag", "archived"],
            ["retag", str(self.file_a), "--tag", "demo",
             "--exclude-tag", "archived"],
            ["retype", str(self.file_a), "--type", "texture",
             "--exclude-tag", "archived"],
        ]
        (self.tmp_dir / "extra.bin").write_text("extra\n", encoding="utf-8")
        for args in rejections:
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("--exclude-tag", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

        # 既有记录不变。
        self.assertQueryOk(
            ["demo", "ui"],
            [self.expected_a, self.expected_b, self.expected_c],
            mode="any",
        )

    def test_exclude_tag_rejected_by_other_subcommands_no_database(self):
        fresh_db = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh_db.exists())

        for args in (["export", "--exclude-tag", "archived"],
                     ["retag", "x.bin", "--tag", "demo",
                      "--exclude-tag", "archived"]):
            with self.subTest(args=args):
                result = self.run_cli(*args, db_path=fresh_db)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(fresh_db.exists())

    def test_without_exclude_tag_behavior_unchanged(self):
        self.register_samples()

        # 不传 --exclude-tag：既有 all/any 行为不变。
        self.assertQueryOk(["demo", "ui"], [self.expected_a])
        self.assertQueryOk(
            ["demo", "ui"],
            [self.expected_a, self.expected_b, self.expected_c],
            mode="any",
        )
        self.assertQueryOk(["archived"], [self.expected_b])


if __name__ == "__main__":
    unittest.main(verbosity=2)
