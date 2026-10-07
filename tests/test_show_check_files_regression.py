"""show --check-files 查看单条记录并追加当前文件状态的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方
依赖、网络或特殊权限。

固定样例：A.bin 登记为 image，标签顺序为 demo、ui；B.bin 登记为 audio，
标签为 music。A.bin 的源文件在测试中被删除、换成目录再恢复，无需重新
登记。

覆盖：
- 验收：源文件不存在时 ``show A.bin --check-files`` 返回原规范路径、原
  类型、完整标签 ["demo", "ui"] 及 file_status=missing；随后不带选项的
  ``show A.bin`` 返回相同元数据且没有状态字段；原位置换成目录返回
  not_file；恢复普通文件后返回 present；
- file_status 定义与 query 一致：普通文件 present，路径不存在、断开的
  符号链接或中间组件不是目录 missing，目录等非普通文件 not_file，符号
  链接按目标判断；
- 先定位登记记录再检查记录保存路径：未登记路径无论是否存在都以退出码 2
  失败、标准输出为空、标准错误说明素材未登记并包含规范路径；等价路径
  写法与符号链接的定位语义保持现状；
- 权限或其他系统错误导致无法判断命中记录状态时，退出码 2、标准输出
  为空、标准错误说明状态读取失败并指出相关路径，不含调用栈、不输出部分
  JSON；不传 --check-files 时不发生状态检查，相同错误条件下仍成功；
- 只检查命中记录保存路径的状态，不读取素材内容，不改写源文件或数据库，
  不改变标签顺序和登记顺序，其他素材的状态不影响本次查看；
- 其他子命令（add/query/export/retag/retype）与不带选项的 show 行为不变。

每个用例使用独立的临时目录与数据库，只创建/清理自己的样例文件。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 子进程内自动导入的垫片：仅当环境变量给出目标路径时，把 os.stat 替换为
# 对该路径固定抛出 PermissionError 的版本，其余路径照常。查看进程对命中
# 记录的规范保存路径读取状态时即得到稳定的权限错误，不依赖实际权限配置。
SITECUSTOMIZE_SOURCE = '''\
"""测试垫片：对指定路径的 os.stat 固定抛出 PermissionError。"""

import os

_fail_path = os.environ.get("ASSET_CATALOG_TEST_STAT_FAIL_PATH")
if _fail_path:
    _real_stat = os.stat

    def _stat_with_fixed_failure(path, *args, **kwargs):
        if os.fspath(path) == _fail_path:
            raise PermissionError(13, "Permission denied", _fail_path)
        return _real_stat(path, *args, **kwargs)

    os.stat = _stat_with_fixed_failure
'''

STAT_FAIL_ENV = "ASSET_CATALOG_TEST_STAT_FAIL_PATH"


class ShowCheckFilesRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "A.bin"
        self.file_b = self.tmp_dir / "B.bin"
        self.file_a.write_text("demo asset A\n", encoding="utf-8")
        self.file_b.write_text("demo asset B\n", encoding="utf-8")
        self.db_path = self.tmp_dir / "catalog.sqlite"

        # 垫片目录：sitecustomize.py 随 PYTHONPATH 进入子进程，仅在环境
        # 变量点名某规范路径时生效。
        self.shim_dir = self.tmp_dir / "shim"
        self.shim_dir.mkdir()
        (self.shim_dir / "sitecustomize.py").write_text(
            SITECUSTOMIZE_SOURCE, encoding="utf-8"
        )

        self.path_a = os.path.realpath(str(self.file_a))
        self.path_b = os.path.realpath(str(self.file_b))
        self.expected_a = {"path": self.path_a, "type": "image", "tags": ["demo", "ui"]}
        self.expected_b = {"path": self.path_b, "type": "audio", "tags": ["music"]}

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args, db=None, cwd=None, fail_stat_path=None):
        """以全新进程运行 python -m asset_catalog，返回 CompletedProcess。

        fail_stat_path 给定时，该进程内对该规范路径的 os.stat 固定抛出
        PermissionError，其余路径不受影响。
        """
        cmd = [
            sys.executable,
            "-m",
            "asset_catalog",
            "--db",
            str(self.db_path if db is None else db),
            *args,
        ]
        env = os.environ.copy()
        python_path = [str(PROJECT_ROOT), str(self.shim_dir)]
        if cwd is not None:
            python_path.append(env.get("PYTHONPATH", ""))
        env["PYTHONPATH"] = os.pathsep.join(python_path)
        if fail_stat_path is not None:
            env[STAT_FAIL_ENV] = fail_stat_path
        else:
            env.pop(STAT_FAIL_ENV, None)
        return subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT if cwd is None else cwd),
            capture_output=True,
            text=True,
            env=env,
        )

    def register_samples(self):
        """按验收顺序登记 A（image: demo, ui）与 B（audio: music）。"""
        result_a = self.run_cli(
            "add", str(self.file_a), "--type", "image", "--tag", "demo", "--tag", "ui"
        )
        self.assertEqual(result_a.returncode, 0, result_a.stderr)
        self.assertEqual(result_a.stderr, "")

        result_b = self.run_cli(
            "add", str(self.file_b), "--type", "audio", "--tag", "music"
        )
        self.assertEqual(result_b.returncode, 0, result_b.stderr)
        self.assertEqual(result_b.stderr, "")

    def assertCheckedShow(self, expected_record, status, *args, **kwargs):
        """show --check-files 成功：单行 JSON 对象恰含四个键且状态相符。"""
        result = self.run_cli("show", *args, "--check-files", **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1, result.stdout)
        data = json.loads(lines[0])
        self.assertIsInstance(data, dict)
        self.assertEqual(set(data), {"path", "type", "tags", "file_status"})
        self.assertEqual(data["file_status"], status)
        self.assertEqual(
            data, {**expected_record, "file_status": status}
        )
        return data

    def assertPlainShow(self, expected_record, *args, **kwargs):
        """不带 --check-files 的 show：仍只有 path、type、tags。"""
        result = self.run_cli("show", *args, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertEqual(set(data), {"path", "type", "tags"})
        self.assertEqual(data, expected_record)
        return data

    def assertShowFails(self, *args, **kwargs):
        result = self.run_cli("show", *args, **kwargs)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr.strip(), "")
        self.assertNotIn("Traceback", result.stderr)
        return result.stderr

    def assertExport(self, expected):
        result = self.run_cli("export")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected)

    def test_acceptance_missing_then_plain_then_not_file_then_present(self):
        self.register_samples()

        # 固定样例：已登记但源文件已不存在的 A.bin。
        self.file_a.unlink()
        self.assertFalse(self.file_a.exists())

        # show A.bin --check-files：原规范路径、原类型、完整标签及 missing。
        data = self.assertCheckedShow(
            self.expected_a,
            "missing",
            "A.bin",
            db="catalog.sqlite",
            cwd=self.tmp_dir,
        )
        self.assertEqual(data["path"], self.path_a)
        self.assertTrue(os.path.isabs(data["path"]))
        self.assertEqual(data["type"], "image")
        self.assertEqual(data["tags"], ["demo", "ui"])

        # 随后不带选项的 show：相同元数据且没有状态字段。
        self.assertPlainShow(
            self.expected_a, "A.bin", db="catalog.sqlite", cwd=self.tmp_dir
        )

        # 原位置换成目录：带选项的查看返回 not_file，无需重新登记。
        os.mkdir(self.path_a)
        self.assertCheckedShow(
            self.expected_a,
            "not_file",
            "./A.bin",
            db="catalog.sqlite",
            cwd=self.tmp_dir,
        )
        # 不带选项时目录不影响记录返回，也不附带状态字段。
        self.assertPlainShow(
            self.expected_a, "A.bin", db="catalog.sqlite", cwd=self.tmp_dir
        )

        # 恢复普通文件后返回 present。
        os.rmdir(self.path_a)
        self.file_a.write_text("restored asset A\n", encoding="utf-8")
        self.assertCheckedShow(self.expected_a, "present", str(self.file_a))

    def test_present_normal_file_status(self):
        self.register_samples()
        self.assertCheckedShow(self.expected_a, "present", str(self.file_a))
        self.assertCheckedShow(self.expected_b, "present", str(self.file_b))

    def test_symlink_judged_by_its_target(self):
        self.register_samples()
        link = self.tmp_dir / "A_link.bin"
        try:
            os.symlink(self.file_a, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"当前环境不支持创建符号链接: {exc}")

        # 符号链接解析为登记路径：定位 A 的记录，状态按存活目标判为 present。
        self.assertCheckedShow(self.expected_a, "present", str(link))

        # 删除目标后登记路径成为断开的链接：missing。
        self.file_a.unlink()
        self.assertCheckedShow(self.expected_a, "missing", str(link))

        # 目标恢复为目录：按目标判为 not_file。
        os.mkdir(self.path_a)
        self.assertCheckedShow(self.expected_a, "not_file", str(link))

    def test_intermediate_component_not_directory_is_missing(self):
        self.register_samples()
        # 把 A 登记为深层路径下的素材后，把中间组件换成普通文件：
        # 中间组件不是目录按 missing 归类（与 query 的状态定义一致）。
        deep_dir = self.tmp_dir / "deep"
        deep_dir.mkdir()
        deep_a = deep_dir / "A.bin"
        deep_a.write_text("deep asset A\n", encoding="utf-8")
        result = self.run_cli(
            "add", str(deep_a), "--type", "image", "--tag", "demo", "--tag", "ui"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        deep_path = os.path.realpath(str(deep_a))

        import shutil
        shutil.rmtree(deep_dir)
        deep_dir.write_text("now a regular file\n", encoding="utf-8")

        result = self.run_cli("show", deep_path, "--check-files")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertEqual(data["path"], deep_path)
        self.assertEqual(data["file_status"], "missing")

    def test_unregistered_nonexistent_path_rejected_even_with_flag(self):
        self.register_samples()
        canonical_c = os.path.realpath(str(self.tmp_dir / "C.bin"))
        stderr = self.assertShowFails(
            "C.bin",
            "--check-files",
            db="catalog.sqlite",
            cwd=self.tmp_dir,
        )
        self.assertIn("未登记", stderr)
        self.assertIn(canonical_c, stderr)
        # 其他素材与登记顺序不变。
        self.assertExport([self.expected_a, self.expected_b])

    def test_unregistered_existing_path_rejected_without_status_check(self):
        self.register_samples()
        stray = self.tmp_dir / "stray.bin"
        stray.write_text("not registered\n", encoding="utf-8")
        # 未登记路径即使在文件系统上存在（且为普通文件）也按未登记失败，
        # 不返回 present 之类的状态结果。
        stderr = self.assertShowFails(str(stray), "--check-files")
        self.assertIn("未登记", stderr)
        self.assertIn(os.path.realpath(str(stray)), stderr)

    def test_equivalent_path_spellings_locate_same_record(self):
        self.register_samples()
        sub = self.tmp_dir / "sub"
        sub.mkdir()
        spellings = (
            self.path_a,
            str(self.tmp_dir / "." / "A.bin"),
            str(sub / ".." / "A.bin"),
            str(self.tmp_dir / "." / "sub" / ".." / "A.bin"),
        )
        for spelling in spellings:
            self.assertCheckedShow(self.expected_a, "present", spelling)

    def test_status_error_on_hit_record_fails_without_partial_output(self):
        self.register_samples()

        # 命中记录的保存路径状态读取固定 PermissionError：
        # 退出码 2、标准输出为空、标准错误说明状态读取失败并指出路径。
        result = self.run_cli(
            "show", str(self.file_a), "--check-files", fail_stat_path=self.path_a
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("无法确定文件状态", result.stderr)
        self.assertIn(self.path_a, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

        # 不传 --check-files 时不做状态检查：相同错误条件下照常返回记录，
        # 且输出不含 file_status。
        self.assertPlainShow(
            self.expected_a, str(self.file_a), fail_stat_path=self.path_a
        )

        # 错误前后数据库登记内容不变。
        self.assertExport([self.expected_a, self.expected_b])

    def test_status_error_on_unregistered_path_still_reports_unregistered(self):
        self.register_samples()
        missing = self.tmp_dir / "ghost.bin"
        missing_canonical = os.path.realpath(str(missing))
        # 定位先于状态检查：即使该路径的状态读取会失败，未登记仍按未登记
        # 报错，而不是状态读取失败。
        stderr = self.assertShowFails(
            str(missing), "--check-files", fail_stat_path=missing_canonical
        )
        self.assertIn("未登记", stderr)
        self.assertIn(missing_canonical, stderr)

    def test_only_hit_record_path_is_checked(self):
        self.register_samples()
        # B 与本次查看无关：其路径即使状态读取失败也不影响查看 A。
        self.assertCheckedShow(
            self.expected_a, "present", str(self.file_a),
            fail_stat_path=self.path_b,
        )

    def test_readonly_database_and_source_bytes_unchanged(self):
        self.register_samples()
        db_before = self.db_path.read_bytes()
        a_before = self.file_a.read_bytes()

        self.assertCheckedShow(self.expected_a, "present", str(self.file_a))

        self.assertEqual(self.db_path.read_bytes(), db_before)
        self.assertEqual(self.file_a.read_bytes(), a_before)

        # 源文件缺失时同样只读、不重建源文件、不改库。
        self.file_a.unlink()
        self.assertCheckedShow(self.expected_a, "missing", str(self.file_a))
        self.assertFalse(self.file_a.exists())
        self.assertEqual(self.db_path.read_bytes(), db_before)

    def test_tag_order_and_registration_order_preserved(self):
        self.register_samples()
        self.file_a.unlink()
        data = self.assertCheckedShow(self.expected_a, "missing", str(self.file_a))
        self.assertEqual(data["tags"], ["demo", "ui"])
        # 查看不重排登记顺序：导出仍为 A、B。
        self.assertExport([self.expected_a, self.expected_b])

    def test_flag_is_store_true_and_unknown_arguments_still_rejected(self):
        self.register_samples()
        # --check-files 不接受取值：多出的位置参数仍按参数错误拒绝。
        result = self.run_cli("show", str(self.file_a), "--check-files", "present")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)
        # show 仍不接受其他子命令的选项。
        result = self.run_cli("show", str(self.file_a), "--check-files", "--tag", "x")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        # 空路径仍在打开数据库前失败。
        result = self.run_cli("show", "", "--check-files")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_fresh_database_created_then_unregistered_reported(self):
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())
        stderr = self.assertShowFails(str(self.file_a), "--check-files", db=fresh)
        self.assertIn("未登记", stderr)
        self.assertIn(self.path_a, stderr)
        # 空库创建规则保持现状：库已初始化，导出为 []。
        self.assertTrue(fresh.exists())
        result = self.run_cli("export", db=fresh)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

    def test_other_commands_unchanged(self):
        self.register_samples()
        # add/query/export/retag/retype 行为保持原样：query 不带 --check-files
        # 时不附加状态字段，即使 A 的源文件缺失仍按登记标签入选。
        self.file_a.unlink()

        result = self.run_cli("query", "--tag", "demo")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        # B 的标签为 music，不带 demo；只有 A 入选。
        self.assertEqual(data, [self.expected_a])
        for record in data:
            self.assertNotIn("file_status", record)

        # retag/retype 对缺失源文件仍可用，show --check-files 反映当前值。
        result = self.run_cli(
            "retag", str(self.file_a), "--tag", "demo", "--tag", "ui", "--tag", "v2"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli("retype", str(self.file_a), "--type", "texture")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertCheckedShow(
            {"path": self.path_a, "type": "texture", "tags": ["demo", "ui", "v2"]},
            "missing",
            str(self.file_a),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
