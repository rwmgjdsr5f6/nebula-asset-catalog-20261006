"""tags --type 按类型统计已用标签的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

覆盖：
- --type 去除首尾空白后与登记类型完整匹配、区分大小写，接受任意非空文本，
  不按扩展名推断；传入 " image " 与 image 等价，Image 不匹配 image；
- 结果每项仅含 tag 与 asset_count，标签保留保存文本，按 Unicode 码点
  字典序升序，计数为指定类型中具有该标签的已登记素材数，只输出正整数项；
- 同一素材的同一标签只计一次，不同登记路径即使内容相同也分别计数；
- 无匹配类型或空目录输出 []；成功退出码为 0、标准错误为空；
- retype 或 retag 成功后的下一次统计反映更新；源文件删除或变成目录仍
  参与计数；统计不检查文件状态、不读取素材内容、不扫描目录，也不改动
  数据库或源文件；
- 不传 --type 时保留原有全目录统计；
- --type 缺值、为空或只有空白时退出码 2、标准输出为空、标准错误指出
  相关参数及原因、不含调用栈，且不创建数据库。

每个用例使用独立的临时目录与数据库，只创建/清理自己的样例文件。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TagsTypeRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "A.bin"
        self.file_b = self.tmp_dir / "B.bin"
        self.file_c = self.tmp_dir / "C.bin"
        for path, text in (
            (self.file_a, "demo asset A\n"),
            (self.file_b, "demo asset B\n"),
            (self.file_c, "demo asset C\n"),
        ):
            path.write_text(text, encoding="utf-8")
        self.db_path = self.tmp_dir / "catalog.sqlite"

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args, db=None):
        """以全新进程运行 python -m asset_catalog，返回 CompletedProcess。"""
        cmd = [
            sys.executable,
            "-m",
            "asset_catalog",
            "--db",
            str(self.db_path if db is None else db),
            *args,
        ]
        return subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )

    def add_asset(self, path, asset_type, *tags):
        args = ["add", str(path), "--type", asset_type]
        for tag in tags:
            args += ["--tag", tag]
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")

    def register_samples(self):
        """登记验收样例：A(image: demo, ui)、B(audio: demo, sound)、
        C(image: demo, UI)。"""
        self.add_asset(self.file_a, "image", "demo", "ui")
        self.add_asset(self.file_b, "audio", "demo", "sound")
        self.add_asset(self.file_c, "image", "demo", "UI")

    def assertTagsOk(self, expected, *args, db=None):
        result = self.run_cli("tags", *args, db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        # 标准输出仅为一行 JSON 数组。
        self.assertEqual(result.stdout.count("\n"), 1)
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        self.assertEqual(data, expected)
        return data

    def assertTagsError(self, *args, db=None):
        result = self.run_cli("tags", *args, db=db)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr.strip(), "")
        self.assertNotIn("Traceback", result.stderr)
        return result.stderr

    def test_type_filter_counts_only_matching_type(self):
        self.register_samples()

        data = self.assertTagsOk(
            [
                {"tag": "UI", "asset_count": 1},
                {"tag": "demo", "asset_count": 2},
                {"tag": "ui", "asset_count": 1},
            ],
            "--type",
            "image",
        )
        # 每项仅含 tag 与 asset_count；标签不重复；数量为正整数；
        # 按标签文本的 Unicode 码点字典序升序（大写先于小写）。
        self.assertEqual(
            [set(item) for item in data],
            [{"tag", "asset_count"}] * 3,
        )
        tags = [item["tag"] for item in data]
        self.assertEqual(len(tags), len(set(tags)))
        self.assertEqual(tags, sorted(tags))
        for item in data:
            self.assertIsInstance(item["asset_count"], int)
            self.assertGreater(item["asset_count"], 0)

        # 其他类型只统计各自的标签。
        self.assertTagsOk(
            [
                {"tag": "demo", "asset_count": 1},
                {"tag": "sound", "asset_count": 1},
            ],
            "--type",
            "audio",
        )

    def test_type_value_stripped_and_case_sensitive(self):
        self.register_samples()

        expected = [
            {"tag": "UI", "asset_count": 1},
            {"tag": "demo", "asset_count": 2},
            {"tag": "ui", "asset_count": 1},
        ]
        # 首尾空白去除后与 image 等价。
        self.assertTagsOk(expected, "--type", " image ")
        self.assertTagsOk(expected, "--type", "\timage\n")
        # 区分大小写：Image 不匹配 image，无该类型素材时输出 []。
        self.assertTagsOk([], "--type", "Image")
        self.assertTagsOk([], "--type", "IMAGE")

    def test_type_accepts_arbitrary_text_without_extension_inference(self):
        # 类型为任意非空文本，不按扩展名推断。
        self.add_asset(self.file_a, "image/png", "demo")
        self.add_asset(self.file_b, " 图像 ", "demo")
        self.assertTagsOk([{"tag": "demo", "asset_count": 1}], "--type", "image/png")
        self.assertTagsOk([{"tag": "demo", "asset_count": 1}], "--type", "图像")
        self.assertTagsOk([], "--type", "png")

    def test_no_matching_type_or_empty_catalog_outputs_empty_array(self):
        self.register_samples()
        # 无匹配类型。
        self.assertTagsOk([], "--type", "video")
        # 空目录：数据库尚未创建，父目录存在时按空库创建规则输出 []。
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())
        self.assertTagsOk([], "--type", "image", db=fresh)
        self.assertTrue(fresh.exists())

    def test_omitted_type_keeps_full_catalog_stats(self):
        self.register_samples()
        self.assertTagsOk(
            [
                {"tag": "UI", "asset_count": 1},
                {"tag": "demo", "asset_count": 3},
                {"tag": "sound", "asset_count": 1},
                {"tag": "ui", "asset_count": 1},
            ]
        )

    def test_same_tag_same_asset_counted_once_per_type(self):
        # add 时重复传入同一标签只登记一次，按类型统计同样只计一次。
        self.add_asset(self.file_a, "image", "demo", "demo")
        self.assertTagsOk([{"tag": "demo", "asset_count": 1}], "--type", "image")

    def test_same_content_different_paths_counted_separately_per_type(self):
        # 不同登记路径即使文件内容相同仍分别计数。
        file_d = self.tmp_dir / "D.bin"
        file_d.write_text("demo asset A\n", encoding="utf-8")
        self.add_asset(self.file_a, "image", "demo")
        self.add_asset(file_d, "image", "demo")
        self.assertTagsOk([{"tag": "demo", "asset_count": 2}], "--type", "image")

    def test_retype_and_retag_reflected_in_next_run(self):
        self.register_samples()

        # retype 把 B 改为 image 后，image 统计立即包含 B 的标签。
        result = self.run_cli("retype", str(self.file_b), "--type", "image")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTagsOk(
            [
                {"tag": "UI", "asset_count": 1},
                {"tag": "demo", "asset_count": 3},
                {"tag": "sound", "asset_count": 1},
                {"tag": "ui", "asset_count": 1},
            ],
            "--type",
            "image",
        )
        self.assertTagsOk([], "--type", "audio")

        # retag 移除 C 的 UI 后，下一次统计不再出现 UI。
        result = self.run_cli(
            "retag", str(self.file_c), "--remove", "--tag", "UI"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTagsOk(
            [
                {"tag": "demo", "asset_count": 3},
                {"tag": "sound", "asset_count": 1},
                {"tag": "ui", "asset_count": 1},
            ],
            "--type",
            "image",
        )

    def test_deleted_or_replaced_source_still_counted_per_type(self):
        self.register_samples()
        # 源文件删除后仍参与统计。
        self.file_c.unlink()
        self.assertFalse(self.file_c.exists())
        # 原路径变成目录后仍参与统计。
        self.file_a.unlink()
        self.file_a.mkdir()

        self.assertTagsOk(
            [
                {"tag": "UI", "asset_count": 1},
                {"tag": "demo", "asset_count": 2},
                {"tag": "ui", "asset_count": 1},
            ],
            "--type",
            "image",
        )

    def test_type_filter_does_not_change_database_or_files(self):
        self.register_samples()
        db_before = self.db_path.read_bytes()
        a_before = self.file_a.read_bytes()

        self.assertTagsOk(
            [
                {"tag": "UI", "asset_count": 1},
                {"tag": "demo", "asset_count": 2},
                {"tag": "ui", "asset_count": 1},
            ],
            "--type",
            "image",
        )

        # 数据库内容与素材文件均不变。
        self.assertEqual(self.db_path.read_bytes(), db_before)
        self.assertEqual(self.file_a.read_bytes(), a_before)

    def test_type_missing_value_rejected(self):
        self.register_samples()
        db_before = self.db_path.read_bytes()
        stderr = self.assertTagsError("--type")
        self.assertIn("--type", stderr)
        # 参数错误不改动既有数据库。
        self.assertEqual(self.db_path.read_bytes(), db_before)

    def test_type_empty_or_blank_rejected(self):
        self.register_samples()
        db_before = self.db_path.read_bytes()
        for value in ("", "   ", "\t\n"):
            stderr = self.assertTagsError("--type", value)
            self.assertIn("--type", stderr)
        self.assertEqual(self.db_path.read_bytes(), db_before)

    def test_invalid_type_argument_does_not_create_database(self):
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertTagsError("--type", "  ", db=fresh)
        self.assertTagsError("--type", db=fresh)
        self.assertFalse(fresh.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
