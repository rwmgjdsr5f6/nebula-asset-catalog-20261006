"""query --tag-mode 多标签命中方式（all 交集 / any 并集）的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

固定样例按 A、B、C 顺序登记：
- A：image，标签依次为 demo、ui；
- B：audio，只有 demo；
- C：image，只有 ui。

覆盖：
- 不传 --tag-mode 与显式传 all：查询 demo、ui 时只返回同时具备两标签的 A；
- --tag-mode any：查询 demo、ui 时按登记顺序返回 A、B、C，各一次，
  命中两个条件的 A 不重复，记录保留规范绝对路径、登记类型与完整标签顺序，
  不出现命中方式或内部编号字段；
- any 与 --type image 同用：返回 A、C（既要命中任一标签也要匹配类型）；
- any 下重复条件、交换条件顺序、给条件加首尾空白结果不变；单个标签时
  any 与默认方式结果一致；未登记标签与有效标签混用时仍按任一命中；
- 大小写区分、完整匹配；无匹配与空目录均返回 []；
- 成功查询退出码 0、标准错误为空、标准输出仅为 JSON 数组；
- 源文件删除后 any 仍按登记标签入选；不传 --check-files 时不检查、不带
  file_status；传 --check-files 时只为最终入选记录追加 file_status，
  未入选素材（含其源文件已缺失）不影响查询；
- --tag-mode 缺少值、为空、含首尾空白、大小写不符或不是 all、any 时：
  退出码 2、标准输出为空、标准错误指出该选项问题且不含调用栈，
  数据库文件不存在时不因此被创建；
- any 下任一 --tag 为空或仅含空白时同样整次拒绝（错误指向 --tag），
  失败后原有记录与样例文件不变。

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
CONTENT_D = "sample asset D\n"


class QueryTagModeRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "sample_a.bin"
        self.file_b = self.tmp_dir / "sample_b.bin"
        self.file_c = self.tmp_dir / "sample_c.bin"
        self.file_d = self.tmp_dir / "sample_d.bin"
        self.file_a.write_text(CONTENT_A, encoding="utf-8")
        self.file_b.write_text(CONTENT_B, encoding="utf-8")
        self.file_c.write_text(CONTENT_C, encoding="utf-8")
        self.file_d.write_text(CONTENT_D, encoding="utf-8")
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
        self.expected_d = {
            "path": os.path.realpath(str(self.file_d)),
            "type": "video",
            "tags": ["other"],
        }

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

    def register_samples(self, include_d=False):
        """按 A、B、C（可选 D）顺序登记（add 仅用于准备数据）。"""
        registrations = [
            (self.file_a, "image", ["demo", "ui"]),
            (self.file_b, "audio", ["demo"]),
            (self.file_c, "image", ["ui"]),
        ]
        if include_d:
            registrations.append((self.file_d, "video", ["other"]))
        for path, asset_type, tags in registrations:
            cmd = ["add", str(path), "--type", asset_type]
            for tag in tags:
                cmd += ["--tag", tag]
            result = self.run_cli(*cmd)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")

    def query_ok(self, tags, expected, mode=None, asset_type=None,
                 check_files=False):
        """查询成功：退出码 0、标准错误为空、标准输出为预期 JSON 数组。"""
        args = ["query"]
        for tag in tags:
            args += ["--tag", tag]
        if mode is not None:
            args += ["--tag-mode", mode]
        if asset_type is not None:
            args += ["--type", asset_type]
        if check_files:
            args += ["--check-files"]
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        # 比较解析后的数据，不依赖对象键顺序或 JSON 空白。
        self.assertEqual(data, expected)
        return data

    def assertSampleContentsUnchanged(self, *files_and_contents):
        for path, content in files_and_contents:
            self.assertEqual(Path(path).read_text(encoding="utf-8"), content)

    # ---- 默认 / all：交集语义 ----

    def test_default_and_all_require_every_tag(self):
        self.register_samples()

        # 固定验收样例：demo + ui 默认只返回 A。
        self.query_ok(["demo", "ui"], [self.expected_a])
        # 显式 all 与默认一致。
        self.query_ok(["demo", "ui"], [self.expected_a], mode="all")

    def test_any_returns_assets_matching_either_tag(self):
        self.register_samples()

        # 固定验收样例：any 返回 A、B、C，保持登记顺序。
        data = self.query_ok(
            ["demo", "ui"],
            [self.expected_a, self.expected_b, self.expected_c],
            mode="any",
        )
        self.assertEqual(
            [record["path"] for record in data],
            [
                self.expected_a["path"],
                self.expected_b["path"],
                self.expected_c["path"],
            ],
        )
        # 命中两个条件的 A 只出现一次。
        self.assertEqual(
            [record["path"] for record in data].count(self.expected_a["path"]),
            1,
        )
        # 不暴露命中方式或内部编号字段，完整标签顺序保留。
        for record in data:
            self.assertEqual(set(record), {"path", "type", "tags"})
            self.assertTrue(os.path.isabs(record["path"]))
        self.assertEqual(data[0]["type"], "image")
        self.assertEqual(data[0]["tags"], ["demo", "ui"])
        self.assertEqual(data[1]["type"], "audio")
        self.assertEqual(data[1]["tags"], ["demo"])
        self.assertEqual(data[2]["type"], "image")
        self.assertEqual(data[2]["tags"], ["ui"])

    def test_any_with_type_returns_a_and_c(self):
        self.register_samples()

        # 固定验收样例：any + --type image 返回 A、C。
        self.query_ok(
            ["demo", "ui"],
            [self.expected_a, self.expected_c],
            mode="any",
            asset_type="image",
        )
        # 类型去除首尾空白后匹配，结论一致。
        self.query_ok(
            ["demo", "ui"],
            [self.expected_a, self.expected_c],
            mode="any",
            asset_type=" image ",
        )
        # any + audio：只有 B。
        self.query_ok(
            ["demo", "ui"], [self.expected_b], mode="any", asset_type="audio"
        )
        # 无匹配类型时输出 []。
        self.query_ok(
            ["demo", "ui"], [], mode="any", asset_type="model"
        )

    def test_any_duplicates_order_and_whitespace_equivalent(self):
        self.register_samples()
        expected = [self.expected_a, self.expected_b, self.expected_c]

        # 交换条件顺序。
        self.query_ok(["ui", "demo"], expected, mode="any")
        # 重复条件：A 命中多次也只返回一次。
        self.query_ok(["demo", "ui", "demo"], expected, mode="any")
        self.query_ok(["demo", "demo", "ui"], expected, mode="any")
        # 条件含首尾空白，逐个去空白后结论一致。
        self.query_ok([" demo ", "ui"], expected, mode="any")
        self.query_ok(["demo", " ui "], expected, mode="any")

    def test_any_single_tag_and_mixed_absent_tag(self):
        self.register_samples()

        # 单个标签时 any 与默认方式结果一致。
        self.query_ok(
            ["demo"],
            [self.expected_a, self.expected_b],
            mode="any",
        )
        self.query_ok(
            ["demo"],
            [self.expected_a, self.expected_b],
        )
        # 有效标签与未登记标签混用：仍按任一命中返回 A、B。
        self.query_ok(
            ["demo", "absent"],
            [self.expected_a, self.expected_b],
            mode="any",
        )
        # 全部未登记：[]。
        self.query_ok(["absent", "missing"], [], mode="any")

    def test_any_case_sensitive_and_exact_match(self):
        self.register_samples()

        # 大小写区分：样例中没有 Demo 标签，any 也不命中。
        self.query_ok(["Demo", "UI"], [], mode="any")
        # 完整匹配：de、u 不是任何登记标签的完整值。
        self.query_ok(["de", "u"], [], mode="any")
        # Demo 不存在，但 ui 存在：返回 A、C。
        self.query_ok(
            ["Demo", "ui"],
            [self.expected_a, self.expected_c],
            mode="any",
        )

    def test_any_after_source_file_deleted(self):
        self.register_samples()
        # 删除 B 的源文件：any 仍按登记标签入选 B，且不带 file_status。
        self.file_b.unlink()
        data = self.query_ok(
            ["demo", "ui"],
            [self.expected_a, self.expected_b, self.expected_c],
            mode="any",
        )
        for record in data:
            self.assertNotIn("file_status", record)

    def test_any_check_files_only_for_selected_records(self):
        # D 带 other 标签，不会被 demo/ui 的 any 查询选中；
        # 删除 D 的源文件后查询仍成功，证明未入选素材不参与状态检查。
        self.register_samples(include_d=True)
        self.file_d.unlink()
        self.file_b.unlink()

        data = self.query_ok(
            ["demo", "ui"],
            [
                {**self.expected_a, "file_status": "present"},
                {**self.expected_b, "file_status": "missing"},
                {**self.expected_c, "file_status": "present"},
            ],
            mode="any",
            check_files=True,
        )
        paths = [record["path"] for record in data]
        self.assertNotIn(self.expected_d["path"], paths)

    def test_any_empty_catalog_and_no_match_return_empty_array(self):
        # 空目录：查询自动建库并返回 []。
        self.query_ok(["demo", "ui"], [], mode="any")
        self.register_samples()
        # 有库但无匹配：[]。
        self.query_ok(["absent"], [], mode="any")

    # ---- 参数错误 ----

    def test_invalid_tag_mode_rejected_without_creating_database(self):
        # 数据库尚不存在，各种非法 --tag-mode 都不得创建它。
        invalid_modes = [
            [],                       # 缺少值
            [""],                     # 空字符串
            ["  "],                   # 仅含空白
            [" all"],                 # 含前导空白
            ["any "],                 # 含尾随空白
            ["ALL"],                  # 大写
            ["Any"],                  # 大小写不符
            ["union"],                # 不支持的取值
        ]
        for mode_args in invalid_modes:
            with self.subTest(mode_args=mode_args):
                self.assertFalse(self.db_path.exists())
                cmd = ["query", "--tag", "demo", "--tag-mode", *mode_args]
                result = self.run_cli(*cmd)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                # 标准错误指向 --tag-mode，且不含调用栈。
                self.assertIn("--tag-mode", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                # 非法参数错误不创建数据库文件。
                self.assertFalse(
                    self.db_path.exists(),
                    f"非法 tag-mode {mode_args!r} 不应创建数据库",
                )

    def test_invalid_tag_mode_after_registration_leaves_state_intact(self):
        self.register_samples()

        for mode_args in [["ALL"], [" any "], [""]]:
            with self.subTest(mode_args=mode_args):
                result = self.run_cli(
                    "query", "--tag", "demo", "--tag-mode", *mode_args
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("--tag-mode", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

                # 失败后默认方式查询仍只返回 A；any 查询仍返回 A、B、C。
                self.query_ok(["demo", "ui"], [self.expected_a])
                self.query_ok(
                    ["demo", "ui"],
                    [self.expected_a, self.expected_b, self.expected_c],
                    mode="any",
                )

    def test_blank_tag_rejected_even_under_any_mode(self):
        self.register_samples()

        invalid_invocations = [
            ("query", "--tag", "demo", "--tag", "", "--tag-mode", "any"),
            ("query", "--tag", "demo", "--tag", "   ", "--tag-mode", "any"),
            ("query", "--tag-mode", "any", "--tag", "demo", "--tag", ""),
        ]
        for args in invalid_invocations:
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 2)
                # 即使其他标签有效，也不返回部分结果。
                self.assertEqual(result.stdout, "")
                # 错误原因指向 --tag。
                self.assertIn("--tag", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

                # 每次失败后 any 查询仍得到原来的 A、B、C。
                self.query_ok(
                    ["demo", "ui"],
                    [self.expected_a, self.expected_b, self.expected_c],
                    mode="any",
                )
                self.assertSampleContentsUnchanged(
                    (self.file_a, CONTENT_A),
                    (self.file_b, CONTENT_B),
                    (self.file_c, CONTENT_C),
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
