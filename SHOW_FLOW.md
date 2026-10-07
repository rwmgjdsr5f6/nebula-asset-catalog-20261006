# show 按路径查看单条记录流程说明

本文对应 `asset_catalog/cli.py` 中 show（按素材路径查看单条已登记记录）这条
流程的实际函数，说明记录如何定位与组装、失败时为何不输出部分结果，便于逐条
核对。行号对应当前版本，函数名是稳定锚点。show 为只读操作：不新增参数语义
之外的副作用，命令参数风格、JSON 输出字段、SQLite 结构与源文件只读规则均与
既有流程一致。可选开关 `--check-files` 只在命中记录上追加当前文件状态，状态
定义与 query 共用同一处 `check_file_status`，不增加筛选，也不存储状态。

## 1. 全流程总览

```
python -m asset_catalog --db DB show PATH [--check-files]
        │  asset_catalog/__main__.py → cli.py: main()（cli.py:771）
        ▼
argparse 解析（build_parser，cli.py:34；show 子命令定义在 cli.py:105-117）：
path 为唯一定位参数，--check-files 为可选 store_true 开关；缺参或出现不支持
的参数（含 --tag 等其他子命令选项）时退出码 2、标准输出为空、不创建数据库
        ▼
handle_show（cli.py:545）按顺序编排下列步骤：
  ① 路径校验与规范化（cli.py:550-552）：空路径报错；
     os.path.realpath 解析为规范绝对路径（与 add 同一规则），
     该校验在打开数据库之前，参数错误不建库
  ② open_database（cli.py:146）：打开/创建并校验结构
  ③ 定位记录（cli.py:556-563）：按规范路径查 asset 表；未登记报错
     （错误含规范路径）——先定位记录，文件状态检查在此之后
  ④ 组装记录（cli.py:566）：复用 build_records（cli.py:342）按
     position 升序读出完整标签，取唯一一条记录
  ⑤ 可选文件状态（cli.py:572-579）：仅当传入 --check-files 时，对记录
     保存路径调用 check_file_status（cli.py:323，与 query 同一实现）：
     present / missing / not_file；权限或其他系统错误在此抛出 CliError
        ▼
标准输出仅一行 JSON：
- 默认：{"path": ..., "type": ..., "tags": [...]}，退出码 0
- 带 --check-files：{"path": ..., "type": ..., "tags": [...],
  "file_status": ...}，退出码 0（cli.py:582-583）
```

任何阶段抛出 `CliError` 都由 `main()`（cli.py:771-778）统一收敛：标准错误
单行 `asset_catalog: 错误: <原因>`（不含调用栈），退出码 2，标准输出为空。

## 2. 路径处理规则

- **路径**（cli.py:550-552）：`os.path.realpath` 解析相对路径、`.`/`..` 与
  符号链接，得到规范绝对路径；相对路径、含 `.` 或 `..` 的等价写法，以及解析
  后指向同一登记路径的符号链接，都定位同一条登记记录。空字符串在打开数据库
  之前即报错（cli.py:550-551），不触发建库。
- **默认不检查文件状态**：不带 `--check-files` 时，show 只在数据库中按规范
  路径匹配，不调用 `os.path.exists`/`os.stat`，也不读取素材内容。源文件已
  删除或原登记路径变成目录时，通过原登记路径仍返回该记录；断开的符号链接
  只有在其规范路径恰好已登记时才命中，否则按未登记处理。
- **`--check-files` 检查记录保存路径**：传入开关时，先在阶段 ③ 按规范路径
  定位登记记录，再对**记录中保存的规范路径**（不是输入写法）调用
  `check_file_status`。未登记路径无论在文件系统上是否存在都在阶段 ③ 按未
  登记失败，不会先检查状态。等价路径写法与符号链接的定位语义与不带开关时
  完全相同。
- **状态定义与 query 一致**（check_file_status，cli.py:323-339）：普通文件
  为 `present`；路径不存在、断开的符号链接或中间组件不是目录为 `missing`；
  目录等非普通文件为 `not_file`；符号链接按其目标判断。只 `os.stat` 读取
  状态，不读取素材内容。
- **只读**：整个流程只对数据库执行 SELECT，不写入数据库，也不创建、移动或
  改写源文件。

## 3. 成功时的可核对结果

- 退出码 0，标准错误为空。
- 默认标准输出仅一行 JSON 对象，只含 `path`、`type`、`tags` 三个键：
  `path` 为登记时保存的规范绝对路径（不是输入写法），`type` 为当前保存的
  类型，`tags` 为按 position 升序读出的完整标签，顺序与登记（或最近一次
  retag）顺序一致。
- 传入 `--check-files` 时，同一行 JSON 对象在原字段之外追加一个
  `file_status` 键，取值为 `present` / `missing` / `not_file`，反映命令执行
  当下记录保存路径的状态；源文件删除、换成目录、恢复普通文件后再次查看会
  分别得到 `missing`、`not_file`、`present`，无需重新登记。
- 记录组装与 query/export 共用 `build_records`（cli.py:342-373），字段口径
  只有这一处；文件状态与 query 的 `--check-files` 共用 `check_file_status`
  （cli.py:323-339）。不带开关时不附加 `file_status` 等任何额外字段。
- 查询不改变任何登记内容：再次 export 时，各记录的路径、类型、标签及首次
  登记顺序（asset.id 升序）均不变。

## 4. 失败边界与记录状态

所有失败统一为：退出码 2、标准输出为空、标准错误单行说明原因且不含调用栈。

| 失败情形 | 发生阶段 | 结果 |
|----------|----------|------|
| 路径缺失、传入不支持的参数（多余位置参数、`--tag`、`--type`、`--append`、未知选项；`--check-files` 已被 show 接受） | argparse 解析（build_parser，cli.py:34） | 退出码 2，不创建数据库 |
| 路径为空字符串 | 路径校验 ①（cli.py:550-551） | 退出码 2，不创建数据库 |
| 缺少 `--db`、数据库路径为空字符串 | 全局参数解析 / open_database（cli.py:146） | 退出码 2，不创建数据库 |
| 目标未登记（无论路径是否存在、是否带 `--check-files`） | 定位记录 ③（cli.py:560-563） | 错误含规范绝对路径；不新增素材、不改变已有记录 |
| 命中记录的状态无法读取（权限或其他系统错误，仅 `--check-files`） | 文件状态 ⑤（cli.py:572-579） | 标准错误说明状态读取失败并指出记录保存路径；不输出部分 JSON |
| 数据库无法打开（路径为目录、父目录缺失、无权限等） | open_database（cli.py:146） | 按既有口径拒绝，不补建缺失的父目录 |
| 数据库损坏或无法读取 | open_database / 读取阶段（cli.py:567-568） | 拒绝且不覆盖原库，不输出部分记录 |
| 结构不兼容（非本产品结构） | open_database / verify_schema（cli.py:171-198、221-229） | 拒绝且不覆盖、不重建原库 |

数据库创建时机（沿用 `open_database` 的既有规则，与 export 相同）：

- 参数错误发生在 `open_database` 之前，**不创建数据库**。
- 参数合法、数据库文件尚不存在且父目录存在时，先创建空库并初始化结构
  （create_schema，cli.py:201），再在阶段 ③ 报告目标未登记（错误含规范
  路径）。
- 父目录缺失时不补建目录，直接在打开阶段报错。

读取失败的处理：定位查询或标签读取任一步抛出 `sqlite3.Error` 时，
`handle_show` 包装为 `数据库读取失败: <数据库给出的原因>`（cli.py:567-568）。
状态读取的系统错误由 `check_file_status` 包装为
`无法确定文件状态: <规范路径>: <系统原因>`（cli.py:335-336）。由于唯一的
`print` 在全部读取与状态检查完成之后（cli.py:583），任何失败都不会先输出
半截 JSON；show 不执行写操作，失败前后数据库与素材文件字节不变。

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

### 查看 A 并附带当前文件状态（--check-files）

```bash
python -m asset_catalog --db catalog.sqlite show ./A.bin --check-files
```

源文件存在且为普通文件时，预期为（键序不限）：

```json
{"path": "<$SAMPLE/A.bin 的规范绝对路径>", "type": "image", "tags": ["demo", "ui"], "file_status": "present"}
```

删除 `A.bin` 后同一命令返回 `file_status` 为 `missing`；在原位置创建同名
目录后返回 `not_file`；恢复普通文件后回到 `present`——均无需重新登记。
不带开关的 `show A.bin` 在上述各状态下始终只返回 `path`、`type`、`tags`。

### 查询未登记的 C（对照失败路径）

```bash
python -m asset_catalog --db catalog.sqlite show ./C.bin
```

预期：退出码 2，标准输出为空，标准错误为单行、不含调用栈，并包含 C.bin 的
规范绝对路径：

```
asset_catalog: 错误: 素材未登记: <$SAMPLE/C.bin 的规范绝对路径>
```

追加 `--check-files` 结果相同：先定位登记记录，未登记路径无论文件是否存在
都不检查状态，按同一口径失败。

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
- 上述“状态无关”仅指不带开关的默认行为；同一记录加 `--check-files` 后会
  分别报告 `missing` / `not_file`（见第 5 节），该状态每次实时读取、不入库。

默认行为对应回归测试：`tests/test_show_regression.py` 中的
`test_deleted_source_still_shown_through_original_path`、
`test_directory_at_registered_path_still_shown` 与
`test_show_is_readonly_against_database_and_source`。

## 7. 回归对照

- `tests/test_show_regression.py`：验收命令与未登记 C.bin、相对/`.`/`..`
  等价写法与符号链接、源文件删除或变为目录、retag/retype 后反映当前值、
  缺参/空参/不支持参数且不建库、空库创建与父目录缺失、目录/非 SQLite/
  外业务表/缺列/截断损坏各类数据库错误、成功失败后 export 顺序不变，以及
  既有数据库与其他子命令行为不受影响，共 18 个用例。
- `tests/test_show_check_files_regression.py`：`show --check-files` 的专项
  回归——固定样例删除/换目录/恢复的 missing、not_file、present 序列（无需
  重新登记），普通文件与符号链接按目标判断，断开的链接与中间组件不是目录为
  missing，未登记路径存在与否都按未登记失败，等价写法定位语义不变，权限错误
  （sitecustomize 垫片固定 PermissionError）时退出码 2、空标准输出、错误含
  路径且不传开关时不受影响，只检查命中记录路径、只读不改库不改源文件、标签
  与登记顺序不变，以及其他子命令行为保持原样。
