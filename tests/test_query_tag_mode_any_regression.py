"""query 任选标签查询（--tag-mode any）的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

固定样例按 A、B、C 顺序登记：A 类型 image，标签依次为 demo、ui；
B 类型 audio，只有 demo；C 类型 image，只有 ui。

覆盖：
- 不传 --tag-mode 与显式 all 时，查询 demo 与 ui 仍为交集语义，只返回 A；
- --tag-mode any 时返回 A、B、C（每个素材只出现一次，保持首次登记顺序），
  记录仍只含 path、type、tags，保留规范绝对路径、登记类型与完整标签顺序；
- any 与 --type image 一起使用时返回 A、C（既要命中任一标签，也要匹配类型）；
- any 下重复条件、条件顺序、给条件增加首尾空白均不改变结果；
- any 仍区分大小写并完整匹配：查询 Demo 只返回 A，查询 de 返回 []；
- 源文件删除后 any 仍可按登记标签入选；--check-files 只为入选记录追加
  现有 file_status（present / missing），不传时不检查文件状态；
- 无匹配或空目录返回 []；
- --tag-mode 缺少值、为空、含首尾空白、大小写不符或不是 all/any 时：
  退出码 2、标准输出为空、标准错误指出该选项且不含调用栈，且不创建数据库、
  不改变已有记录；
- any 下任一 --tag 为空或只有空白时（即使其他标签有效）同样拒绝整次查询，
  错误原因指向 --tag；
- 成功时退出码 0、标准错误为空。

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


class QueryTagModeAnyRegressionTest(unittest.TestCase):
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
            "type": "audio",
            "tags": ["demo"],
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
        B：audio，只有 demo；
        C：image，只有 ui。
        """
        registrations = [
            (self.file_a, "image", ["demo", "ui"]),
            (self.file_b, "audio", ["demo"]),
            (self.file_c, "image", ["ui"]),
        ]
        for path, asset_type, tags in registrations:
            args = ["add", str(path), "--type", asset_type]
            for tag in tags:
                args += ["--tag", tag]
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")

    def query(self, tags, mode=..., asset_type=None, check_files=False):
        """发起 query；mode 为默认哨兵时不传 --tag-mode。"""
        args = ["query"]
        if mode is not ...:
            args += ["--tag-mode", mode]
        for tag in tags:
            args += ["--tag", tag]
        if asset_type is not None:
            args += ["--type", asset_type]
        if check_files:
            args += ["--check-files"]
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

    def test_default_and_explicit_all_keep_intersection(self):
        self.register_samples()

        # 不传 --tag-mode：仍要求同时具备 demo 与 ui，只返回 A。
        self.assertQueryOk(["demo", "ui"], [self.expected_a])
        # 显式 all 与默认一致。
        self.assertQueryOk(["demo", "ui"], [self.expected_a], mode="all")

    def test_any_returns_assets_having_either_tag_once_each(self):
        self.register_samples()

        data = self.assertQueryOk(
            ["demo", "ui"],
            [self.expected_a, self.expected_b, self.expected_c],
            mode="any",
        )
        # 命中多个条件的 A 只返回一次，顺序为首次登记顺序。
        paths = [record["path"] for record in data]
        self.assertEqual(
            paths,
            [
                self.expected_a["path"],
                self.expected_b["path"],
                self.expected_c["path"],
            ],
        )
        for record in data:
            self.assertTrue(os.path.isabs(record["path"]))
            # 不增加匹配方式或内部编号字段。
            self.assertEqual(set(record), {"path", "type", "tags"})
        # 完整标签顺序保持登记时的样子，不被查询条件改变。
        self.assertEqual(data[0]["tags"], ["demo", "ui"])
        self.assertEqual(data[1]["tags"], ["demo"])
        self.assertEqual(data[2]["tags"], ["ui"])

    def test_any_combined_with_type_filter(self):
        self.register_samples()

        # any 与 --type image：既要命中任一标签，也要是 image，返回 A、C。
        self.assertQueryOk(
            ["demo", "ui"],
            [self.expected_a, self.expected_c],
            mode="any",
            asset_type="image",
        )
        # 类型筛选区分大小写、完整匹配；无匹配时输出 []。
        self.assertQueryOk(
            ["demo", "ui"], [], mode="any", asset_type="IMAGE"
        )
        self.assertQueryOk(["demo"], [], mode="any", asset_type="video")

    def test_any_duplicates_order_and_whitespace_equivalent(self):
        self.register_samples()
        expected = [self.expected_a, self.expected_b, self.expected_c]

        # 条件顺序不影响结果。
        self.assertQueryOk(["ui", "demo"], expected, mode="any")
        # 重复条件等同于只传一次，素材不重复出现。
        self.assertQueryOk(["demo", "ui", "demo"], expected, mode="any")
        self.assertQueryOk(["demo", "demo", "ui"], expected, mode="any")
        # 给任一条件增加首尾空白，结果相同。
        self.assertQueryOk([" demo ", "ui"], expected, mode="any")
        self.assertQueryOk(["demo", " ui "], expected, mode="any")

    def test_any_still_case_sensitive_and_exact(self):
        self.register_samples()

        # 区分大小写：登记的标签均为小写 demo，Demo 不命中任何素材。
        self.assertQueryOk(["Demo"], [], mode="any")
        # 同样的小写值则命中 A、B。
        self.assertQueryOk(
            ["demo"], [self.expected_a, self.expected_b], mode="any"
        )
        # 完整匹配，不做子串匹配。
        self.assertQueryOk(["de"], [], mode="any")
        # 未登记标签返回 []；与有效标签混合时只返回命中的素材。
        self.assertQueryOk(["absent"], [], mode="any")
        self.assertQueryOk(
            ["demo", "absent"], [self.expected_a, self.expected_b], mode="any"
        )

    def test_any_deleted_source_file_still_selected_and_check_files(self):
        self.register_samples()

        # 删除源文件 C：不传 --check-files 时仍按登记标签入选，不检查状态。
        self.file_c.unlink()
        data = self.assertQueryOk(
            ["demo", "ui"],
            [self.expected_a, self.expected_b, self.expected_c],
            mode="any",
        )
        self.assertNotIn("file_status", data[2])

        # --check-files 只为最终入选记录追加现有 file_status；
        # C 的登记路径已删除，报告 missing。
        result = self.query(["demo", "ui"], mode="any", check_files=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        with_status = json.loads(result.stdout)
        self.assertEqual([record["file_status"] for record in with_status],
                         ["present", "present", "missing"])
        for record in with_status:
            self.assertEqual(
                set(record), {"path", "type", "tags", "file_status"}
            )

    def test_any_empty_directory_or_no_match_returns_empty_array(self):
        # 数据库不存在但父目录存在：创建空目录数据库并返回 []。
        result = self.run_cli(
            "query", "--tag-mode", "any", "--tag", "demo"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [])
        self.assertTrue(self.db_path.exists())

        # 有数据库但无匹配同样返回 []。
        self.register_samples()
        self.assertQueryOk(["absent"], [], mode="any")

    def test_invalid_tag_mode_rejected_without_creating_database(self):
        fresh_db = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh_db.exists())

        invalid_modes = [
            ["--tag-mode"],                    # 缺少值
            ["--tag-mode", ""],                # 为空
            ["--tag-mode", " any "],           # 含首尾空白
            ["--tag-mode", "ANY"],             # 大小写不符
            ["--tag-mode", "all "],            # 含首尾空白
            ["--tag-mode", "every"],           # 不是 all/any
        ]
        for mode_args in invalid_modes:
            with self.subTest(mode_args=mode_args):
                result = self.run_cli(
                    "query", *mode_args, "--tag", "demo", db_path=fresh_db
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                # 标准错误指出 --tag-mode 的问题，不固定整段错误文字。
                self.assertIn("--tag-mode", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                # 参数错误不创建数据库。
                self.assertFalse(fresh_db.exists())

    def test_invalid_tag_mode_leaves_existing_records_intact(self):
        self.register_samples()

        result = self.query(["demo", "ui"], mode="ALL")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--tag-mode", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

        # 失败后默认方式与 any 查询结果均不变。
        self.assertQueryOk(["demo", "ui"], [self.expected_a])
        self.assertQueryOk(
            ["demo", "ui"],
            [self.expected_a, self.expected_b, self.expected_c],
            mode="any",
        )
        self.assertSampleContentsUnchanged()

    def test_blank_tag_rejected_even_under_any(self):
        self.register_samples()

        invalid_invocations = [
            ["query", "--tag-mode", "any", "--tag", "demo", "--tag", ""],
            ["query", "--tag-mode", "any", "--tag", "demo", "--tag", "   "],
            ["query", "--tag-mode", "any", "--tag", "  ", "--tag", "ui"],
        ]
        for args in invalid_invocations:
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 2)
                # 不返回其他有效标签的结果。
                self.assertEqual(result.stdout, "")
                # 错误原因指向 --tag。
                self.assertIn("--tag", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

                # 每次失败后 any 查询仍得到 A、B、C。
                self.assertQueryOk(
                    ["demo", "ui"],
                    [self.expected_a, self.expected_b, self.expected_c],
                    mode="any",
                )
                self.assertSampleContentsUnchanged()

    def test_tag_mode_rejected_for_other_subcommands(self):
        # add 与 export 不接受 --tag-mode。
        result = self.run_cli(
            "add",
            str(self.file_a),
            "--type", "image",
            "--tag", "demo",
            "--tag-mode", "any",
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

        result = self.run_cli("export", "--tag-mode", "any")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
