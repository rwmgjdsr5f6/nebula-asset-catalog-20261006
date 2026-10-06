"""add 拒绝非法 --type / --tag 时不写入元数据的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

覆盖（每次失败只引入一种参数问题，其余参数与待登记文件均有效，
避免把路径或数据库错误当成元数据校验结果）：
- ``--type`` 未提供、缺少值、空字符串、仅含空白；
- ``--tag`` 未提供、缺少值、空字符串、仅含空白；
- 重复传入标签时先给有效标签 ``new`` 再给空白标签：整次登记被拒绝，
  前面的有效标签不保留。

对每种失败场景分别验证：
1. 指向已登记 A 的数据库尝试登记另一文件 B：退出码 2、标准输出为空、
   标准错误指出相应的 ``--type`` 或 ``--tag`` 问题且不含调用栈
   （不固定完整错误文案）；失败后由**新进程**查询 ``demo`` 仍只返回 A 的
   原路径、类型与完整标签，查询 ``new`` 返回 ``[]``，即元数据未被写入、
   覆盖或部分保留；
2. 指向尚不存在的数据库文件：同样失败后该文件仍不存在
   （校验发生在建库与写入之前）；
3. 全部样例文件内容在失败前后保持一致。

另含合法输入对照：A 类型 image、标签 demo；B 类型 ``" image "``，
标签依次为 ``" demo "``、ui、demo、Demo。B 成功登记时退出码 0、
标准错误为空，标准输出解析为含规范绝对路径、类型 image 与
标签 ["demo","ui","Demo"] 的单个 JSON 对象；随后由新进程查询 demo，
按登记顺序返回 A、B 各一次，B 保留完整标签顺序。

JSON 一律经 json.loads 解析后比较，不依赖空白或对象键顺序。
每个场景使用独立的数据库；样例文件与数据库均在临时目录中自建，
tearDown 时整体清理，不接触已有素材。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# A、B 使用不同的固定内容，便于确认两个样例均未被读写修改。
SAMPLE_CONTENT_A = "add metadata regression sample A\n"
SAMPLE_CONTENT_B = "add metadata regression sample B\n"

# (场景说明, 标准错误中必须出现的参数名, add 子命令中路径之后的参数)
INVALID_SCENARIOS = [
    ("--type 未提供", "--type", ["--tag", "demo"]),
    ("--type 缺少值", "--type", ["--tag", "demo", "--type"]),
    ("--type 为空字符串", "--type", ["--tag", "demo", "--type", ""]),
    ("--type 仅含空白", "--type", ["--tag", "demo", "--type", "   "]),
    ("--tag 未提供", "--tag", ["--type", "image"]),
    ("--tag 缺少值", "--tag", ["--type", "image", "--tag"]),
    ("--tag 为空字符串", "--tag", ["--type", "image", "--tag", ""]),
    ("--tag 仅含空白", "--tag", ["--type", "image", "--tag", "   "]),
    (
        "先给有效标签 new 再给空白标签，整次登记被拒绝",
        "--tag",
        ["--type", "image", "--tag", "new", "--tag", "  "],
    ),
]


class AddMetadataValidationRegressionTest(unittest.TestCase):
    def setUp(self):
        # 临时目录建在项目根目录下，与既有回归测试一致；
        # 目录、样例与数据库均由本用例自建，tearDown 时整体清理。
        self._tmp = tempfile.TemporaryDirectory(dir=PROJECT_ROOT)
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "sample_a.bin"
        self.file_b = self.tmp_dir / "sample_b.bin"
        self.file_a.write_text(SAMPLE_CONTENT_A, encoding="utf-8")
        self.file_b.write_text(SAMPLE_CONTENT_B, encoding="utf-8")
        self.canonical_a = os.path.realpath(str(self.file_a))
        self.canonical_b = os.path.realpath(str(self.file_b))
        self.expected_a = {
            "path": self.canonical_a,
            "type": "image",
            "tags": ["demo"],
        }

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, db_path, *args):
        """以全新进程运行 python -m asset_catalog，返回 CompletedProcess。"""
        cmd = [
            sys.executable,
            "-m",
            "asset_catalog",
            "--db",
            str(db_path),
            *args,
        ]
        return subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )

    def register_a(self, db_path):
        """在指定数据库中登记 A：类型 image、标签 demo，必须成功。"""
        result = self.run_cli(
            db_path,
            "add",
            str(self.file_a),
            "--type",
            "image",
            "--tag",
            "demo",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), self.expected_a)
        return result

    def assert_rejected(self, result, option, label):
        """非法元数据登记被拒绝：退出码 2、标准输出为空、
        标准错误指出相应参数问题且不含调用栈（不固定完整文案）。"""
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
            option,
            result.stderr,
            f"{label}: 标准错误未指出 {option} 问题: {result.stderr!r}",
        )
        self.assertNotIn(
            "Traceback",
            result.stderr,
            f"{label}: 标准错误含调用栈: {result.stderr!r}",
        )

    def assert_sample_contents_unchanged(self, label):
        self.assertEqual(
            self.file_a.read_text(encoding="utf-8"),
            SAMPLE_CONTENT_A,
            f"{label}: 样例 A 内容被修改",
        )
        self.assertEqual(
            self.file_b.read_text(encoding="utf-8"),
            SAMPLE_CONTENT_B,
            f"{label}: 样例 B 内容被修改",
        )

    def assert_query_tag(self, db_path, tag, expected, label):
        """由新进程查询标签，退出码 0、标准错误为空，结果等于 expected。"""
        result = self.run_cli(db_path, "query", "--tag", tag)
        self.assertEqual(
            result.returncode,
            0,
            f"{label}: 查询 {tag} 失败: {result.stderr!r}",
        )
        self.assertEqual(result.stderr, "", f"{label}: 查询 {tag} 标准错误非空")
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        self.assertEqual(data, expected, f"{label}: 查询 {tag} 结果不符")

    def test_invalid_type_or_tag_is_rejected_and_writes_no_metadata(self):
        for index, (label, option, extra_args) in enumerate(INVALID_SCENARIOS):
            with self.subTest(场景=label):
                # 每个场景使用各自独立的数据库，互不影响。
                db_existing = self.tmp_dir / f"existing_{index}.sqlite"
                db_missing = self.tmp_dir / f"missing_{index}.sqlite"
                self.register_a(db_existing)
                self.assertFalse(
                    db_missing.exists(), f"{label}: 前置条件错误，数据库文件已存在"
                )

                # 1) 对已登记 A 的数据库，用另一存在的普通文件 B 做一次非法登记。
                result_existing = self.run_cli(
                    db_existing, "add", str(self.file_b), *extra_args
                )
                self.assert_rejected(result_existing, option, label)

                # 失败后由新进程查询：demo 仍只返回 A 的原记录；
                # new 返回 []（即先给的有效标签 new 也未被部分保留）。
                self.assert_query_tag(
                    db_existing, "demo", [self.expected_a], label
                )
                self.assert_query_tag(db_existing, "new", [], label)
                self.assert_sample_contents_unchanged(label)

                # 2) 同样的非法输入指向尚不存在的数据库文件：
                # 校验先于建库，失败后文件仍不存在。
                result_missing = self.run_cli(
                    db_missing, "add", str(self.file_b), *extra_args
                )
                self.assert_rejected(result_missing, option, label)
                self.assertFalse(
                    db_missing.exists(),
                    f"{label}: 非法登记失败后却创建了数据库文件",
                )
                self.assert_sample_contents_unchanged(label)

    def test_valid_add_control_succeeds_and_normalizes_metadata(self):
        """合法输入对照：证明测试不会把正常登记误判为拒绝。"""
        db_path = self.tmp_dir / "control.sqlite"

        # A：类型 image，标签 demo。
        result_a = self.register_a(db_path)
        self.assertTrue(
            os.path.isabs(json.loads(result_a.stdout)["path"]),
            "A 的输出路径应为绝对路径",
        )

        # B：类型与首个标签带首尾空白，且 demo 重复、Demo 大小写不同。
        result_b = self.run_cli(
            db_path,
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
        )
        self.assertEqual(result_b.returncode, 0, result_b.stderr)
        self.assertEqual(result_b.stderr, "")

        # 标准输出为单个 JSON 对象（单行），比较不依赖空白或键顺序。
        self.assertEqual(len(result_b.stdout.strip().splitlines()), 1)
        payload_b = json.loads(result_b.stdout)
        self.assertIsInstance(payload_b, dict)
        expected_b = {
            "path": self.canonical_b,
            "type": "image",
            "tags": ["demo", "ui", "Demo"],
        }
        self.assertEqual(payload_b, expected_b)
        self.assertTrue(os.path.isabs(payload_b["path"]))
        self.assertEqual(payload_b["path"], self.canonical_b)
        self.assertEqual(payload_b["type"], "image")
        # 去首尾空白、按首次出现去重、大小写保留、登记顺序保留。
        self.assertEqual(payload_b["tags"], ["demo", "ui", "Demo"])

        # 由新进程查询 demo：按登记顺序返回 A、B 各一次，
        # B 保留完整标签顺序。
        self.assert_query_tag(
            db_path,
            "demo",
            [self.expected_a, expected_b],
            "合法对照查询 demo",
        )

        # 样例内容在成功登记前后保持一致。
        self.assert_sample_contents_unchanged("合法对照")


if __name__ == "__main__":
    unittest.main(verbosity=2)
