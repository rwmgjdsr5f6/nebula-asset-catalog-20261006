"""query 按标签排除素材（--exclude-tag）的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

覆盖：
- 验收样例 A（["demo","ui"]）、B（["demo","archived"]）、C（["ui"]）依次
  登记为 image；--tag-mode any 命中 demo、ui 再排除 archived 时只按 A、C
  顺序返回；同一库 all 命中 demo、ui 再排除 ui 时返回 []；
- 排除标签重复、条件顺序、首尾空白不影响结果；按完整文本区分大小写；
  不存在的排除标签不影响结果；同一标签同时用作命中与排除条件时仍排除；
- 与 --type 同用时先确定标签/类型范围再排除；不传 --exclude-tag 时
  行为与之前一致；
- 排除只作用于本次查询：export 仍返回 A、B、C，样例文件内容不变；
- 与 --check-files / --file-status 同用时，被排除素材不检查文件状态
  （即使其路径已删除也不报错），剩余范围沿用既有状态规则；
- 成功查询退出码 0、标准错误为空、标准输出仅为 JSON 数组，记录只含
  path、type、tags（--check-files 时才追加 file_status）；
- --exclude-tag 缺值、为空或仅有空白时整次退出码 2、标准输出为空、
  标准错误指出该选项且不含调用栈、不创建数据库；add、export、retag、
  retype 收到该选项同样被拒绝。

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

    def register_samples(self):
        """按 A、B、C 顺序登记三个 image 文件。

        A：标签 demo、ui；B：标签 demo、archived；C：标签 ui。
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

    def assertQueryOk(self, args, expected):
        """查询成功：退出码 0、标准错误为空、标准输出为预期 JSON 数组。"""
        result = self.run_cli("query", *args)
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

    def test_acceptance_any_exclude_archived_returns_a_c(self):
        self.register_samples()

        # any 命中 demo、ui 的范围是 A、B、C；排除 archived 后只余 A、C，
        # 按首次登记顺序输出，每条只出现一次。
        data = self.assertQueryOk(
            ["--tag-mode", "any", "--tag", "demo", "--tag", "ui",
             "--exclude-tag", "archived"],
            [self.expected_a, self.expected_c],
        )
        self.assertEqual([r["path"] for r in data], [
            self.expected_a["path"], self.expected_c["path"]
        ])
        # 记录仍只含 path、type、tags；完整标签保持原顺序。
        for record in data:
            self.assertEqual(set(record), {"path", "type", "tags"})
        self.assertEqual(data[0]["tags"], ["demo", "ui"])
        self.assertEqual(data[1]["tags"], ["ui"])

    def test_acceptance_same_tag_hit_and_excluded_returns_empty(self):
        self.register_samples()

        # all 命中 demo、ui 的只有 A；ui 同时用作排除条件，A 仍被排除，
        # 不报冲突错误，返回 []。
        self.assertQueryOk(
            ["--tag", "demo", "--tag", "ui", "--exclude-tag", "ui"],
            [],
        )

    def test_exclude_normalization_duplicates_order_whitespace_case(self):
        self.register_samples()
        expected = [self.expected_a, self.expected_c]

        # 重复条件只生效一次、条件顺序不影响结果、首尾空白被去除。
        self.assertQueryOk(
            ["--tag-mode", "any", "--tag", "demo", "--tag", "ui",
             "--exclude-tag", " archived ", "--exclude-tag", "archived"],
            expected,
        )
        self.assertQueryOk(
            ["--tag-mode", "any", "--tag", "ui", "--tag", "demo",
             "--exclude-tag", "archived", "--exclude-tag", "nope"],
            expected,
        )
        # 区分大小写：ARCHIVED 不匹配 archived，三个素材都保留。
        self.assertQueryOk(
            ["--tag-mode", "any", "--tag", "demo", "--tag", "ui",
             "--exclude-tag", "ARCHIVED"],
            [self.expected_a, self.expected_b, self.expected_c],
        )
        # 不存在的排除标签不影响结果。
        self.assertQueryOk(
            ["--tag-mode", "any", "--tag", "demo", "--tag", "ui",
             "--exclude-tag", "absent"],
            [self.expected_a, self.expected_b, self.expected_c],
        )

    def test_exclude_with_type_and_without_option(self):
        self.register_samples()

        # 与 --type 同用：先命中任一标签并匹配 image，再排除 archived。
        self.assertQueryOk(
            ["--tag-mode", "any", "--tag", "demo", "--tag", "ui",
             "--type", "image", "--exclude-tag", "archived"],
            [self.expected_a, self.expected_c],
        )
        # 类型不匹配时范围为空，输出 []。
        self.assertQueryOk(
            ["--tag-mode", "any", "--tag", "demo", "--tag", "ui",
             "--type", "audio", "--exclude-tag", "archived"],
            [],
        )
        # all 语义下排除：命中 demo+ui 的只有 A，排除 ui 后为空。
        self.assertQueryOk(
            ["--tag", "demo", "--tag", "ui", "--exclude-tag", "ui"],
            [],
        )
        # 不传 --exclude-tag 时保持已有行为（any 返回 A、B、C）。
        self.assertQueryOk(
            ["--tag-mode", "any", "--tag", "demo", "--tag", "ui"],
            [self.expected_a, self.expected_b, self.expected_c],
        )

    def test_exclude_is_query_only_and_does_not_modify_records(self):
        self.register_samples()

        self.assertQueryOk(
            ["--tag-mode", "any", "--tag", "demo", "--tag", "ui",
             "--exclude-tag", "archived"],
            [self.expected_a, self.expected_c],
        )
        # export 不接受排除，仍返回全部三条记录，B 未被删除或修改。
        result = self.run_cli("export")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            [self.expected_a, self.expected_b, self.expected_c],
        )
        self.assertSampleContentsUnchanged()

    def test_excluded_asset_file_status_not_checked(self):
        self.register_samples()
        # B 命中 demo、archived，删除其源文件：排除 archived 后 B 不在
        # 剩余范围内，其 missing 状态不应被检查，查询仍成功。
        self.file_b.unlink()

        result = self.run_cli(
            "query", "--tag-mode", "any", "--tag", "demo", "--tag", "ui",
            "--exclude-tag", "archived", "--check-files",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertEqual(
            [r["path"] for r in data],
            [self.expected_a["path"], self.expected_c["path"]],
        )
        # 剩余候选文件存在，均为 present；记录含且仅含追加的 file_status。
        for record in data:
            self.assertEqual(record["file_status"], "present")
            self.assertEqual(set(record), {"path", "type", "tags", "file_status"})

        # --file-status missing 同样不检查被排除的 B，筛选结果为空。
        result = self.run_cli(
            "query", "--tag-mode", "any", "--tag", "demo", "--tag", "ui",
            "--exclude-tag", "archived", "--file-status", "missing",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

        # 对照：不排除时 B 的 missing 状态正常参与筛选。
        result = self.run_cli(
            "query", "--tag-mode", "any", "--tag", "demo",
            "--file-status", "missing",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        missing = json.loads(result.stdout)
        self.assertEqual([r["path"] for r in missing], [self.expected_b["path"]])
        # 单独使用 --file-status 时不附加 file_status 字段。
        self.assertEqual(set(missing[0]), {"path", "type", "tags"})

    def test_blank_or_missing_exclude_tag_rejected_without_creating_db(self):
        # 登记前数据库尚不存在；参数错误的查询不应创建它。
        self.assertFalse(self.db_path.exists())
        invalid_invocations = [
            ["--tag", "demo", "--exclude-tag", ""],        # 空字符串
            ["--tag", "demo", "--exclude-tag", "   "],     # 仅含空白
            ["--tag", "demo", "--exclude-tag"],            # 缺值
        ]
        for args in invalid_invocations:
            with self.subTest(args=args):
                result = self.run_cli("query", *args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("--exclude-tag", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(
                    self.db_path.exists(), "参数错误不应创建数据库"
                )

        self.register_samples()
        # 即使其他条件有效，任一排除标签为空也整次拒绝，不返回部分结果。
        result = self.run_cli(
            "query", "--tag-mode", "any", "--tag", "demo", "--tag", "ui",
            "--exclude-tag", "archived", "--exclude-tag", " ",
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--exclude-tag", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        # 失败后重新查询，结果不变。
        self.assertQueryOk(
            ["--tag-mode", "any", "--tag", "demo", "--tag", "ui",
             "--exclude-tag", "archived"],
            [self.expected_a, self.expected_c],
        )
        self.assertSampleContentsUnchanged()

    def test_exclude_tag_rejected_by_other_subcommands(self):
        self.register_samples()
        for args in [
            ["add", str(self.file_a), "--type", "image", "--tag", "demo",
             "--exclude-tag", "demo"],
            ["export", "--exclude-tag", "demo"],
            ["retag", str(self.file_a), "--tag", "demo",
             "--exclude-tag", "demo"],
            ["retype", str(self.file_a), "--type", "image",
             "--exclude-tag", "demo"],
        ]:
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("--exclude-tag", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
        # 既有记录全部保持不变。
        result = self.run_cli("export")
        self.assertEqual(
            json.loads(result.stdout),
            [self.expected_a, self.expected_b, self.expected_c],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
