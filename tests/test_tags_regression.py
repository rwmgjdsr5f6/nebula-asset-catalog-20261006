"""tags 已用标签统计的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

覆盖：
- 每项仅含 tag 与 asset_count，标签不重复，按 Unicode 码点字典序升序，
  数量为正整数；空目录输出 []；
- 统计区分大小写、完整匹配、不改写标签文本；同一素材的同一标签只计一次，
  不同登记路径分别计数；没有素材使用的标签不输出零计数项；
- retag 编辑后的标签在下次统计中生效，新进程读取结论一致；
- 源文件删除或原路径变成目录后素材仍参与统计；tags 不检查文件状态、
  不读取素材内容、不扫描目录，也不改动数据库或素材文件；
- 数据库文件不存在但父目录存在时创建空目录数据库并输出 []，
  父目录缺失时不补建目录；
- 缺少 --db、数据库路径为空、tags 传入素材路径或 --tag、
  --check-files 等不支持的参数、--type 缺值/为空/只有空白、
  数据库路径指向目录、数据库无法打开或
  内容损坏、表结构不兼容时：退出码 2、标准输出为空、标准错误说明原因
  且不含调用栈；被拒绝的损坏或不兼容数据库字节保持不变；
- --type 按类型筛选统计：类型去除首尾空白后与登记类型完整匹配
  （区分大小写），只统计该类型素材的标签；无匹配类型输出 []；
- --prefix 按标签前缀筛选：前缀值去除首尾空白后与保存的标签文本逐字符
  比较，区分大小写、保留中间空白与中文、只匹配标签开头，%、_ 与反斜线均为
  普通字符；不合并共享前缀的不同标签，无匹配输出 []；缺值、为空或只有空白
  时退出码 2 且不创建数据库；其他子命令收到 `--prefix` 同样拒绝；
- 读取失败时不输出部分数组。

每个用例使用独立的临时目录与数据库，只创建/清理自己的样例文件。
"""

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TagsRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "A.bin"
        self.file_b = self.tmp_dir / "B.bin"
        self.file_a.write_text("demo asset A\n", encoding="utf-8")
        self.file_b.write_text("demo asset B\n", encoding="utf-8")
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

    def register_samples(self):
        """按验收顺序登记 A（image: demo, ui）与 B（image: demo, UI）。"""
        result_a = self.run_cli(
            "add",
            str(self.file_a),
            "--type",
            "image",
            "--tag",
            "demo",
            "--tag",
            "ui",
        )
        self.assertEqual(result_a.returncode, 0, result_a.stderr)
        self.assertEqual(result_a.stderr, "")

        result_b = self.run_cli(
            "add",
            str(self.file_b),
            "--type",
            "image",
            "--tag",
            "demo",
            "--tag",
            "UI",
        )
        self.assertEqual(result_b.returncode, 0, result_b.stderr)
        self.assertEqual(result_b.stderr, "")

    def assertTagsOk(self, expected, db=None, *args):
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

    def test_tags_counts_case_sensitive_and_sorted(self):
        self.register_samples()

        data = self.assertTagsOk(
            [
                {"tag": "UI", "asset_count": 1},
                {"tag": "demo", "asset_count": 2},
                {"tag": "ui", "asset_count": 1},
            ]
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

    def test_same_tag_same_asset_counted_once(self):
        # add 时重复传入同一标签只登记一次，统计中同一素材只计一次。
        result = self.run_cli(
            "add",
            str(self.file_a),
            "--type",
            "image",
            "--tag",
            "demo",
            "--tag",
            "demo",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTagsOk([{"tag": "demo", "asset_count": 1}])

    def test_same_content_different_paths_counted_separately(self):
        # 不同登记路径即使文件内容相同仍分别计数。
        file_c = self.tmp_dir / "C.bin"
        file_c.write_text("demo asset A\n", encoding="utf-8")
        for path in (self.file_a, file_c):
            result = self.run_cli(
                "add", str(path), "--type", "image", "--tag", "demo"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTagsOk([{"tag": "demo", "asset_count": 2}])

    def test_retag_changes_reflected_in_next_run(self):
        self.register_samples()

        # 从 A.bin 移除 ui 后，统计只剩 UI 数量 1 与 demo 数量 2。
        result = self.run_cli(
            "retag",
            str(self.file_a),
            "--remove",
            "--tag",
            "ui",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        expected = [
            {"tag": "UI", "asset_count": 1},
            {"tag": "demo", "asset_count": 2},
        ]
        self.assertTagsOk(expected)
        # 新进程再次读取同一数据库，结论一致。
        self.assertTagsOk(expected)

    def test_deleted_or_replaced_source_still_counted(self):
        self.register_samples()
        # 源文件删除后仍参与统计。
        self.file_b.unlink()
        self.assertFalse(self.file_b.exists())
        # 原路径变成目录后仍参与统计。
        self.file_a.unlink()
        self.file_a.mkdir()

        self.assertTagsOk(
            [
                {"tag": "UI", "asset_count": 1},
                {"tag": "demo", "asset_count": 2},
                {"tag": "ui", "asset_count": 1},
            ]
        )

    def test_tags_does_not_change_database_or_files(self):
        self.register_samples()
        db_before = self.db_path.read_bytes()
        a_before = self.file_a.read_bytes()

        self.assertTagsOk(
            [
                {"tag": "UI", "asset_count": 1},
                {"tag": "demo", "asset_count": 2},
                {"tag": "ui", "asset_count": 1},
            ]
        )

        # 数据库内容与素材文件均不变。
        self.assertEqual(self.db_path.read_bytes(), db_before)
        self.assertEqual(self.file_a.read_bytes(), a_before)

    def test_empty_database_outputs_empty_array(self):
        # 尚未初始化：文件不存在但父目录存在，创建空目录数据库并输出 []。
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())
        self.assertTagsOk([], db=fresh)
        self.assertTrue(fresh.exists())
        # 由新进程再次读取同一空库，结论一致。
        self.assertTagsOk([], db=fresh)

    def register_typed_samples(self):
        """按验收顺序登记 A（image: demo, ui）、B（audio: demo, sound）
        与 C（image: demo, UI）。"""
        file_c = self.tmp_dir / "C.bin"
        file_c.write_text("demo asset C\n", encoding="utf-8")
        samples = [
            (self.file_a, "image", ("demo", "ui")),
            (self.file_b, "audio", ("demo", "sound")),
            (file_c, "image", ("demo", "UI")),
        ]
        for path, asset_type, tags in samples:
            cmd = ["add", str(path), "--type", asset_type]
            for tag in tags:
                cmd += ["--tag", tag]
            result = self.run_cli(*cmd)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
        return file_c

    def test_tags_type_filters_by_exact_type(self):
        # 验收场景：A 为 image（demo、ui），B 为 audio（demo、sound），
        # C 为 image（demo、UI）；tags --type image 依次为 UI 计 1、
        # demo 计 2、ui 计 1，audio 的 sound 不参与。
        self.register_typed_samples()
        self.assertTagsOk(
            [
                {"tag": "UI", "asset_count": 1},
                {"tag": "demo", "asset_count": 2},
                {"tag": "ui", "asset_count": 1},
            ],
            None,
            "--type",
            "image",
        )
        # 不带 --type 时保留原有全目录统计。
        self.assertTagsOk(
            [
                {"tag": "UI", "asset_count": 1},
                {"tag": "demo", "asset_count": 3},
                {"tag": "sound", "asset_count": 1},
                {"tag": "ui", "asset_count": 1},
            ]
        )

    def test_tags_type_strips_surrounding_whitespace(self):
        # 类型值去除首尾空白后匹配：" image " 与 image 等价。
        self.register_typed_samples()
        self.assertTagsOk(
            [
                {"tag": "UI", "asset_count": 1},
                {"tag": "demo", "asset_count": 2},
                {"tag": "ui", "asset_count": 1},
            ],
            None,
            "--type",
            " image ",
        )

    def test_tags_type_case_sensitive_and_unmatched_outputs_empty(self):
        self.register_typed_samples()
        # 完整匹配且区分大小写：Image 不匹配 image。
        self.assertTagsOk([], None, "--type", "Image")
        # 无匹配类型成功输出 []，退出码 0、标准错误为空。
        self.assertTagsOk([], None, "--type", "video")

    def test_tags_type_empty_database_outputs_empty_array(self):
        # 空目录搭配 --type：沿用空库创建规则并输出 []。
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())
        self.assertTagsOk([], fresh, "--type", "image")
        self.assertTrue(fresh.exists())

    def test_tags_type_reflects_retype_and_retag(self):
        file_c = self.register_typed_samples()

        # retype 把 B 改为 image 后，sound 进入 image 统计。
        result = self.run_cli("retype", str(self.file_b), "--type", "image")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTagsOk(
            [
                {"tag": "UI", "asset_count": 1},
                {"tag": "demo", "asset_count": 3},
                {"tag": "sound", "asset_count": 1},
                {"tag": "ui", "asset_count": 1},
            ],
            None,
            "--type",
            "image",
        )

        # retag 从 C 移除 UI 后，image 统计中不再出现 UI。
        result = self.run_cli(
            "retag", str(file_c), "--remove", "--tag", "UI"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTagsOk(
            [
                {"tag": "demo", "asset_count": 3},
                {"tag": "sound", "asset_count": 1},
                {"tag": "ui", "asset_count": 1},
            ],
            None,
            "--type",
            "image",
        )

    def test_tags_type_missing_or_blank_value_rejected(self):
        self.register_samples()
        db_before = self.db_path.read_bytes()
        # --type 缺值（argparse 拒绝）。
        self.assertTagsError("--type")
        # --type 为空或只有空白。
        self.assertTagsError("--type", "")
        self.assertTagsError("--type", "   ")
        # 参数错误不改动既有数据库。
        self.assertEqual(self.db_path.read_bytes(), db_before)

    def test_tags_type_blank_value_does_not_create_database(self):
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertTagsError("--type", "  ", db=fresh)
        self.assertFalse(fresh.exists())

    def register_prefix_samples(self):
        """登记验收样例：A（image: ui、ui.button、UI）、B（image:
        ui.button）、C（audio: ui.button、audio）。"""
        file_c = self.tmp_dir / "C.bin"
        file_c.write_text("demo asset C\n", encoding="utf-8")
        samples = [
            (self.file_a, "image", ("ui", "ui.button", "UI")),
            (self.file_b, "image", ("ui.button",)),
            (file_c, "audio", ("ui.button", "audio")),
        ]
        for path, asset_type, tags in samples:
            cmd = ["add", str(path), "--type", asset_type]
            for tag in tags:
                cmd += ["--tag", tag]
            result = self.run_cli(*cmd)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
        return file_c

    def test_tags_prefix_acceptance_counts(self):
        # 验收场景：--prefix ui 依次为 ui 计 1、ui.button 计 3；UI 不入选。
        self.register_prefix_samples()
        self.assertTagsOk(
            [
                {"tag": "ui", "asset_count": 1},
                {"tag": "ui.button", "asset_count": 3},
            ],
            None,
            "--prefix",
            "ui",
        )

    def test_tags_prefix_with_type_filters_scope_first(self):
        # 同用 --type image：先把范围限定为 image 素材，再保留前缀标签：
        # ui 计 1（仅 A）、ui.button 计 2（A、B）；audio 的 C 不参与。
        self.register_prefix_samples()
        self.assertTagsOk(
            [
                {"tag": "ui", "asset_count": 1},
                {"tag": "ui.button", "asset_count": 2},
            ],
            None,
            "--prefix",
            "ui",
            "--type",
            "image",
        )
        # 类型顺序可互换，结论一致。
        self.assertTagsOk(
            [
                {"tag": "ui", "asset_count": 1},
                {"tag": "ui.button", "asset_count": 2},
            ],
            None,
            "--type",
            "image",
            "--prefix",
            "ui",
        )
        # 无素材匹配类型时成功输出 []。
        self.assertTagsOk([], None, "--prefix", "ui", "--type", "video")

    def test_tags_prefix_is_case_sensitive_and_start_only(self):
        self.register_prefix_samples()
        # 区分大小写：大写 UI 只选 UI，不会选到 ui 或 ui.button。
        self.assertTagsOk(
            [{"tag": "UI", "asset_count": 1}], None, "--prefix", "UI"
        )
        # 只匹配标签开头：button 是 ui.button 的中间子串，不入选。
        self.assertTagsOk([], None, "--prefix", "button")
        # 无任何标签以该文本开头时成功输出 []，不输出零计数项。
        self.assertTagsOk([], None, "--prefix", "ui.button.x")

    def test_tags_prefix_treats_special_characters_as_literals(self):
        # %、_、反斜线与句点都是普通字符：只按文本开头逐字符比较，
        # 不做 LIKE 通配，也不匹配中间子串。
        file_c = self.tmp_dir / "C.bin"
        file_c.write_text("demo asset C\n", encoding="utf-8")
        special_tags = ("ui%", "ui_x", r"ui\back", "a.b", "a%b", "a_b")
        cmd = ["add", str(file_c), "--type", "image"]
        for tag in special_tags:
            cmd += ["--tag", tag]
        result = self.run_cli(*cmd)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertTagsOk(
            [{"tag": "ui%", "asset_count": 1}], None, "--prefix", "ui%"
        )
        self.assertTagsOk(
            [{"tag": "ui_x", "asset_count": 1}], None, "--prefix", "ui_"
        )
        self.assertTagsOk(
            [{"tag": r"ui\back", "asset_count": 1}],
            None,
            "--prefix",
            "ui\\",
        )
        # 通配文本不得扩大匹配范围：ui%x 不当作“ui 后任意字符再 x”。
        self.assertTagsOk([], None, "--prefix", "ui%x")
        # 下划线只按字面匹配：ui_ 只选 ui_x，不选 ui% 或 ui\back。
        self.assertTagsOk(
            [{"tag": "ui_x", "asset_count": 1}], None, "--prefix", "ui_"
        )
        self.assertTagsOk(
            [
                {"tag": "a%b", "asset_count": 1},
                {"tag": "a.b", "asset_count": 1},
                {"tag": "a_b", "asset_count": 1},
            ],
            None,
            "--prefix",
            "a",
        )

    def test_tags_prefix_strips_edges_keeps_inner_whitespace_and_chinese(self):
        file_c = self.tmp_dir / "C.bin"
        file_c.write_text("demo asset C\n", encoding="utf-8")
        result = self.run_cli(
            "add",
            str(file_c),
            "--type",
            "image",
            "--tag",
            "ui 中",
            "--tag",
            "中文 标签",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        # 首尾空白被去除后再匹配；中间空白逐字符保留。
        self.assertTagsOk(
            [{"tag": "ui 中", "asset_count": 1}],
            None,
            "--prefix",
            " ui ",
        )
        self.assertTagsOk(
            [{"tag": "ui 中", "asset_count": 1}], None, "--prefix", "ui 中"
        )
        # 中间空白不同则不匹配。
        self.assertTagsOk([], None, "--prefix", "ui中")
        # 中文前缀逐字符比较、只匹配开头。
        self.assertTagsOk(
            [{"tag": "中文 标签", "asset_count": 1}],
            None,
            "--prefix",
            "中文",
        )
        self.assertTagsOk([], None, "--prefix", "标签")

    def test_tags_prefix_deleted_or_directory_source_still_counted(self):
        # 统计不检查登记路径状态：源文件删除或变成目录后前缀统计结果不变。
        self.register_prefix_samples()
        self.file_a.unlink()
        self.file_b.unlink()
        self.file_b.mkdir()
        self.assertTagsOk(
            [
                {"tag": "ui", "asset_count": 1},
                {"tag": "ui.button", "asset_count": 3},
            ],
            None,
            "--prefix",
            "ui",
        )

    def test_tags_prefix_does_not_change_database(self):
        self.register_prefix_samples()
        db_before = self.db_path.read_bytes()
        self.assertTagsOk(
            [
                {"tag": "ui", "asset_count": 1},
                {"tag": "ui.button", "asset_count": 3},
            ],
            None,
            "--prefix",
            "ui",
        )
        # 前缀统计不改动数据库字节。
        self.assertEqual(self.db_path.read_bytes(), db_before)

    def test_tags_prefix_no_match_and_empty_database_output_empty_array(self):
        self.register_prefix_samples()
        # 无匹配标签成功输出 []，退出码 0、标准错误为空。
        self.assertTagsOk([], None, "--prefix", "nope")
        # 父目录存在而数据库不存在：沿用建空库规则，输出 []。
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())
        self.assertTagsOk([], fresh, "--prefix", "ui")
        self.assertTrue(fresh.exists())

    def test_tags_prefix_missing_or_blank_value_rejected(self):
        self.register_prefix_samples()
        db_before = self.db_path.read_bytes()
        # --prefix 缺值（argparse 拒绝）。
        self.assertTagsError("--prefix")
        # --prefix 为空或只有空白。
        self.assertTagsError("--prefix", "")
        self.assertTagsError("--prefix", "   ")
        stderr = self.assertTagsError("--prefix", "\t ")
        # 错误信息指出该选项及原因，且不含调用栈。
        self.assertIn("--prefix", stderr)
        # 参数错误不改动既有数据库。
        self.assertEqual(self.db_path.read_bytes(), db_before)

    def test_tags_prefix_blank_value_does_not_create_database(self):
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertTagsError("--prefix", "  ", db=fresh)
        self.assertFalse(fresh.exists())

    def test_prefix_rejected_for_other_subcommands(self):
        self.register_samples()
        # --prefix 只属于 tags：其他子命令收到时按参数错误拒绝，
        # 退出码 2、标准输出为空、标准错误不含调用栈。
        for args in (
            ("query", "--tag", "demo", "--prefix", "ui"),
            ("export", "--prefix", "ui"),
            ("show", str(self.file_a), "--prefix", "ui"),
            ("add", str(self.file_a), "--type", "image", "--tag", "demo",
             "--prefix", "ui"),
            ("retag", str(self.file_a), "--tag", "demo", "--prefix", "ui"),
            ("retype", str(self.file_a), "--type", "image",
             "--prefix", "ui"),
        ):
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 2, args)
            self.assertEqual(result.stdout, "", args)
            self.assertNotIn("Traceback", result.stderr, args)

    def test_missing_parent_directory_is_not_created(self):
        missing = self.tmp_dir / "no" / "such" / "catalog.sqlite"
        self.assertTagsError(db=missing)
        self.assertFalse((self.tmp_dir / "no").exists())

    def test_unsupported_tags_arguments_rejected(self):
        self.register_samples()
        db_before = self.db_path.read_bytes()
        self.assertTagsError(str(self.file_a))
        self.assertTagsError("--tag", "demo")
        self.assertTagsError("--check-files")
        # 参数错误不改动既有数据库。
        self.assertEqual(self.db_path.read_bytes(), db_before)

    def test_unsupported_arguments_do_not_create_database(self):
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertTagsError("--tag", "demo", db=fresh)
        self.assertFalse(fresh.exists())

    def test_database_path_is_directory_rejected(self):
        target = self.tmp_dir / "a_directory"
        target.mkdir()
        self.assertTagsError(db=target)

    def test_empty_database_path_rejected(self):
        # --db 显式传空字符串：退出码 2、标准输出为空。
        result = self.run_cli("tags", db="")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_db_option_rejected(self):
        cmd = [sys.executable, "-m", "asset_catalog", "tags"]
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

    def test_corrupt_and_incompatible_databases_rejected_and_untouched(self):
        self.register_samples()

        # 非 SQLite 文本文件：拒绝且字节不变。
        non_sqlite = self.tmp_dir / "broken.sqlite"
        non_sqlite.write_text("not a sqlite database", encoding="utf-8")
        before = non_sqlite.read_bytes()
        self.assertTagsError(db=non_sqlite)
        self.assertEqual(non_sqlite.read_bytes(), before)

        # 含其他业务表的 SQLite 文件：拒绝、不补表、不重建，原有记录保留。
        foreign = self.tmp_dir / "foreign.sqlite"
        conn = sqlite3.connect(str(foreign))
        conn.execute("CREATE TABLE business_record(id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO business_record(note) VALUES ('fixed record')")
        conn.commit()
        conn.close()
        before = foreign.read_bytes()
        self.assertTagsError(db=foreign)
        self.assertEqual(foreign.read_bytes(), before)
        conn = sqlite3.connect(str(foreign))
        self.assertEqual(
            conn.execute("SELECT note FROM business_record").fetchall(),
            [("fixed record",)],
        )
        conn.close()

        # asset 缺少 type 列的不兼容结构：同样拒绝。
        bad_schema = self.tmp_dir / "bad_schema.sqlite"
        conn = sqlite3.connect(str(bad_schema))
        conn.execute(
            "CREATE TABLE asset(id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "path TEXT NOT NULL UNIQUE)"
        )
        conn.execute(
            "CREATE TABLE asset_tag(asset_id INTEGER NOT NULL, tag TEXT NOT NULL, "
            "position INTEGER NOT NULL, PRIMARY KEY(asset_id, tag))"
        )
        conn.commit()
        conn.close()
        self.assertTagsError(db=bad_schema)

    def test_malformed_database_produces_no_partial_output(self):
        self.register_samples()
        malformed = self.tmp_dir / "malformed.sqlite"
        malformed.write_bytes(self.db_path.read_bytes())
        size = malformed.stat().st_size
        with malformed.open("wb") as fh:
            fh.truncate(size // 2)

        # 镜像损坏：整次操作失败，不输出任何部分数组。
        self.assertTagsError(db=malformed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
