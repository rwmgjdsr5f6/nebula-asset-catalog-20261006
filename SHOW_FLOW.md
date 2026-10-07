# show 按路径查看单条记录流程说明

本文对应 `asset_catalog/cli.py` 中 show（按素材路径查看单条已登记记录）这条
新增流程的实际函数，说明记录如何定位与组装、失败时为何不输出部分结果，便于
逐条核对。行号对应当前版本，函数名是稳定锚点。show 为只读操作：不新增参数
语义之外的副作用，命令参数风格、JSON 输出字段、SQLite 结构与源文件只读规则
均与既有流程一致。

## 1. 全流程总览

```
python -m asset_catalog --db DB show PATH
        │  asset_catalog/__main__.py → cli.py: main()（cli.py:751）
        ▼
argparse 解析（build_parser，cli.py:34；show 子命令定义在 cli.py:105-109）：
path 为唯一定位参数；缺参或出现不支持的参数（含 --tag 等其他子命令选项）
时退出码 2、标准输出为空、不创建数据库
        ▼
handle_show（cli.py:537）按顺序编排下列步骤：
  ① 路径校验与规范化（cli.py:542-544）：空路径报错；
     os.path.realpath 解析为规范绝对路径（与 add 同一规则），
     该校验在打开数据库之前，参数错误不建库
  ② open_database（cli.py:146）：打开/创建并校验结构
  ③ 定位记录（cli.py:548-553）：按规范路径查 asset 表；未登记报错
     （错误含规范路径）
  ④ 组装记录（cli.py:556）：复用 build_records（cli.py:342）按
     position 升序读出完整标签，取唯一一条记录
        ▼
标准输出仅一行 JSON：{"path": ..., "type": ..., "tags": [...]}，退出码 0
（cli.py:563-564）
```

任何阶段抛出 `CliError` 都由 `main()`（cli.py:754-757）统一收敛：标准错误
单行 `asset_catalog: 错误: <原因>`（不含调用栈），退出码 2，标准输出为空。

## 2. 路径处理规则

- **路径**（cli.py:542-544）：`os.path.realpath` 解析相对路径、`.`/`..` 与
  符号链接，得到规范绝对路径；相对路径、含 `.` 或 `..` 的等价写法，以及解析
  后指向同一登记路径的符号链接，都定位同一条登记记录。空字符串在打开数据库
  之前即报错（cli.py:542-543），不触发建库。
- **不检查文件状态**：show 只在数据库中按规范路径匹配，不调用
  `os.path.exists`/`os.stat`，也不读取素材内容。源文件已删除或原登记路径
  变成目录时，通过原登记路径仍返回该记录；断开的符号链接只有在其规范路径
  恰好已登记时才命中，否则按未登记处理。
- **只读**：整个流程只对数据库执行 SELECT，不写入数据库，也不创建、移动或
  改写源文件。

## 3. 成功时的可核对结果

- 退出码 0，标准错误为空。
- 标准输出仅一行 JSON 对象，只含 `path`、`type`、`tags` 三个键：
  `path` 为登记时保存的规范绝对路径（不是输入写法），`type` 为当前保存的
  类型，`tags` 为按 position 升序读出的完整标签，顺序与登记（或最近一次
  retag）顺序一致。
- 记录组装与 query/export 共用 `build_records`（cli.py:342-373），字段口径
  只有这一处；show 不附加 `file_status` 等任何额外字段。
- 查询不改变任何登记内容：再次 export 时，各记录的路径、类型、标签及首次
  登记顺序（asset.id 升序）均不变。

## 4. 失败边界与记录状态

所有失败统一为：退出码 2、标准输出为空、标准错误单行说明原因且不含调用栈。

| 失败情形 | 发生阶段 | 结果 |
|----------|----------|------|
| 路径缺失、传入不支持的参数（多余位置参数、`--tag`、`--type`、`--check-files`、`--append`、未知选项） | argparse 解析（build_parser，cli.py:34） | 退出码 2，不创建数据库 |
| 路径为空字符串 | 路径校验 ①（cli.py:542-543） | 退出码 2，不创建数据库 |
| 缺少 `--db`、数据库路径为空字符串 | 全局参数解析 / open_database（cli.py:146） | 退出码 2，不创建数据库 |
| 目标未登记 | 定位记录 ③（cli.py:552-553） | 错误含规范绝对路径；不新增素材、不改变已有记录 |
| 数据库无法打开（路径为目录、父目录缺失、无权限等） | open_database（cli.py:146） | 按既有口径拒绝，不补建缺失的父目录 |
| 数据库损坏或无法读取 | open_database / 读取阶段（cli.py:557-558） | 拒绝且不覆盖原库，不输出部分记录 |
| 结构不兼容（非本产品结构） | open_database / verify_schema（cli.py:171-198、221-229） | 拒绝且不覆盖、不重建原库 |

数据库创建时机（沿用 `open_database` 的既有规则，与 export 相同）：

- 参数错误发生在 `open_database` 之前，**不创建数据库**。
- 参数合法、数据库文件尚不存在且父目录存在时，先创建空库并初始化结构
  （create_schema，cli.py:201），再在阶段 ③ 报告目标未登记（错误含规范
  路径）。
- 父目录缺失时不补建目录，直接在打开阶段报错。

读取失败的处理：定位查询或标签读取任一步抛出 `sqlite3.Error` 时，
`handle_show` 包装为 `数据库读取失败: <数据库给出的原因>`（cli.py:557-558）。
由于唯一的 `print` 在全部读取完成之后（cli.py:563），任何读取失败都不会先
输出半截 JSON；show 不执行写操作，失败前后数据库与素材文件字节不变。

## 5. 固定样例（成功路径）

### 工作目录与路径约定

- 样例使用独立演示目录 `$SAMPLE`（下文以 `$SAMPLE` 代指），其中只有
  `A.bin`、`B.bin` 两个演示文件与数据库 `catalog.sqlite`；验收命令就在
  `$SAMPLE` 目录内执行（需保证 `asset_catalog` 包可导入，例如把项目根加入
  `PYTHONPATH`）。
- JSON 核对按解析后的内容判断，不依赖对象键序或空白。

### 准备：按 A、B 顺序登记

```bash
python -m asset_catalog --db catalog.sqlite add A.bin --type image --tag demo --tag ui
python -m asset_catalog --db catalog.sqlite add B.bin --type audio --tag demo
```

### 在演示目录查看 A

```bash
python -m asset_catalog --db catalog.sqlite show ./A.bin
```

预期：退出码 0，标准错误为空，标准输出仅一行（键序不限）：

```json
{"path": "<$SAMPLE/A.bin 的规范绝对路径>", "type": "image", "tags": ["demo", "ui"]}
```

### 查询未登记的 C（对照失败路径）

```bash
python -m asset_catalog --db catalog.sqlite show ./C.bin
```

预期：退出码 2，标准输出为空，标准错误为单行、不含调用栈，并包含 C.bin 的
规范绝对路径：

```
asset_catalog: 错误: 素材未登记: <$SAMPLE/C.bin 的规范绝对路径>
```

### 随后导出完整目录

```bash
python -m asset_catalog --db catalog.sqlite export
```

预期：成功与失败的 show 之后，导出仍按 A、B 的登记顺序返回原记录，路径、
类型、标签均不变：

```json
[
  {"path": "<$SAMPLE/A.bin 的规范绝对路径>", "type": "image", "tags": ["demo", "ui"]},
  {"path": "<$SAMPLE/B.bin 的规范绝对路径>", "type": "audio", "tags": ["demo"]}
]
```

**验证状态**：以上样例已在本仓库当前实现上实际执行（Python 3、Linux、
临时目录），退出码、标准错误为空/单行内容、show 与 export 的 JSON 内容均与
上述预期一致。对应回归测试：`tests/test_show_regression.py` 中的
`test_acceptance_relative_path_in_demo_directory`、
`test_unregistered_c_rejected_by_relative_path_in_demo_directory` 与
`test_export_unchanged_after_success_and_failure`。

## 6. 源文件状态无关性样例

- 删除已登记的 `A.bin` 后执行 `show A.bin`（或任意解析后与其规范路径相同的
  写法）：仍返回 A 的登记记录，退出码 0，不报“路径不存在”。
- 删除 `B.bin` 后在原路径创建一个同名目录再执行 `show B.bin`：仍返回 B 的
  登记记录，不报“不是普通文件”。
- 以上操作前后数据库字节与未删除素材的字节完全一致。

对应回归测试：`tests/test_show_regression.py` 中的
`test_deleted_source_still_shown_through_original_path`、
`test_directory_at_registered_path_still_shown` 与
`test_show_is_readonly_against_database_and_source`。

## 7. 回归对照

- `tests/test_show_regression.py`：验收命令与未登记 C.bin、相对/`.`/`..`
  等价写法与符号链接、源文件删除或变为目录、retag/retype 后反映当前值、
  缺参/空参/不支持参数且不建库、空库创建与父目录缺失、目录/非 SQLite/
  外业务表/缺列/截断损坏各类数据库错误、成功失败后 export 顺序不变，以及
  既有数据库与其他子命令行为不受影响，共 18 个用例。
