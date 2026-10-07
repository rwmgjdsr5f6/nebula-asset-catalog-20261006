# show 按路径查看单条记录流程说明

本文对应 `asset_catalog/cli.py` 中 show（按素材路径查看单条已登记记录）这条
流程的实际函数，说明记录如何定位与组装、可选的 `--check-files` 如何在命中后
检查文件状态、失败时为何不输出部分结果，便于逐条核对。行号对应当前版本，
函数名是稳定锚点。show 为只读操作：不新增参数语义之外的副作用，命令参数
风格、JSON 输出字段、SQLite 结构与源文件只读规则均与既有流程一致。

## 1. 全流程总览

```
python -m asset_catalog --db DB show PATH [--check-files]
        │  asset_catalog/__main__.py → cli.py: main()（cli.py:764）
        ▼
argparse 解析（build_parser，cli.py:34；show 子命令定义在 cli.py:105-114）：
path 为唯一定位参数，--check-files 为可选开关；缺参或出现不支持的参数
（含 --tag、--file-status 等其他子命令选项）时退出码 2、标准输出为空、
不创建数据库
        ▼
handle_show（cli.py:542）按顺序编排下列步骤：
  ① 路径校验与规范化（cli.py:547-549）：空路径报错；
     os.path.realpath 解析为规范绝对路径（与 add 同一规则），
     该校验在打开数据库之前，参数错误不建库
  ② open_database（cli.py:151）：打开/创建并校验结构
  ③ 定位记录（cli.py:553-558）：按规范路径查 asset 表；未登记报错
     （错误含规范路径），未登记路径不检查文件状态
  ④ 组装记录（cli.py:561）：复用 build_records（cli.py:347）按
     position 升序读出完整标签，取唯一一条记录
  ⑤ 可选文件状态检查（cli.py:567-572）：仅当传入 --check-files 时，
     对记录保存路径调用 check_file_status（cli.py:328）追加 file_status；
     状态读取失败抛 CliError，此前不输出任何内容
        ▼
标准输出仅一行 JSON：
- 默认：{"path": ..., "type": ..., "tags": [...]}，退出码 0（cli.py:576-577）
- --check-files：{"path": ..., "type": ..., "tags": [...],
  "file_status": "present|missing|not_file"}，退出码 0
```

任何阶段抛出 `CliError` 都由 `main()`（cli.py:767-770）统一收敛：标准错误
单行 `asset_catalog: 错误: <原因>`（不含调用栈），退出码 2，标准输出为空。

## 2. 路径处理规则

- **路径**（cli.py:547-549）：`os.path.realpath` 解析相对路径、`.`/`..` 与
  符号链接，得到规范绝对路径；相对路径、含 `.` 或 `..` 的等价写法，以及解析
  后指向同一登记路径的符号链接，都定位同一条登记记录。空字符串在打开数据库
  之前即报错（cli.py:547-548），不触发建库。
- **先定位记录，再检查状态**：show 始终先按规范路径在数据库中匹配（步骤 ③）。
  未登记路径无论文件是否存在都在步骤 ③ 报“素材未登记”（含规范路径），不会
  对其执行文件状态检查；断开的符号链接按其链接目标解析，只有解析后的规范
  路径恰好已登记时才命中，否则按未登记处理。
- **默认不检查文件状态**：不传 `--check-files` 时不调用
  `os.path.exists`/`os.stat`，也不读取素材内容。源文件已删除或原登记路径
  变成目录时，通过原登记路径仍返回该记录。
- **--check-files 的状态定义**（check_file_status，cli.py:328-344，与 query
  共用同一函数）：对**记录保存路径**（步骤 ④ 得到的 `path`，即登记时保存的
  规范绝对路径）调用 `os.stat`，按 stat 结果判断——
  - 普通文件（`stat.S_ISREG`）为 `present`；
  - `os.stat` 抛出 `FileNotFoundError`/`NotADirectoryError`（路径本身不存在、
    断开的符号链接或中间组件不是目录）为 `missing`；
  - 其余对象（目录等非普通文件）为 `not_file`；
  - 符号链接由 `os.stat` 按其指向的目标判断，断链归为 `missing`。
- **只读**：整个流程只对数据库执行 SELECT，不写入数据库；状态检查只读取
  inode 状态，不读取素材内容，也不创建、移动或改写源文件。

## 3. 成功时的可核对结果

- 退出码 0，标准错误为空。
- 默认（不传 `--check-files`）标准输出仅一行 JSON 对象，只含 `path`、`type`、
  `tags` 三个键。
- 传入 `--check-files` 时标准输出仍仅一行 JSON 对象，在原字段之外增加
  `file_status`（共四个键，无其他字段）：`path` 为登记时保存的规范绝对路径
  （不是输入写法），`type` 为当前保存的类型，`tags` 为按 position 升序读出
  的完整标签，顺序与登记（或最近一次 retag）顺序一致；`file_status` 反映
  **本次查看时**记录保存路径的当前状态，不保存历史状态，文件恢复或变化后
  重新查看即得到新状态，无需重新登记。
- 记录组装与 query/export 共用 `build_records`（cli.py:347-378），字段口径
  只有这一处；状态判定与 query/--file-status 共用 `check_file_status`
  （cli.py:328-344），状态取值与含义完全一致。
- 查看不改变任何登记内容：再次 export 时，各记录的路径、类型、标签及首次
  登记顺序（asset.id 升序）均不变。

## 4. 失败边界与记录状态

所有失败统一为：退出码 2、标准输出为空、标准错误单行说明原因且不含调用栈。

| 失败情形 | 发生阶段 | 结果 |
|----------|----------|------|
| 路径缺失、传入不支持的参数（多余位置参数、`--tag`、`--type`、`--file-status`、`--append`、未知选项） | argparse 解析（build_parser，cli.py:34） | 退出码 2，不创建数据库 |
| 路径为空字符串 | 路径校验 ①（cli.py:547-548） | 退出码 2，不创建数据库 |
| 缺少 `--db`、数据库路径为空字符串 | 全局参数解析 / open_database（cli.py:151） | 退出码 2，不创建数据库 |
| 目标未登记（无论文件是否存在，含传入 `--check-files`） | 定位记录 ③（cli.py:557-558） | 错误含规范绝对路径；不检查其文件状态；不新增素材、不改变已有记录 |
| 命中记录的状态无法读取（权限不足或其他系统错误），仅 `--check-files` 时 | 状态检查 ⑤（cli.py:567-572 → check_file_status cli.py:340-341） | 错误为“无法确定文件状态”并指出相关路径；不输出部分 JSON |
| 数据库无法打开（路径为目录、父目录缺失、无权限等） | open_database（cli.py:151） | 按既有口径拒绝，不补建缺失的父目录 |
| 数据库损坏或无法读取 | open_database / 读取阶段（cli.py:562-563） | 拒绝且不覆盖原库，不输出部分记录 |
| 结构不兼容（非本产品结构） | open_database（cli.py:151-203）/ verify_schema（cli.py:226-234） | 拒绝且不覆盖、不重建原库 |

数据库创建时机（沿用 `open_database` 的既有规则，与 export 相同）：

- 参数错误发生在 `open_database` 之前，**不创建数据库**。
- 参数合法、数据库文件尚不存在且父目录存在时，先创建空库并初始化结构
  （create_schema，cli.py:206），再在阶段 ③ 报告目标未登记（错误含规范
  路径）；传入 `--check-files` 时同样如此——未登记路径不触发状态检查。
- 父目录缺失时不补建目录，直接在打开阶段报错。

读取失败的处理：定位查询或标签读取任一步抛出 `sqlite3.Error` 时，
`handle_show` 包装为 `数据库读取失败: <数据库给出的原因>`（cli.py:562-563）。
文件状态读取失败时由 `check_file_status` 包装为
`无法确定文件状态: <记录保存路径>: <系统原因>`（cli.py:340-341）。由于唯一
的 `print` 在全部读取与状态检查完成之后（cli.py:576），任何失败都不会先
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

## 6. 源文件状态无关性样例（默认）与 --check-files 样例

### 6.1 默认行为：源文件状态不影响记录返回

- 删除已登记的 `A.bin` 后执行 `show A.bin`（或任意解析后与其规范路径相同的
  写法）：仍返回 A 的登记记录，退出码 0，不报“路径不存在”。
- 删除 `B.bin` 后在原路径创建一个同名目录再执行 `show B.bin`：仍返回 B 的
  登记记录，不报“不是普通文件”。
- 以上操作前后数据库字节与未删除素材的字节完全一致。

对应回归测试：`tests/test_show_regression.py` 中的
`test_deleted_source_still_shown_through_original_path`、
`test_directory_at_registered_path_still_shown` 与
`test_show_is_readonly_against_database_and_source`。

### 6.2 --check-files：已登记但源文件已不存在的 A.bin

固定样例：A 已登记（类型 `image`，标签顺序 `demo`、`ui`），登记后删除源文件
`A.bin`。在演示目录依次执行：

```bash
# 带选项：原元数据之外报告当前状态 missing
python -m asset_catalog --db catalog.sqlite show A.bin --check-files
# 随后不带选项：相同元数据，没有状态字段
python -m asset_catalog --db catalog.sqlite show A.bin
```

依次预期（退出码均为 0、标准错误为空、标准输出各一行）：

```json
{"path": "<$SAMPLE/A.bin 的规范绝对路径>", "type": "image", "tags": ["demo", "ui"], "file_status": "missing"}
{"path": "<$SAMPLE/A.bin 的规范绝对路径>", "type": "image", "tags": ["demo", "ui"]}
```

随后在原位置创建同名目录，再恢复为普通文件（均不重新登记）：

```bash
mkdir A.bin
python -m asset_catalog --db catalog.sqlite show A.bin --check-files   # not_file
rmdir A.bin
printf 'demo asset A\n' > A.bin
python -m asset_catalog --db catalog.sqlite show A.bin --check-files   # present
```

预期两次查看分别返回 `"file_status": "not_file"` 与 `"file_status": "present"`，
其余字段始终是 A 的原规范路径、`image` 与 `["demo", "ui"]`。

其他边界：

- 普通文件存在时为 `present`；`--check-files` 放在路径前后均可。
- 登记路径的中间组件被替换为普通文件（不再是目录）时为 `missing`，记录仍按
  其登记的规范路径命中；不带选项时照常返回记录。
- 登记路径处替换为指向**另一条已登记普通文件**的符号链接时，按链接目标的
  规范路径定位目标记录，并按目标（普通文件）报告 `present`，输出 `path` 为
  目标记录保存的规范路径；断开的链接解析到未登记路径时按未登记失败。
- 未登记路径无论文件是否存在，`--check-files` 都退出码 2、标准输出为空、
  标准错误说明素材未登记并包含规范路径。
- 对命中记录保存路径的状态读取注入 `PermissionError` 时（子进程内确定性
  注入）：退出码 2、标准输出为空，标准错误包含“无法确定文件状态”与该路径、
  不含调用栈；失败后不带选项的 show 元数据不变。
- 其他素材的文件状态不影响本次查看；查看前后数据库字节与未改动素材的字节
  完全一致。

对应回归测试：`tests/test_show_file_status_regression.py`（13 个用例）。

## 7. 回归对照

- `tests/test_show_regression.py`：验收命令与未登记 C.bin、相对/`.`/`..`
  等价写法与符号链接、源文件删除或变为目录（默认仍返回记录）、retag/retype
  后反映当前值、缺参/空参/不支持参数且不建库（`--check-files` 现为合法开关，
  `--file-status` 等其他子命令选项仍被拒绝）、空库创建与父目录缺失、
  目录/非 SQLite/外业务表/缺列/截断损坏各类数据库错误、成功失败后 export
  顺序不变，以及既有数据库与其他子命令行为不受影响。
- `tests/test_show_file_status_regression.py`：`show --check-files` 的
  missing/not_file/present 验收序列（删除→目录→恢复，无需重新登记）、
  present 与开关位置、中间组件替换、断链与指向其他已登记文件的符号链接、
  未登记路径无论是否存在均失败、权限错误不输出部分 JSON、其他素材状态
  无关、数据库与源文件只读、空库创建与父目录缺失口径不变。
