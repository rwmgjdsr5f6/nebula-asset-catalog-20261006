"""add 拒绝非法类型或标签时不写入元数据的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile / json），
无需第三方依赖。

覆盖（每次失败只引入一种参数问题，待登记文件与其他参数均有效）：
- ``--type`` 未提供、缺少值、空字符串、仅含空白：退出码 2、标准输出为空、
  标准错误指出相应的 ``--type`` 问题且不含调用栈（不固定完整错误文案）；
- ``--tag`` 未提供、缺少值、空字符串、仅含空白：同样被拒绝，标准错误指出
  相应的 ``--tag`` 问题；
- 重复传入标签时，先给有效标签 ``new`` 再给空字符串或仅含空白标签：
  整次登记被拒绝，前面的有效标签不被保留（查询 ``new`` 返回 ``[]``）；
- 使用已登记 A 的数据库对另一文件 B 发起以上每次失败登记后，由**新进程**
  查询 ``demo`` 仍只返回 A 的原路径、类型与完整标签，查询 ``new`` 为 ``[]``，
  A、B 两个样例文件内容保持不变；
- 同样的无效输入指向尚不存在的数据库文件时，失败后该文件仍不存在
  （元数据校验先于数据库打开，不会创建空库）；
- 合法输入对照：A 类型 ``image``、标签 ``demo``；对 B 传入类型 ``" image "``，
  依次传入标签 ``" demo "``、``ui``、``demo``、``Demo``，成功登记并输出
  单个 JSON 对象（规范绝对路径、类型 ``image``、标签 ``["demo","ui","Demo"]``），
  退出码 0、标准错误为空；随后由新进程查询 ``demo``，按登记顺序返回 A、B
  各一次，B 保留完整标签顺序，文件内容不变。

JSON 比较均经 ``json.loads`` 解析后进行，不依赖空白或对象键顺序。
每个用例使用独立的临时目录与数据库，只创建/清理自己的样例文件，
不接触已有素材。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# A、B 使用不同的固定内容，便于确认失败前后样例文件未被改动、记录未被混淆。
SAMPLE_CONTENT_A = b"add metadata regression sample A\n"
SAMPLE_CONTENT_B = b"add metadata regression sample B\n"

# 仅含空白（无换行）的类型/标签取值。
BLANK = "   "


class AddMetadataRegressionTest(unittest.TestCase):
    def setUp(self):
        # 临时目录建在项目根目录下，与既有回归测试一致；
        # 目录、样例与数据库均由本用例自建，tearDown 时整体清理。
        self._tmp = tempfile.TemporaryDirectory(dir=PROJECT_ROOT)
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "sample_a.bin"
        self.file_b = self.tmp_dir / "sample_b.bin"
        self.file_a.write_bytes(SAMPLE_CONTENT_A)
        self.file_b.write_bytes(SAMPLE_CONTENT_B)
        self.db_path = self.tmp_dir / "catalog.sqlite"

        self.canonical_a = os.path.realpath(str(self.file_a))
        self.canonical_b = os.path.realpath(str(self.file_b))

        self.expected_a = {
            "path": self.canonical_a,
            "type": "image",
            "tags": ["demo"],
        }
        self.expected_b = {
            "path": self.canonical_b,
            "type": "image",
            "tags": ["demo", "ui", "Demo"],
        }

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, args, db_path=None):
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

    def invalid_cases(self):
        """全部非法登记调用：(场景说明, 出错参数名, add 子命令参数列表)。

        待登记文件始终是存在的普通文件 B；每次只引入一种参数问题，
        其余参数有效，以免把路径或数据库错误当成元数据校验结果。
        """
        path_b = str(self.file_b)
        return [
            ("--type 未提供", "--type", ["add", path_b, "--tag", "new"]),
            (
                "--type 缺少值",
                "--type",
                ["add", path_b, "--tag", "new", "--type"],
            ),
            (
                "--type 为空字符串",
                "--type",
                ["add", path_b, "--type", "", "--tag", "new"],
            ),
            (
                "--type 仅含空白",
                "--type",
                ["add", path_b, "--type", BLANK, "--tag", "new"],
            ),
            ("--tag 未提供", "--tag", ["add", path_b, "--type", "image"]),
            (
                "--tag 缺少值",
                "--tag",
                ["add", path_b, "--type", "image", "--tag"],
            ),
            (
                "--tag 为空字符串",
                "--tag",
                ["add", path_b, "--type", "image", "--tag", ""],
            ),
            (
                "--tag 仅含空白",
                "--tag",
                ["add", path_b, "--type", "image", "--tag", BLANK],
            ),
            (
                "先给有效标签 new 再给空字符串标签",
                "--tag",
                [
                    "add",
                    path_b,
                    "--type",
                    "image",
                    "--tag",
                    "new",
                    "--tag",
                    "",
                ],
            ),
            (
                "先给有效标签 new 再给仅含空白标签",
                "--tag",
                [
                    "add",
                    path_b,
                    "--type",
                    "image",
                    "--tag",
                    "new",
                    "--tag",
                    BLANK,
                ],
            ),
        ]

    def register_a(self):
        """登记 A：类型 image，标签 demo；退出码 0、标准错误为空。"""
        result = self.run_cli(
            ["add", str(self.file_a), "--type", "image", "--tag", "demo"]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), self.expected_a)

    def query_tag(self, tag):
        """以全新进程按完整标签查询，返回解析后的 JSON 数组。"""
        result = self.run_cli(["query", "--tag", tag])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        return data

    def assert_rejected(self, label, option_name, argv, db_path=None):
        """非法登记被拒绝：退出码 2、标准输出为空、标准错误最后一行指出
        相应的 --type/--tag 问题且不含调用栈。"""
        result = self.run_cli(argv, db_path=db_path)
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
        # argparse 的用法行可能同时列出 --type 与 --tag，故只检查最后一行
        # （真正的错误说明），确认它指出本次出错的那个参数。
        message_lines = result.stderr.strip().splitlines()
        last_line = message_lines[-1] if message_lines else ""
        self.assertIn(
            option_name,
            last_line,
            f"{label}: 标准错误未指出 {option_name} 问题: {result.stderr!r}",
        )
        self.assertNotIn(
            "Traceback",
            result.stderr,
            f"{label}: 标准错误含调用栈: {result.stderr!r}",
        )
        return result

    def assert_sample_files_unchanged(self, label):
        """全部样例文件的内容在失败前后保持一致。"""
        self.assertEqual(
            self.file_a.read_bytes(),
            SAMPLE_CONTENT_A,
            f"{label}: 样例 A 内容被修改",
        )
        self.assertEqual(
            self.file_b.read_bytes(),
            SAMPLE_CONTENT_B,
            f"{label}: 样例 B 内容被修改",
        )

    def test_invalid_type_or_tag_rejected_without_writing_metadata(self):
        self.register_a()

        for label, option_name, argv in self.invalid_cases():
            with self.subTest(场景=label):
                self.assert_rejected(label, option_name, argv)

                # 每次失败后由新进程查询：demo 仍只返回 A 的原记录，
                # new 返回 []（含“先有效后空白”的重复标签场景）。
                self.assertEqual(self.query_tag("demo"), [self.expected_a])
                self.assertEqual(self.query_tag("new"), [])

                # 失败登记不改动任何样例文件。
                self.assert_sample_files_unchanged(label)

    def test_invalid_inputs_never_create_nonexistent_database(self):
        # 每个场景各用一个尚不存在的数据库文件路径；元数据校验先于打开数据库，
        # 失败后这些文件必须仍不存在。
        for index, (label, option_name, argv) in enumerate(self.invalid_cases()):
            with self.subTest(场景=label):
                missing_db = self.tmp_dir / f"missing_{index}.sqlite"
                self.assertFalse(
                    missing_db.exists(), f"{label}: 前置条件有误，数据库已存在"
                )

                self.assert_rejected(
                    label, option_name, argv, db_path=missing_db
                )

                self.assertFalse(
                    missing_db.exists(),
                    f"{label}: 失败登记后创建了本不该存在的数据库文件",
                )
                self.assert_sample_files_unchanged(label)

    def test_control_valid_add_normalizes_type_and_tags(self):
        """合法输入对照：带空白的类型与标签被规范化、重复标签按首次出现去重。"""
        self.register_a()

        # B：类型 " image "，标签依次为 " demo "、ui、demo、Demo。
        result_b = self.run_cli(
            [
                "add",
                str(self.file_b),
                "--type",
                " image ",
                "--tag",
                " demo ",
                "--tag",
                "ui",
                "--tag",
                "demo",
                "--tag",
                "Demo",
            ]
        )
        self.assertEqual(result_b.returncode, 0, result_b.stderr)
        self.assertEqual(result_b.stderr, "")

        # 标准输出为单个 JSON 对象（不依赖空白或键顺序）。
        self.assertEqual(len(result_b.stdout.strip().splitlines()), 1)
        payload = json.loads(result_b.stdout)
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload, self.expected_b)
        self.assertTrue(os.path.isabs(payload["path"]))
        self.assertEqual(payload["path"], self.canonical_b)
        self.assertEqual(payload["type"], "image")
        self.assertEqual(payload["tags"], ["demo", "ui", "Demo"])

        # 新进程查询 demo：按登记顺序返回 A、B 各一次，B 保留完整标签顺序。
        self.assertEqual(
            self.query_tag("demo"), [self.expected_a, self.expected_b]
        )

        # 样例文件内容在全部操作后保持不变。
        self.assert_sample_files_unchanged("合法输入对照")


if __name__ == "__main__":
    unittest.main(verbosity=2)
