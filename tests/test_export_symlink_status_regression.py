"""export --check-files 在登记路径后来变为符号链接时的专项回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / tempfile / os / json /
pathlib），无需第三方依赖。

核心约定：``export --check-files`` 的状态按登记路径处对象的**链接目标**判断
（内部对该路径调用 ``os.stat``，跟随符号链接），而输出仍保留登记时保存的
**原登记路径**（export 不做路径解析与定位，直接回显数据库中的 path）。

两组独立临时样例，均按 A、B 顺序登记：A 类型 ``image``，标签依次为
``demo``、``ui``；B 类型 ``audio``，标签只有 ``music``。

第一组（A 原路径替换为指向**未登记普通文件 T** 的符号链接）：
- 链接有效时 ``export --check-files`` 按 A、B 顺序各输出一次，两者状态均为
  present；A 的 path/type/tags 保持登记值，T 不成为目录记录；
- 删除 T 形成断链后，A 为 missing、B 仍为 present，两条记录均保留；
- 在原目标位置按原内容重建 T 后，新导出进程重新报告 A 为 present，
  无需再次登记。

第二组（A 原路径替换为指向**临时目录**的符号链接）：
- A 为 not_file，B 仍为 present，结果顺序与元数据不变。

通用核对：
- 上述带选项导出均退出码 0、标准错误为空、标准输出只有一行 JSON 数组；
  断链应成功报告 missing（不报错、不丢记录）；
- 每种状态下不带 --check-files 的 export 仍返回两条原元数据记录，
  不含 file_status；
- 比较解析后的内容，不依赖 JSON 空白或键顺序；
- 测试核对每次导出前后样例数据库字节、B 与仍存在的 T 内容以及链接指向
  不变（样例准备中主动删除或重建的内容除外）；
- 环境确实不支持创建符号链接时仅 skipTest 跳过相关用例并说明原因，
  其他错误应使测试失败，不把跳过记为行为验证通过。

每个用例使用独立的临时目录与数据库，只创建/清理自己的样例文件，
不访问真实素材或网络，重复执行结论一致。
"""

import errno
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# “确实无法在此环境创建符号链接”的 errno 集合：仅这些错误按跳过处理，
# 其余 OSError 属于测试自身问题，应使测试失败。
_SYMLINK_UNSUPPORTED_ERRNOS = {
    errno.EPERM,
    getattr(errno, "ENOSYS", None),
    getattr(errno, "ENOTSUP", None),
}
_SYMLINK_UNSUPPORTED_ERRNOS.discard(None)

# 样例文件固定内容（字节），用于逐字节核对导出只读、T 的删除/重建可预期。
CONTENT_A = b"demo asset A\n"
CONTENT_B = b"demo asset B\n"
CONTENT_T = b"unregistered target T\n"
CONTENT_T_REBUILT = CONTENT_T  # 在原目标位置按原内容重建 T。


def make_samples():
    """创建一组独立临时样例：A、B 普通文件（暂不创建 T 与符号链接）。

    返回包含临时目录句柄（用于结束后清理）与各路径的命名字典。
    """
    tmp_holder = tempfile.TemporaryDirectory()
    tmp_dir = Path(tmp_holder.name)
    file_a = tmp_dir / "A.bin"
    file_b = tmp_dir / "B.bin"
    file_t = tmp_dir / "T.bin"
    file_a.write_bytes(CONTENT_A)
    file_b.write_bytes(CONTENT_B)
    db_path = tmp_dir / "catalog.sqlite"
    return {
        "holder": tmp_holder,
        "dir": tmp_dir,
        "a": file_a,
        "b": file_b,
        "t": file_t,
        "db": db_path,
        "path_a": os.path.realpath(str(file_a)),
        "path_b": os.path.realpath(str(file_b)),
        "path_t": os.path.realpath(str(file_t)),
        "expected_a": {
            "path": os.path.realpath(str(file_a)),
            "type": "image",
            "tags": ["demo", "ui"],
        },
        "expected_b": {
            "path": os.path.realpath(str(file_b)),
            "type": "audio",
            "tags": ["music"],
        },
    }


def symlink_or_skip(testcase, target, link, *, where):
    """在 link 处创建指向 target 的符号链接；环境不支持时跳过该用例。

    只把“确实无法创建符号链接”（PermissionError / OSError 含不支持语义、
    NotImplementedError）当作跳过理由；其他错误继续抛出使测试失败。
    """
    try:
        os.symlink(target, link)
    except NotImplementedError as exc:
        testcase.skipTest(f"当前环境不支持创建符号链接（{where}）: {exc}")
    except PermissionError as exc:
        # 常见于受限制/非开发者模式的 Windows：无法创建符号链接。
        testcase.skipTest(f"当前环境不支持创建符号链接（{where}）: {exc}")
    except OSError as exc:
        # EPERM 等“操作不允许”视为环境不支持符号链接；其余 OSError（如
        # 已存在、路径过长等）属于测试自身问题，应使测试失败。
        if getattr(exc, "errno", None) in _SYMLINK_UNSUPPORTED_ERRNOS:
            testcase.skipTest(f"当前环境不支持创建符号链接（{where}）: {exc}")
        raise


class ExportSymlinkStatusRegressionTest(unittest.TestCase):
    """覆盖 export --check-files 按链接目标判状态、输出保留原登记路径。"""

    def setUp(self):
        self.s = make_samples()
        self.addCleanup(self.s["holder"].cleanup)

    def run_cli(self, *args):
        """以全新进程运行 python -m asset_catalog --db <库> ...。"""
        cmd = [
            sys.executable,
            "-m",
            "asset_catalog",
            "--db",
            str(self.s["db"]),
            *args,
        ]
        return subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )

    def register_a_then_b(self):
        """按 A、B 顺序登记（add 仅用于准备数据，非本次测试对象）。"""
        result_a = self.run_cli(
            "add", str(self.s["a"]),
            "--type", "image", "--tag", "demo", "--tag", "ui",
        )
        self.assertEqual(result_a.returncode, 0, result_a.stderr)
        self.assertEqual(result_a.stderr, "")
        self.assertEqual(json.loads(result_a.stdout), self.s["expected_a"])

        result_b = self.run_cli(
            "add", str(self.s["b"]),
            "--type", "audio", "--tag", "music",
        )
        self.assertEqual(result_b.returncode, 0, result_b.stderr)
        self.assertEqual(result_b.stderr, "")
        self.assertEqual(json.loads(result_b.stdout), self.s["expected_b"])

    def assertExportOkSingleLineArray(self, check_files, expected):
        """成功导出：退出码 0、标准错误为空、标准输出仅一行 JSON 数组。

        通过 json.loads 比较解析后的内容，不依赖 JSON 空白或键顺序。
        """
        args = ("--check-files",) if check_files else ()
        result = self.run_cli("export", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        # 标准输出只有一行 JSON 数组（末尾恰好一个换行符）。
        self.assertEqual(result.stdout.count("\n"), 1, result.stdout)
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        self.assertEqual(data, expected)
        return data

    def assertCheckFiles(self, status_a, status_b):
        """带 --check-files 导出：按 A、B 顺序各一次，状态分别给定。"""
        data = self.assertExportOkSingleLineArray(
            True,
            [
                {**self.s["expected_a"], "file_status": status_a},
                {**self.s["expected_b"], "file_status": status_b},
            ],
        )
        self.assertEqual(len(data), 2)
        self.assertEqual(
            [r["file_status"] for r in data], [status_a, status_b]
        )
        # A 的 path/type/tags 保持登记值；B 同理。输出路径仍是原登记路径，
        # 不是链接目标 T 的路径，也没有 T 的目录记录。
        for record, base in zip(data, (self.s["expected_a"], self.s["expected_b"])):
            self.assertEqual(record["path"], base["path"])
            self.assertTrue(os.path.isabs(record["path"]))
            self.assertEqual(record["type"], base["type"])
            self.assertEqual(record["tags"], base["tags"])
        self.assertNotIn(self.s["path_t"], [r["path"] for r in data])
        return data

    def assertPlainExportUnchanged(self):
        """不带 --check-files：两条原元数据记录，键仅 path/type/tags。"""
        data = self.assertExportOkSingleLineArray(
            False, [self.s["expected_a"], self.s["expected_b"]]
        )
        for record in data:
            self.assertEqual(set(record), {"path", "type", "tags"})
            self.assertNotIn("file_status", record)
        return data

    def assertNoRecordForT(self):
        """T 从未登记：目录中没有以 T 的规范路径为 path 的记录。"""
        result = self.run_cli("export")
        self.assertEqual(result.returncode, 0, result.stderr)
        paths = [r["path"] for r in json.loads(result.stdout)]
        self.assertNotIn(self.s["path_t"], paths)

    def snapshot_bytes(self):
        """读取当前可读取的只读基线：db 与 B 始终存在；T 存在时纳入。"""
        return {
            "db": self.s["db"].read_bytes(),
            "b": self.s["b"].read_bytes(),
            "t": self.s["t"].read_bytes() if self.s["t"].exists() else None,
        }

    def assertLinkStillPointsTo(self, target, *, exists_target):
        """链接仍在、仍指向给定目标（逐字节比较 readlink 字符串）。"""
        self.assertTrue(
            self.s["a"].is_symlink(), "A 原路径应为符号链接"
        )
        self.assertEqual(os.readlink(str(self.s["a"])), str(target))
        if exists_target:
            self.assertTrue(self.s["t"].exists())
        else:
            self.assertFalse(self.s["t"].exists())

    def replace_a_with_link_to_t(self):
        """删除普通文件 A，在 A 原路径创建指向未登记普通文件 T 的符号链接。"""
        self.s["a"].unlink()
        self.assertFalse(self.s["a"].exists())
        symlink_or_skip(
            self, self.s["t"], self.s["a"], where="A 原路径 -> 未登记文件 T"
        )
        # 创建链接时 T 尚不存在（断链）；随后再写入 T 形成有效链接。
        self.s["t"].write_bytes(CONTENT_T)
        self.assertTrue(self.s["t"].is_file())
        self.assertTrue(self.s["a"].exists())  # 跟随链接到已存在的 T。

    def test_link_to_unregistered_file_present_and_no_t_record(self):
        self.register_a_then_b()
        self.replace_a_with_link_to_t()

        # 链接有效：按链接目标（未登记普通文件 T）判断为 present，
        # B 也 present；A 的 path/type/tags 保持登记值，T 不成为记录。
        self.assertCheckFiles("present", "present")
        # 每种状态下普通导出仍返回两条原元数据，不含 file_status。
        self.assertPlainExportUnchanged()
        self.assertNoRecordForT()

    def test_broken_link_reports_missing_then_rebuilt_reports_present(self):
        self.register_a_then_b()
        self.replace_a_with_link_to_t()

        # 删除 T 形成断链：A 报告 missing，B 仍 present，两条记录均保留。
        self.s["t"].unlink()
        self.assertTrue(self.s["a"].is_symlink())
        self.assertFalse(self.s["a"].exists())  # 跟随链接失败 => 路径不存在。
        self.assertCheckFiles("missing", "present")
        self.assertPlainExportUnchanged()

        # 在原目标位置按原内容重建 T：新导出进程重新报告 A 为 present，
        # 无需再次登记。
        self.s["t"].write_bytes(CONTENT_T_REBUILT)
        self.assertTrue(self.s["a"].exists())
        self.assertCheckFiles("present", "present")
        self.assertPlainExportUnchanged()
        self.assertNoRecordForT()

    def test_exports_readonly_database_b_and_target_bytes(self):
        """三种状态下，导出均不改写数据库字节、B 与仍存在的 T 的内容。"""
        self.register_a_then_b()
        self.replace_a_with_link_to_t()

        link_target_str = str(self.s["t"])

        # 状态 1：链接有效（present/present）。
        snap = self.snapshot_bytes()
        self.assertCheckFiles("present", "present")
        self.assertPlainExportUnchanged()
        self.assertEqual(self.s["db"].read_bytes(), snap["db"])
        self.assertEqual(self.s["b"].read_bytes(), snap["b"])
        self.assertEqual(self.s["t"].read_bytes(), CONTENT_T)
        self.assertLinkStillPointsTo(link_target_str, exists_target=True)

        # 状态 2：断链（missing/present）。删除 T 属样例准备的主动变更。
        self.s["t"].unlink()
        snap = self.snapshot_bytes()
        self.assertCheckFiles("missing", "present")
        self.assertPlainExportUnchanged()
        self.assertEqual(self.s["db"].read_bytes(), snap["db"])
        self.assertEqual(self.s["b"].read_bytes(), snap["b"])
        # T 已被主动删除，导出不应重新创建 T，链接仍在且指向不变。
        self.assertFalse(self.s["t"].exists())
        self.assertLinkStillPointsTo(link_target_str, exists_target=False)

        # 状态 3：重建 T（present/present）。重建属样例准备的主动变更。
        self.s["t"].write_bytes(CONTENT_T_REBUILT)
        snap = self.snapshot_bytes()
        self.assertCheckFiles("present", "present")
        self.assertPlainExportUnchanged()
        self.assertEqual(self.s["db"].read_bytes(), snap["db"])
        self.assertEqual(self.s["b"].read_bytes(), snap["b"])
        self.assertEqual(self.s["t"].read_bytes(), CONTENT_T)
        self.assertLinkStillPointsTo(link_target_str, exists_target=True)

    def test_link_to_directory_reports_not_file(self):
        """第二组独立样例：A 原路径替换为指向临时目录的符号链接。"""
        self.register_a_then_b()

        target_dir = self.s["dir"] / "D"
        target_dir.mkdir()
        self.s["a"].unlink()
        symlink_or_skip(
            self, target_dir, self.s["a"], where="A 原路径 -> 临时目录 D"
        )
        self.assertTrue(self.s["a"].is_symlink())
        self.assertTrue(os.path.isdir(str(self.s["a"])))  # 跟随链接到目录。

        # A 按链接目标（目录）判断为 not_file，B 仍 present，顺序与元数据不变。
        self.assertCheckFiles("not_file", "present")
        self.assertPlainExportUnchanged()

        # 导出不改写数据库、B 内容与链接指向；目录 D 也不被改动。
        db_before = self.s["db"].read_bytes()
        b_before = self.s["b"].read_bytes()
        link_before = os.readlink(str(self.s["a"]))
        d_entries_before = sorted(p.name for p in target_dir.iterdir())
        self.assertCheckFiles("not_file", "present")
        self.assertPlainExportUnchanged()
        self.assertEqual(self.s["db"].read_bytes(), db_before)
        self.assertEqual(self.s["b"].read_bytes(), b_before)
        self.assertEqual(os.readlink(str(self.s["a"])), link_before)
        self.assertEqual(
            sorted(p.name for p in target_dir.iterdir()), d_entries_before
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
