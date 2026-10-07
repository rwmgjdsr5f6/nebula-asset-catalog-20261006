"""query 排除路径的状态读取错误不影响查询的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile），无需第三方
依赖、网络或特殊权限。

固定场景：
- 临时目录中按 A.bin、B.bin、C.bin 顺序登记为 image，标签依次为
  ["demo", "ui"]、["demo", "archived"]、["ui"]。
- 三个文件始终是普通文件；仅 B 的规范登记路径在状态读取时固定产生
  PermissionError（由测试经 PYTHONPATH 注入的 sitecustomize 垫片在
  子进程内替换 os.stat 实现，仅对 B 的规范路径抛错），其他路径正常。
  场景不依赖实际权限配置。

覆盖：
- 查询选择 any 命中 demo、ui 再排除 archived：被排除的 B 不进入候选，
  其状态读取错误不影响查询——
  * 仅 --check-files：退出码 0、标准错误为空，标准输出是按 A、C 顺序
    排列的 JSON 数组，记录保留规范路径、类型、完整标签及其顺序，并追加
    值为 present 的 file_status；
  * 仅 --file-status present：仍返回 A、C，记录仅含 path、type、tags；
  * 两组选项同用：返回 A、C 并附加 present 状态；
- 对照：相同样例与错误条件下取消 --exclude-tag，分别检查仅附加状态、
  仅筛选 present、两者同用的查询，均以退出码 2 结束，标准输出为空，
  标准错误说明状态读取失败并包含 B 的规范路径，不含调用栈，也不返回
  已处理的 A；
- 每个用例结束后由新进程 export 核对全部登记记录及顺序未变，数据库
  文件与源文件字节保持不变。

测试比较解析后的 JSON，不依赖对象键序或文本空白；临时数据由测试自行
清理，登记、查询与导出分别由独立进程完成。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

CONTENT_A = b"exclusion status-error sample A\n"
CONTENT_B = b"exclusion status-error sample B\n"
CONTENT_C = b"exclusion status-error sample C\n"

# 子进程内自动导入的垫片：仅当环境变量给出目标路径时，把 os.stat 替换为
# 对该路径固定抛出 PermissionError 的版本，其余路径照常。查询进程对 B 的
# 规范路径读取状态时即得到稳定的权限错误，不依赖实际权限配置。
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


class QueryExclusionStatusErrorRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "A.bin"
        self.file_b = self.tmp_dir / "B.bin"
        self.file_c = self.tmp_dir / "C.bin"
        self.file_a.write_bytes(CONTENT_A)
        self.file_b.write_bytes(CONTENT_B)
        self.file_c.write_bytes(CONTENT_C)
        self.db_path = self.tmp_dir / "catalog.sqlite"

        # 垫片目录：sitecustomize.py 随 PYTHONPATH 进入每个子进程，
        # 仅在环境变量点名 B 的规范路径时生效。
        self.shim_dir = self.tmp_dir / "shim"
        self.shim_dir.mkdir()
        (self.shim_dir / "sitecustomize.py").write_text(
            SITECUSTOMIZE_SOURCE, encoding="utf-8"
        )

        self.path_a = os.path.realpath(str(self.file_a))
        self.path_b = os.path.realpath(str(self.file_b))
        self.path_c = os.path.realpath(str(self.file_c))
        self.record_a = {
            "path": self.path_a,
            "type": "image",
            "tags": ["demo", "ui"],
        }
        self.record_b = {
            "path": self.path_b,
            "type": "image",
            "tags": ["demo", "archived"],
        }
        self.record_c = {
            "path": self.path_c,
            "type": "image",
            "tags": ["ui"],
        }

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args, fail_stat_on_b=False):
        """以全新进程运行 python -m asset_catalog，返回 CompletedProcess。

        fail_stat_on_b 为真时，该进程内对 B 的规范登记路径的 os.stat
        固定抛出 PermissionError，其余路径不受影响。
        """
        cmd = [
            sys.executable,
            "-m",
            "asset_catalog",
            "--db",
            str(self.db_path),
            *args,
        ]
        # 显式把项目根与垫片目录放入 PYTHONPATH，使验收命令在任意工作
        # 目录下均可解析 asset_catalog 包与 sitecustomize 垫片；子进程
        # 仍是全新的独立进程。
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            [str(PROJECT_ROOT), str(self.shim_dir), env.get("PYTHONPATH", "")]
        )
        if fail_stat_on_b:
            env[STAT_FAIL_ENV] = self.path_b
        else:
            env.pop(STAT_FAIL_ENV, None)
        return subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            env=env,
        )

    def register_samples(self):
        """按 A、B、C 顺序登记三个 image 文件，并记录登记后的数据库字节。"""
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
        self.db_bytes_after_registration = self.db_path.read_bytes()

    def query_args(self, *extra):
        """any 命中 demo、ui 的公共查询条件，附加给定选项。"""
        return [
            "query", "--tag-mode", "any",
            "--tag", "demo", "--tag", "ui",
            *extra,
        ]

    def assertQueryOkWithExclusion(self, extra, expected):
        """排除 archived 且 B 状态读取固定失败时，查询仍成功返回预期记录。"""
        result = self.run_cli(
            *self.query_args("--exclude-tag", "archived", *extra),
            fail_stat_on_b=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        # 比较解析后的数据，不依赖对象键顺序或 JSON 空白。
        self.assertEqual(data, expected)
        return data

    def assertQueryFailsWithoutExclusion(self, extra):
        """取消排除时，B 的状态读取失败使整次查询以退出码 2 失败。"""
        result = self.run_cli(*self.query_args(*extra), fail_stat_on_b=True)
        self.assertEqual(
            result.returncode,
            2,
            f"状态读取失败应退出为 2；实际退出码 {result.returncode}，"
            f"输出: {result.stdout!r}，错误: {result.stderr!r}",
        )
        # 不返回部分结果：已处理的 A 也不出现在标准输出中。
        self.assertEqual(result.stdout, "", "失败时标准输出应为空")
        self.assertIn(
            "无法确定文件状态",
            result.stderr,
            f"标准错误应说明状态读取失败，实际为: {result.stderr!r}",
        )
        self.assertIn(
            self.path_b,
            result.stderr,
            f"标准错误应包含 B 的规范路径，实际为: {result.stderr!r}",
        )
        self.assertNotIn("Traceback", result.stderr, "标准错误不应包含调用栈")

    def assertUnchanged(self):
        """新进程 export 核对全部登记记录及顺序未变；数据库与源文件字节不变。"""
        result = self.run_cli("export")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            [self.record_a, self.record_b, self.record_c],
            "export 的完整目录记录或登记顺序与预期不符",
        )
        self.assertEqual(
            self.db_path.read_bytes(),
            self.db_bytes_after_registration,
            "数据库文件字节发生变化",
        )
        self.assertEqual(self.file_a.read_bytes(), CONTENT_A)
        self.assertEqual(self.file_b.read_bytes(), CONTENT_B)
        self.assertEqual(self.file_c.read_bytes(), CONTENT_C)

    def test_check_files_with_exclusion_ignores_excluded_status_error(self):
        """仅 --check-files：被排除的 B 状态读取失败不影响 A、C 的输出。"""
        self.register_samples()

        expected = [
            {**self.record_a, "file_status": "present"},
            {**self.record_c, "file_status": "present"},
        ]
        data = self.assertQueryOkWithExclusion(["--check-files"], expected)
        # 按 A、C 顺序排列，每条只出现一次。
        self.assertEqual([r["path"] for r in data], [self.path_a, self.path_c])
        for record in data:
            self.assertEqual(
                set(record), {"path", "type", "tags", "file_status"}
            )
        # 完整标签及其顺序保留。
        self.assertEqual(data[0]["tags"], ["demo", "ui"])
        self.assertEqual(data[1]["tags"], ["ui"])

        self.assertUnchanged()

    def test_file_status_filter_with_exclusion_ignores_excluded_status_error(
        self,
    ):
        """仅 --file-status present：仍返回 A、C，记录仅含 path、type、tags。"""
        self.register_samples()

        data = self.assertQueryOkWithExclusion(
            ["--file-status", "present"],
            [self.record_a, self.record_c],
        )
        self.assertEqual([r["path"] for r in data], [self.path_a, self.path_c])
        for record in data:
            self.assertEqual(set(record), {"path", "type", "tags"})

        self.assertUnchanged()

    def test_combined_options_with_exclusion_ignore_excluded_status_error(
        self,
    ):
        """两组选项同用：返回 A、C 并附加值为 present 的 file_status。"""
        self.register_samples()

        expected = [
            {**self.record_a, "file_status": "present"},
            {**self.record_c, "file_status": "present"},
        ]
        data = self.assertQueryOkWithExclusion(
            ["--check-files", "--file-status", "present"], expected
        )
        self.assertEqual([r["path"] for r in data], [self.path_a, self.path_c])
        for record in data:
            self.assertEqual(record["file_status"], "present")
            self.assertEqual(
                set(record), {"path", "type", "tags", "file_status"}
            )

        self.assertUnchanged()

    def test_check_files_without_exclusion_fails_on_status_error(self):
        """对照：取消排除后仅附加状态的查询因 B 的状态读取失败整体失败。"""
        self.register_samples()

        self.assertQueryFailsWithoutExclusion(["--check-files"])

        self.assertUnchanged()

    def test_file_status_filter_without_exclusion_fails_on_status_error(self):
        """对照：取消排除后仅筛选 present 的查询同样整体失败。"""
        self.register_samples()

        self.assertQueryFailsWithoutExclusion(["--file-status", "present"])

        self.assertUnchanged()

    def test_combined_options_without_exclusion_fail_on_status_error(self):
        """对照：取消排除后两组选项同用的查询同样整体失败。"""
        self.register_samples()

        self.assertQueryFailsWithoutExclusion(
            ["--check-files", "--file-status", "present"]
        )

        self.assertUnchanged()


if __name__ == "__main__":
    unittest.main(verbosity=2)
