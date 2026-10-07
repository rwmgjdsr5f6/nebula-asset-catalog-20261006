# retype 类型修改流程说明

本文对应 `asset_catalog/cli.py` 中 retype（修改已登记素材的类型）这条流程的
实际函数，说明一次类型修改如何从参数解析走到标准输出、失败时旧记录如何完整
保留，便于逐条核对。行号对应当前版本，函数名是稳定锚点。命令参数、JSON 输出、
SQLite 结构与源文件只读规则均未改变，本文只增加说明。

## 1. 全流程总览

```
python -m asset_catalog --db DB retype PATH --type TYPE
        │  asset_catalog/__main__.py → cli.py: main()（cli.py:715）
        ▼
argparse 解析（build_parser，cli.py:34；retype 子命令定义在 cli.py:128）：
path 为位置参数、--type 必填；缺少任一或出现不支持的参数时
退出码 2、标准输出为空、不创建数据库
        ▼
handle_retype（cli.py:659）按顺序编排下列步骤：
  ① 路径处理：空路径报错；os.path.realpath 解析为规范绝对路径
     （与 add 同一规则，等价写法定位同一记录）
  ② 类型处理：--type 去除首尾空白、保留大小写，接受任意非空文本；
     去空白后为空则报错。①② 均在打开数据库之前，参数错误不建库
  ③ open_database（cli.py:140）：打开/创建数据库并校验结构
  ④ 定位记录：按规范路径 SELECT id；未登记报错（错误含规范路径）
  ⑤ 读取标签：按 position 升序读出完整标签列表，用于输出
  ⑥ 更新提交：UPDATE asset SET type 与 conn.commit() 在同一事务；
     任一步抛出 sqlite3.Error 时整体 rollback
        ▼
标准输出仅一行 JSON：{"path": ..., "type": ..., "tags": [...]}，退出码 0
```

任何阶段抛出 `CliError` 都由 `main()`（cli.py:715）统一收敛：标准错误单行
`asset_catalog: 错误: <原因>`（不含调用栈），退出码 2，标准输出为空。

## 2. 路径与类型处理（handle_retype 步骤 ①②，cli.py:662-670）

- **路径**：`os.path.realpath(args.path)` 把相对路径、`.`、`..` 与符号链接
  解析为规范绝对路径，与 add 登记时的规则相同，因此同一记录的等价写法
  （相对路径、带点写法）都能定位到它。retype 不检查源文件当前状态：
  源文件已删除或原路径已变成目录，仍可按原登记路径修改类型。
- **类型**：`args.type.strip()` 去除首尾空白、保留大小写，接受任意非空文本
  （含中文、斜杠、空格等），不按扩展名猜测。去空白后为空即参数错误。
- 两步校验都在 `open_database` 之前完成：**参数错误不创建数据库**。

## 3. 定位、读取与提交（handle_retype 步骤 ③-⑥，cli.py:672-697）

- **打开数据库**：`open_database`（cli.py:140）沿用既有口径——文件不存在
  且父目录存在时先创建空库（`create_schema`，cli.py:195）；已有 `asset` 与
  `asset_tag` 两表时由 `verify_schema`（cli.py:215）校验列结构；存在其他
  用户表或视图时判定"结构不属于本产品"并拒绝；损坏或无法打开时报
  `无法打开数据库 …` / `已损坏或无法读写 …`。不创建缺失的父目录。
- **定位记录**：`SELECT id FROM asset WHERE path = ?`（cli.py:674）按规范
  路径精确匹配；未命中时报 `素材未登记: <规范路径>`，错误包含规范路径。
- **读取标签**：`SELECT tag … ORDER BY position ASC`（cli.py:684）按登记
  顺序读出完整标签列表，只用于组装输出，不参与写入。
- **更新提交**：`UPDATE asset SET type = ? WHERE id = ?`（cli.py:693）与
  `conn.commit()` 构成同一事务。提交与当前类型相同的规范化值同样成功
  （UPDATE 照常执行，结果不变）。任一步抛出 `sqlite3.Error` 时
  `conn.rollback()`（cli.py:699）整体回滚：**旧记录完整保留，不新增素材、
  不改变其他记录、不留下部分修改**。

## 4. 成功输出与不变量

成功时退出码 0、标准错误为空，标准输出仅一行 JSON 对象，且只含
`path`、`type`、`tags` 三个键（cli.py:706-711）：

- `path` 是规范绝对路径，`type` 是去空白后的新类型，`tags` 是步骤 ⑤ 读出的
  完整标签，顺序与登记时一致；
- 只改类型：路径、完整标签及其顺序、首次登记顺序（asset.id 升序）与其他
  素材的记录保持不变；
- 操作不读取素材内容，也不创建、移动或改写源文件（源文件只读）；
- 结果持久保存：新进程的 `query --type` 按新类型命中、旧类型不再命中，
  `export` 显示新类型且顺序不变。

## 5. 失败边界一览

所有失败统一为：退出码 2、标准输出为空、标准错误单行说明原因、不含调用栈。

| 失败情形 | 发生阶段 | 结果 |
|----------|----------|------|
| 缺少路径或 `--type`、不支持的参数 | argparse 解析（build_parser，cli.py:34） | 退出码 2，不创建数据库 |
| 路径为空、`--type` 缺值或去空白后为空 | handle_retype 步骤 ①②（cli.py:662-670） | 退出码 2，不创建数据库 |
| 目标未登记 | 步骤 ④ 定位记录（cli.py:677） | 错误含规范路径；数据库不存在但父目录存在时已先创建空库，再报告未登记 |
| 数据库无法打开（如路径指向目录） | 步骤 ③ open_database（cli.py:140） | 退出码 2，不写任何内容 |
| 数据库损坏、结构不兼容 | 步骤 ③ open_database / verify_schema | 拒绝覆盖或重建，原库字节不变 |
| 父目录缺失 | 步骤 ③ open_database | 报错，不补建目录 |
| 读写失败（如触发器拒绝 UPDATE） | 步骤 ⑥ 更新提交（cli.py:693-700） | 整体回滚，旧记录完整保留，错误为 `数据库读写失败: <数据库给出的原因>` |

## 6. 固定样例（已实际执行验证）

工作目录与路径约定：命令在项目根目录执行（`python -m asset_catalog` 可解析
`asset_catalog` 包）；样例文件 `A.bin`、`B.bin` 与数据库 `catalog.sqlite`
放在同一工作目录下，命令中以其相对路径引用，JSON 中的 `path` 为对应的规范
绝对路径（下以 `<工作目录>` 代指实际绝对路径）。JSON 核对按 `json.loads`
解析后的内容判断，不依赖对象键序或空白。

准备样例（按 A、B 顺序登记；A 类型 image、标签依次为 demo、ui，B 类型
audio、标签只有 demo）：

```sh
python -m asset_catalog --db catalog.sqlite add A.bin --type image --tag demo --tag ui
python -m asset_catalog --db catalog.sqlite add B.bin --type audio --tag demo
```

执行类型修改：

```sh
python -m asset_catalog --db catalog.sqlite retype A.bin --type texture
```

预期退出码 0，标准错误为空，标准输出：

```json
{"path": "<工作目录>/A.bin", "type": "texture", "tags": ["demo", "ui"]}
```

随后导出完整目录：

```sh
python -m asset_catalog --db catalog.sqlite export
```

```json
[{"path": "<工作目录>/A.bin", "type": "texture", "tags": ["demo", "ui"]}, {"path": "<工作目录>/B.bin", "type": "audio", "tags": ["demo"]}]
```

只有 A 的类型由 image 变为 texture；导出仍按 A、B 的首次登记顺序，两条记录
的标签及顺序保持原样，B 完全不变。以上两条命令已于 2026-10-07 在 Linux
环境以 `python3 -m asset_catalog` 实际执行，输出与上述一致（路径为当时
临时目录的规范绝对路径）。

## 7. 失败样例：写入被拒时旧记录保留（已实际执行验证）

对照 `tests/test_retype_write_failure_regression.py` 的固定场景：在第 6 节
样例库上附加一个 BEFORE UPDATE 触发器作为固定拒绝条件——拒绝把类型更新为
texture，拒绝文本固定为 `retype blocked`；其他类型与读取操作不受影响。

输入（与成功样例完全相同的命令）：

```sh
python -m asset_catalog --db catalog.sqlite retype A.bin --type texture
```

结果（已于 2026-10-07 实际执行验证）：

- 退出码 2，标准输出为空；
- 标准错误单行：`asset_catalog: 错误: 数据库读写失败: retype blocked`
  ——同时包含"数据库读写失败"与数据库给出的 `retype blocked`，不含调用栈；
- 事务整体回滚，随后新进程 `export` 仍按 A、B 顺序返回原记录：

```json
[{"path": "<工作目录>/A.bin", "type": "image", "tags": ["demo", "ui"]}, {"path": "<工作目录>/B.bin", "type": "audio", "tags": ["demo"]}]
```

A 的类型仍为 image，标签仍为 demo、ui，不留下部分修改；两个源文件字节不变。
同一测试还验证了撤去拒绝条件（DROP TRIGGER）后用同一输入再次提交即成功，
结果与第 6 节一致。对应测试位置：
`tests/test_retype_write_failure_regression.py` 的
`test_retype_blocked_keeps_old_record_then_succeeds_when_condition_removed`。

## 8. 核对方式说明

- 第 6、7 节标注"已实际执行验证"的命令与输出来自真实运行；文中其余结论
  （如各失败情形的发生阶段、参数错误不建库）为对当前源码的静态推导，
  并由第 9 节列出的回归测试覆盖，未逐一在此重复执行。
- 行号对应当前版本 `asset_catalog/cli.py`，函数名（`main`、`build_parser`、
  `handle_retype`、`open_database`、`create_schema`、`verify_schema`）是
  稳定锚点；行号漂移时按函数名定位。

## 9. 回归对照

- `tests/test_retype_regression.py`：成功修改、路径等价写法、同类型重复
  提交、源文件删除或变为目录、参数错误不建库、未登记报错含规范路径、
  空库先建再报未登记、父目录缺失不补建、数据库打不开/损坏/结构不兼容；
- `tests/test_retype_write_failure_regression.py`：类型写入被触发器拒绝时
  整体回滚、旧记录完整保留，撤去拒绝条件后同一输入成功。
