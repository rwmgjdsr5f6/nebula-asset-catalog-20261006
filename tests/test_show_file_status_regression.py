"""show --check-files 单条记录文件状态查看的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方依赖。

覆盖：
- 固定样例：已登记但源文件已不存在的 A.bin（类型 image，标签顺序 demo、ui），
  在演示目录执行 ``show A.bin --check-files`` 返回原规范路径、原类型、完整
  标签及 file_status=missing，退出码 0、标准错误为空、标准输出仅一行 JSON；
  随后执行 ``show A.bin`` 返回相同元数据且没有状态字段；原位置换成目录后
  带选项查看返回 not_file，恢复普通文件后返回 present，无需重新登记；
- 普通文件为 present，输出只含 path、type、tags、file_status 四个键，
  --check-files 放在路径前后均可；
- 路径不存在、断开的符号链接（定位语义不变：按链接目标的规范路径报未登记）、
  中间组件不是目录均为 missing；目录等非普通文件为 not_file；
- 符号链接按目标判断：登记路径处替换为指向另一条已登记普通文件的链接时，
  按其解析后的规范路径定位目标记录并报告 present；
- 未登记路径无论文件是否存在，--check-files 都以退出码 2 失败、标准输出
  为空、标准错误说明素材未登记并包含规范路径（不检查、不保存状态）；
- 状态读取出现 PermissionError 时（子进程内确定性注入，不依赖系统权限配置）：
  退出码 2、标准输出为空、标准错误指出状态读取失败及相关路径且不含调用栈，
  不输出部分 JSON；失败后不带选项的 show 元数据不变；
- 其他素材的文件状态不影响本次查看；查看不读取素材内容、不改写源文件或
  数据库字节，不改变标签顺序与登记顺序；
- 空库创建与父目录缺失等既有口径不变：参数合法、父目录存在时先创建空库再
  报告未登记。

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

# 在子进程中对指定路径的 os.stat 注入 PermissionError，再经
# python -m asset_catalog 的 __main__ 入口执行命令，
# 从而确定性地复现“状态读取权限不足”，不依赖系统权限配置。
INJECT_PERMISSION_ERROR_RUNNER = r"""
import os
import runpy
import sys

target = sys.argv[1]
sys.argv = ["asset_catalog"] + sys.argv[2:]

_real_stat = os.stat


def _patched_stat(path, *args, **kwargs):
    if os.fspath(path) == target:
        raise PermissionError(13, "Permission denied", os.fspath(path))
    return _real_stat(path, *args, **kwargs)


os.stat = _patched_stat
runpy.run_module("asset_catalog", run_name="__main__")
"""


class ShowFileStatusRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "A.bin"
        self.file_b = self.tmp_dir / "B.bin"
        self.file_a.write_text("demo asset A\n", encoding="utf-8")
        self.file_b.write_text("demo asset B\n", encoding="utf-8")
        self.db_path = self.tmp_dir / "catalog.sqlite"

        self.path_a = os.path.realpath(str(self.file_a))
        self.path_b = os.path.realpath(str(self.file_b))
        self.expected_a = {
            "path": self.path_a,
            "type": "image",
            "tags": ["demo", "ui"],
        }
        self.expected_b = {
            "path": self.path_b,
            "type": "audio",
            "tags": ["music"],
        }

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args, db=None, cwd=None):
        """以全新进程运行 python -m asset_catalog，返回 CompletedProcess。

        cwd 默认为项目根目录；在临时演示目录中运行验收命令时通过
        PYTHONPATH 保证仍能导入本包。
        """
        cmd = [
            sys.executable,
            "-m",
            "asset_catalog",
            "--db",
            str(self.db_path if db is None else db),
            *args,
        ]
        env = None
        if cwd is not None:
            env = dict(os.environ)
            env["PYTHONPATH"] = (
                str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
            )
        return subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT if cwd is None else cwd),
            capture_output=True,
            text=True,
            env=env,
        )

    def run_cli_with_permission_error(self, target_path, *args):
        """以全新进程运行同一入口，但对 target_path 的状态读取注入 PermissionError。"""
        cmd = [
            sys.executable,
            "-c",
            INJECT_PERMISSION_ERROR_RUNNER,
            target_path,
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

    def assertShowWithStatus(self, expected_record, status, *args, db=None, cwd=None):
        """带 --check-files 命中：退出码 0、标准错误为空、一行四键 JSON。"""
        result = self.run_cli("show", *args, db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1, result.stdout)
        data = json.loads(lines[0])
        self.assertIsInstance(data, dict)
        self.assertEqual(set(data), {"path", "type", "tags", "file_status"})
        self.assertEqual(
            data, {**expected_record, "file_status": status}
        )
        return data

    def assertPlainShow(self, expected_record, *args, db=None, cwd=None):
        """不带选项查看：一行三键 JSON，不含 file_status。"""
        result = self.run_cli("show", *args, db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1, result.stdout)
        data = json.loads(lines[0])
        self.assertEqual(set(data), {"path", "type", "tags"})
        self.assertEqual(data, expected_record)
        return data

    def assertShowError(self, *args, db=None, cwd=None):
        result = self.run_cli("show", *args, db=db, cwd=cwd)
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

        # 验收命令在演示目录内执行：
        # python -m asset_catalog --db catalog.sqlite show A.bin --check-files
        data = self.assertShowWithStatus(
            self.expected_a,
            "missing",
            "A.bin",
            "--check-files",
            db="catalog.sqlite",
            cwd=self.tmp_dir,
        )
        self.assertEqual(data["path"], self.path_a)
        self.assertEqual(data["type"], "image")
        self.assertEqual(data["tags"], ["demo", "ui"])

        # 随后不传选项：相同元数据，没有状态字段。
        self.assertPlainShow(
            self.expected_a, "A.bin", db="catalog.sqlite", cwd=self.tmp_dir
        )

        # 原位置换成目录：带选项查看返回 not_file；不带选项仍返回登记记录。
        os.mkdir(self.path_a)
        self.assertShowWithStatus(
            self.expected_a,
            "not_file",
            "A.bin",
            "--check-files",
            db="catalog.sqlite",
            cwd=self.tmp_dir,
        )
        self.assertPlainShow(
            self.expected_a, "A.bin", db="catalog.sqlite", cwd=self.tmp_dir
        )

        # 恢复普通文件后返回 present，无需重新登记。
        os.rmdir(self.path_a)
        self.file_a.write_text("demo asset A\n", encoding="utf-8")
        self.assertShowWithStatus(
            self.expected_a,
            "present",
            "A.bin",
            "--check-files",
            db="catalog.sqlite",
            cwd=self.tmp_dir,
        )

        # 全程不改变登记内容：A、B 的路径、类型、标签与登记顺序均不变。
        self.assertExport([self.expected_a, self.expected_b])

    def test_present_for_existing_regular_file(self):
        self.register_samples()
        data = self.assertShowWithStatus(
            self.expected_a, "present", str(self.file_a), "--check-files"
        )
        self.assertTrue(os.path.isabs(data["path"]))

    def test_flag_before_path_works_like_flag_after(self):
        self.register_samples()
        self.assertShowWithStatus(
            self.expected_a, "present", "--check-files", str(self.file_a)
        )
        self.assertShowWithStatus(
            self.expected_a, "present", str(self.file_a), "--check-files"
        )

    def test_repeated_check_files_reflects_current_state(self):
        self.register_samples()
        self.assertShowWithStatus(
            self.expected_a, "present", str(self.file_a), "--check-files"
        )
        self.file_a.unlink()
        # 每次查看都是全新进程并即时读取状态，不保存历史状态。
        self.assertShowWithStatus(
            self.expected_a, "missing", str(self.file_a), "--check-files"
        )
        self.assertShowWithStatus(
            self.expected_a, "missing", str(self.file_a), "--check-files"
        )

    def test_intermediate_component_replaced_with_file_reports_missing(self):
        nested = self.tmp_dir / "nested"
        nested.mkdir()
        nested_file = nested / "a.bin"
        nested_file.write_text("nested asset\n", encoding="utf-8")

        result = self.run_cli(
            "add", str(nested_file), "--type", "image",
            "--tag", "demo", "--tag", "ui",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = {
            "path": os.path.realpath(str(nested_file)),
            "type": "image",
            "tags": ["demo", "ui"],
        }

        # 移除 a.bin 与父目录，再在 nested 原位置创建普通文件：
        # 中间组件不是目录，记录仍按其登记的规范路径命中，状态为 missing。
        nested_file.unlink()
        os.rmdir(str(nested))
        nested.write_text("not a directory anymore\n", encoding="utf-8")
        self.assertShowWithStatus(
            expected, "missing", str(nested_file), "--check-files"
        )
        # 不带选项时记录照常返回。
        self.assertPlainShow(expected, str(nested_file))

    def test_broken_symlink_at_registered_path_locates_nothing(self):
        self.register_samples()
        self.file_a.unlink()
        link = self.file_a
        target = self.tmp_dir / "gone_target.bin"
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"当前环境不支持创建符号链接: {exc}")

        # 断开的符号链接按链接目标解析：定位语义保持现状——解析后的规范
        # 路径（目标路径）未登记，按未登记失败而非检查链接本身的状态。
        canonical_target = os.path.realpath(str(target))
        stderr = self.assertShowError(str(link), "--check-files")
        self.assertIn("未登记", stderr)
        self.assertIn(canonical_target, stderr)

    def test_symlink_to_another_registered_file_locates_target_record(self):
        self.register_samples()
        self.file_a.unlink()
        try:
            os.symlink(self.file_b, self.file_a)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"当前环境不支持创建符号链接: {exc}")

        # A 原路径现在是指向已登记普通文件 B 的符号链接：realpath 按现状
        # 定位到 B 的记录，状态按链接目标（普通文件）判断为 present，
        # 输出的 path 是 B 登记时保存的规范绝对路径。
        data = self.assertShowWithStatus(
            self.expected_b, "present", str(self.file_a), "--check-files"
        )
        self.assertEqual(data["path"], self.path_b)

    def test_unregistered_path_rejected_whether_file_exists_or_not(self):
        self.register_samples()

        # 未登记且路径不存在：退出码 2、标准输出为空、错误含规范路径。
        missing = self.tmp_dir / "C.bin"
        canonical_missing = os.path.realpath(str(missing))
        stderr = self.assertShowError(str(missing), "--check-files")
        self.assertIn("未登记", stderr)
        self.assertIn(canonical_missing, stderr)

        # 未登记但文件存在：同样按未登记失败，不返回其文件状态。
        existing = self.tmp_dir / "unregistered.bin"
        existing.write_text("not in catalog\n", encoding="utf-8")
        canonical_existing = os.path.realpath(str(existing))
        stderr = self.assertShowError(str(existing), "--check-files")
        self.assertIn("未登记", stderr)
        self.assertIn(canonical_existing, stderr)
        self.assertNotIn("file_status", stderr)

        # 失败后已有记录不变。
        self.assertExport([self.expected_a, self.expected_b])

    def test_permission_error_fails_without_partial_output(self):
        self.register_samples()

        # 对 A 的记录保存路径注入 PermissionError：整次查看退出码 2、
        # 标准输出为空，标准错误指出状态读取失败及相关路径，不含调用栈。
        failed = self.run_cli_with_permission_error(
            self.path_a, "show", str(self.file_a), "--check-files"
        )
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("无法确定文件状态", failed.stderr)
        self.assertIn(self.path_a, failed.stderr)
        self.assertNotIn("Traceback", failed.stderr)

        # 失败后不带选项的查看元数据不变，且不含 file_status。
        self.assertPlainShow(self.expected_a, str(self.file_a))

        # 数据库登记内容不变。
        self.assertExport([self.expected_a, self.expected_b])

    def test_other_assets_state_does_not_affect_this_show(self):
        self.register_samples()
        # B 的源文件删除且原路径变为目录：查看 A 时只检查 A 的保存路径，
        # B 的状态不影响本次结果。
        self.file_b.unlink()
        os.mkdir(self.path_b)
        self.assertShowWithStatus(
            self.expected_a, "present", str(self.file_a), "--check-files"
        )

    def test_check_files_is_readonly_against_database_and_source(self):
        self.register_samples()
        db_before = self.db_path.read_bytes()
        a_before = self.file_a.read_bytes()
        b_before = self.file_b.read_bytes()

        self.assertShowWithStatus(
            self.expected_a, "present", str(self.file_a), "--check-files"
        )
        # 源文件删除后查看 missing：数据库字节同样不变。
        self.file_a.unlink()
        self.assertShowWithStatus(
            self.expected_a, "missing", str(self.file_a), "--check-files"
        )

        self.assertEqual(self.db_path.read_bytes(), db_before)
        self.assertEqual(self.file_b.read_bytes(), b_before)
        self.assertFalse(self.file_a.exists())
        # 恢复仅用于与登记时内容对照，确认查看未改写源文件。
        self.file_a.write_bytes(a_before)
        self.assertEqual(self.file_a.read_bytes(), a_before)

    def test_fresh_database_created_then_unregistered_reported(self):
        fresh = self.tmp_dir / "fresh.sqlite"
        self.assertFalse(fresh.exists())

        # 沿用空库创建规则：合法参数、父目录存在时先创建空库，再按未登记
        # 失败；即使带 --check-files，也不检查未登记路径的状态。
        stderr = self.assertShowError(str(self.file_a), "--check-files", db=fresh)
        self.assertIn("未登记", stderr)
        self.assertIn(self.path_a, stderr)
        self.assertTrue(fresh.exists())
        result = self.run_cli("export", db=fresh)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

    def test_missing_parent_directory_is_not_created(self):
        missing = self.tmp_dir / "no" / "such" / "catalog.sqlite"
        self.assertShowError(str(self.file_a), "--check-files", db=missing)
        self.assertFalse((self.tmp_dir / "no").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
