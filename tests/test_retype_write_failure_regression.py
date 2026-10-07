"""retype 类型写入被数据库拒绝时的回归测试。

通过 README 公开的 ``python -m asset_catalog`` 入口以独立子进程方式观察行为，
只使用 Python 3 标准库（unittest / subprocess / sqlite3 / tempfile），
无需第三方依赖、网络或特殊权限。

固定场景：
- 两个内容固定、路径不同的临时演示文件按 A.bin、B.bin 顺序登记：
  A 类型 image，标签依次为 demo、ui；B 类型 audio，标签只有 demo。
- 样例库保留原有兼容表结构（asset / asset_tag 及索引），仅附加一个
  BEFORE UPDATE 触发器作为固定拒绝条件：拒绝把类型更新为 texture，
  拒绝文本固定为 ``retype blocked``；其他类型与读取操作不受影响。
- 用 ``retype`` 对 A 的登记路径提交 ``--type texture``：更新被触发器
  拒绝，预期退出码 2、标准输出为空，标准错误说明数据库读写失败并保留
  数据库给出的 ``retype blocked`` 文本，不含调用栈；事务整体回滚。
- 失败后由新进程 export 仍按 A、B 的登记顺序返回原记录，规范路径、
  类型与标签顺序不变；``query --tag demo --type image`` 只返回 A，
  改为 texture 时返回 []；两个源文件字节不变。
- 在同一场景撤去拒绝条件（DROP TRIGGER）后用同一输入再次提交：
  退出码 0、标准错误为空，标准输出仅为含 path、type、tags 的 A 记录，
  类型为 texture，标签仍为 demo、ui；新进程 export 仍按 A、B 顺序返回，
  只有 A 的类型变化；按 demo 和 texture 查询只命中 A，按 demo 和 image
  查询返回 []，B 保持原样；两个源文件字节始终不变。

样例库、素材文件均由测试在独立临时目录中准备并自动清理，不访问既有素材。
JSON 比较按 json.loads 解析后的内容判断，不依赖空白或对象键序。
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

REJECT_TRIGGER = "reject_retype_to_texture"
REJECT_MESSAGE = "retype blocked"

# 固定条件：仅拒绝把素材类型更新为 texture，拒绝文本固定为 retype blocked；
# 更新为其他类型（或不触碰类型列的操作）允许执行。
CREATE_REJECT_TRIGGER_SQL = f"""
CREATE TRIGGER {REJECT_TRIGGER}
BEFORE UPDATE ON asset
WHEN NEW.type = 'texture'
BEGIN
    SELECT RAISE(ABORT, '{REJECT_MESSAGE}');
END;
"""

DROP_REJECT_TRIGGER_SQL = f"DROP TRIGGER {REJECT_TRIGGER}"


class RetypeWriteFailureRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.file_a = self.tmp_dir / "A.bin"
        self.file_b = self.tmp_dir / "B.bin"
        self.content_a = b"retype write-failure sample A\n"
        self.content_b = b"retype write-failure sample B\n"
        self.file_a.write_bytes(self.content_a)
        self.file_b.write_bytes(self.content_b)
        self.db_path = self.tmp_dir / "catalog.sqlite"

        self.path_a = os.path.realpath(str(self.file_a))
        self.path_b = os.path.realpath(str(self.file_b))
        self.record_a_image = {
            "path": self.path_a,
            "type": "image",
            "tags": ["demo", "ui"],
        }
        self.record_b = {
            "path": self.path_b,
            "type": "audio",
            "tags": ["demo"],
        }
        self.record_a_texture = {
            "path": self.path_a,
            "type": "texture",
            "tags": ["demo", "ui"],
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
        # 显式把项目根放入 PYTHONPATH，使验收命令在任意工作目录下均可解析
        # asset_catalog 包；子进程仍是全新的独立进程。
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            env=env,
        )

    def open_sample_db(self):
        return sqlite3.connect(str(self.db_path))

    def install_reject_condition(self):
        """附加拒绝把类型更新为 texture 的固定条件（触发器），并提交。"""
        conn = self.open_sample_db()
        try:
            conn.execute(CREATE_REJECT_TRIGGER_SQL)
            conn.commit()
        finally:
            conn.close()

    def remove_reject_condition(self):
        """撤去固定拒绝条件（删除触发器），并提交。"""
        conn = self.open_sample_db()
        try:
            conn.execute(DROP_REJECT_TRIGGER_SQL)
            conn.commit()
        finally:
            conn.close()

    def register_samples(self):
        """按 A、B 顺序登记样例：A(image: demo, ui)，B(audio: demo)。"""
        result_a = self.run_cli(
            "add", str(self.file_a), "--type", "image",
            "--tag", "demo", "--tag", "ui",
        )
        self.assertEqual(result_a.returncode, 0, result_a.stderr)
        self.assertEqual(result_a.stderr, "")
        self.assertEqual(
            json.loads(result_a.stdout),
            self.record_a_image,
            "add A 的输出记录与样例不符",
        )

        result_b = self.run_cli(
            "add", str(self.file_b), "--type", "audio", "--tag", "demo"
        )
        self.assertEqual(result_b.returncode, 0, result_b.stderr)
        self.assertEqual(result_b.stderr, "")
        self.assertEqual(
            json.loads(result_b.stdout), self.record_b, "add B 的输出记录与样例不符"
        )

    def retype_a_to_texture(self):
        """用同一输入提交 retype：对 A 的登记路径设置 --type texture。"""
        return self.run_cli("retype", str(self.file_a), "--type", "texture")

    def assert_export(self, expected_records):
        result = self.run_cli("export")
        self.assertEqual(
            result.returncode,
            0,
            f"export 应成功退出，实际退出码 {result.returncode}，"
            f"标准错误: {result.stderr!r}",
        )
        self.assertEqual(result.stderr, "", "export 的标准错误应为空")
        self.assertEqual(
            json.loads(result.stdout),
            expected_records,
            "export 的完整目录记录、登记顺序或标签顺序与预期不符",
        )

    def assert_query(self, tag, asset_type, expected_records):
        args = ["query", "--tag", tag]
        if asset_type is not None:
            args.extend(["--type", asset_type])
        result = self.run_cli(*args)
        type_desc = f" --type {asset_type!r}" if asset_type is not None else ""
        self.assertEqual(
            result.returncode,
            0,
            f"query --tag {tag!r}{type_desc} 应成功退出，实际退出码 "
            f"{result.returncode}，标准错误: {result.stderr!r}",
        )
        self.assertEqual(
            result.stderr,
            "",
            f"query --tag {tag!r}{type_desc} 的标准错误应为空",
        )
        self.assertEqual(
            json.loads(result.stdout),
            expected_records,
            f"query --tag {tag!r}{type_desc} 的持久化记录与预期不符",
        )

    def assert_files_unchanged(self):
        self.assertEqual(
            self.file_a.read_bytes(), self.content_a, "A.bin 文件内容发生变化"
        )
        self.assertEqual(
            self.file_b.read_bytes(), self.content_b, "B.bin 文件内容发生变化"
        )

    def test_retype_blocked_keeps_old_record_then_succeeds_when_condition_removed(
        self,
    ):
        # ---- add 仅准备样例：按 A、B 顺序登记 ----
        self.register_samples()

        # ---- 附加固定拒绝条件；原兼容表结构与读取功能保持不变 ----
        self.install_reject_condition()
        self.assert_export([self.record_a_image, self.record_b])

        # ---- 拒绝在场：retype A --type texture 被数据库拒绝 ----
        result = self.retype_a_to_texture()
        self.assertEqual(
            result.returncode,
            2,
            "类型写入被拒应退出为 2；"
            f"实际退出码 {result.returncode}，输出: {result.stdout!r}，"
            f"错误: {result.stderr!r}",
        )
        self.assertEqual(result.stdout, "", "失败时标准输出应为空")
        self.assertNotEqual(result.stderr.strip(), "", "失败时标准错误应说明原因")
        self.assertEqual(
            len(result.stderr.strip().splitlines()),
            1,
            f"失败原因应为单行说明，实际为: {result.stderr!r}",
        )
        self.assertIn(
            "数据库读写失败",
            result.stderr,
            f"标准错误应说明数据库读写失败，实际为: {result.stderr!r}",
        )
        self.assertIn(
            REJECT_MESSAGE,
            result.stderr,
            f"标准错误应保留数据库给出的拒绝文本 {REJECT_MESSAGE!r}，"
            f"实际为: {result.stderr!r}",
        )
        self.assertNotIn("Traceback", result.stderr, "标准错误不应包含调用栈")

        # ---- 失败后由新进程核对：旧记录完整保留 ----
        # export 仍按 A、B 顺序返回：规范路径、类型与标签顺序均不变。
        self.assert_export([self.record_a_image, self.record_b])
        # A 仍可按旧类型 image 与标签 demo 查到；texture 无任何命中。
        self.assert_query("demo", "image", [self.record_a_image])
        self.assert_query("demo", "texture", [])
        # 不带类型的 demo 查询仍按登记顺序返回 A、B，B 未受影响。
        self.assert_query("demo", None, [self.record_a_image, self.record_b])
        # 两个源文件字节不变。
        self.assert_files_unchanged()

        # ---- 同一场景撤去拒绝条件，用同一输入再次提交 ----
        self.remove_reject_condition()
        result = self.retype_a_to_texture()
        self.assertEqual(
            result.returncode,
            0,
            "撤去拒绝条件后相同修改应成功；"
            f"实际退出码 {result.returncode}，标准错误: {result.stderr!r}",
        )
        self.assertEqual(result.stderr, "", "成功时标准错误应为空")
        self.assertEqual(
            len(result.stdout.strip().splitlines()),
            1,
            "成功时标准输出应只有一条 JSON 记录",
        )
        payload = json.loads(result.stdout)
        self.assertEqual(
            set(payload),
            {"path", "type", "tags"},
            "成功输出应为仅含 path、type、tags 的 JSON 对象",
        )
        self.assertEqual(
            payload,
            self.record_a_texture,
            "A 的类型应变为 texture，路径与标签顺序保持不变",
        )
        self.assertEqual(payload["type"], "texture")
        self.assertEqual(payload["tags"], ["demo", "ui"])
        self.assertEqual(payload["path"], self.path_a)

        # ---- 新进程核对持久化结果：只有 A 的类型变化 ----
        self.assert_export([self.record_a_texture, self.record_b])
        # 按 demo 和 texture 查询只命中 A；按 demo 和 image 查询返回 []。
        self.assert_query("demo", "texture", [self.record_a_texture])
        self.assert_query("demo", "image", [])
        # B 保持原样：demo 查询仍按 A、B 顺序返回两条，B 类型仍为 audio。
        self.assert_query("demo", None, [self.record_a_texture, self.record_b])
        # 两次操作均不改写源文件。
        self.assert_files_unchanged()


if __name__ == "__main__":
    unittest.main(verbosity=2)
