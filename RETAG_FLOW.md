# retag 标签编辑流程说明

本文对应 `asset_catalog/cli.py` 中 retag（替换 / 追加 / 移除标签）这条流程的
实际函数，说明三种模式的标签结果如何计算、失败时记录状态如何保证，便于逐条
核对。行号对应当前版本，函数名是稳定锚点。命令参数、帮助语义、输出格式与
SQLite 结构均未改变。

## 1. 全流程总览

```
python -m asset_catalog --db DB retag PATH --tag T... [--append | --remove]
        │  asset_catalog/__main__.py → cli.py: main()（cli.py:715）
        ▼
argparse 解析：--append 与 --remove 互斥（build_parser，cli.py:34），
同时出现或参数不支持时退出码 2、标准输出为空、不创建数据库
        ▼
handle_retag（cli.py:610）按顺序编排下列步骤：
  ① 路径规范化为规范绝对路径（os.path.realpath，与 add 同一规则）
  ② normalize_tags（cli.py:226）：去首尾空白、按首次出现顺序去重、
     保留大小写；空标签报错。①② 均在打开数据库之前，参数错误不建库
  ③ resolve_retag_mode（cli.py:537）：把互斥开关归并为模式字符串
  ④ open_database（cli.py:140）：打开/创建并校验结构
  ⑤ 按规范路径定位素材；未登记报错（错误含规范路径）
  ⑥ fetch_current_tags（cli.py:585）：仅 append / remove 读取旧标签
  ⑦ compute_retag_tags（cli.py:551）：纯函数算出最终标签列表
  ⑧ rewrite_tags（cli.py:597）+ commit：同一事务删除旧标签、写入新标签
        ▼
标准输出仅一行 JSON：{"path": ..., "type": ..., "tags": [...]}，退出码 0
```

任何阶段抛出 `CliError` 都由 `main()` 统一收敛：标准错误单行
`asset_catalog: 错误: <原因>`（不含调用栈），退出码 2，标准输出为空。

## 2. 三种模式的标签结果：compute_retag_tags（cli.py:551）

最终标签列表由这一个纯函数决定，不触碰数据库，可脱离数据库单独核对
（`new_tags` 是 `normalize_tags` 去重后的输入，`existing_tags` 是
`fetch_current_tags` 按 position 升序读出的当前标签）：

| 模式 | 常量 | 结果 |
|------|------|------|
| 替换（默认） | `RETAG_MODE_REPLACE` | 即 `new_tags`；不读取旧标签（`existing_tags` 传 `None`） |
| 追加 `--append` | `RETAG_MODE_APPEND` | 旧标签及顺序保留，已存在的标签（区分大小写）不移动，只把尚不存在的新标签接在末尾；重复追加同一批标签结果不变 |
| 移除 `--remove` | `RETAG_MODE_REMOVE` | 按完整文本区分大小写移除，不存在的标签忽略，其余保持原顺序；会移除全部标签时抛 `CliError`，整次拒绝、不改动任何记录 |

验收样例可直接对照此表：A 原标签 `["demo","ui"]`，`--append --tag ui --tag UI`
→ `ui` 已存在不移动、`UI` 区分大小写接到末尾，得 `["demo","ui","UI"]`；
再 `--remove --tag ui` → `["demo","UI"]`。B 的记录与两条素材的登记顺序
（asset.id 升序）全程不被触及。

## 3. 失败边界与记录状态

- **参数错误（退出码 2，不创建数据库）**：路径缺失或为空、缺少 `--tag`、
  任一标签为空或仅含空白、不支持的参数、`--append` 与 `--remove` 同时出现。
  路径与标签校验（`handle_retag` 步骤 ①②）在 `open_database` 之前完成。
- **未登记目标**：报错含规范路径；数据库不存在但父目录存在时
  `open_database` 先创建空库再报告未登记，父目录缺失时不补建目录。
- **数据库打不开、损坏、结构不兼容**：`open_database` 按既有口径拒绝，
  不覆盖原库。
- **写入中途失败**：`rewrite_tags`（cli.py:597）的 DELETE 与 INSERT 与
  `handle_retag` 的 `conn.commit()` 构成同一事务；任一步抛出
  `sqlite3.Error` 时 `handle_retag` 整体 `rollback`，旧标签及顺序完整
  保留，不留下部分新标签，也不新增素材或改变其他记录。
- **移除全部标签**：`compute_retag_tags` 在任何写入之前抛出 `CliError`，
  既有记录原样保留。

## 4. 成功后的可核对性

- 输出 JSON 的 `tags` 即 `compute_retag_tags` 的返回值，与
  `rewrite_tags` 写入数据库的顺序完全一致（position 从 0 递增），
  因此标准输出、query 与 export 的新进程读取结果三者必然一致。
- 只改标签：路径、类型、首次登记顺序与其他素材的记录保持不变；
  源文件已删除或变成目录仍可编辑，源文件内容始终只读。

## 5. 回归对照

- `tests/test_retag_regression.py`：替换模式与失败边界；
- `tests/test_retag_append_regression.py` / `test_retag_remove_regression.py`：
  追加与移除模式；
- `tests/test_retag_atomicity.py`：写入中途失败（第二个新标签被触发器
  拒绝）时事务整体回滚，旧标签完整保留。
