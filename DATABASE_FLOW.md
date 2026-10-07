# 默认导出的数据库打开流程说明

本文围绕一次默认导出 `python -m asset_catalog --db DB_FILE export`，说明
`--db` 指向的输入文件在什么条件下会被**初始化**、**沿用**或**拒绝**：从公开
入口的参数处理讲到 `open_database` 的结构判定，再到导出结果与错误输出，并为
每个结论标注实际源码位置与函数名（行号对应当前版本的 `asset_catalog/cli.py`，
函数名是稳定锚点）。

本文只描述现有行为，不改变任何命令、输出格式与数据库兼容规则，也不新增执行
脚本；除本说明外不交付代码改动。其他子命令（add、query、show、retag、retype、
tags）同样经过 `open_database`，打开判定完全一致，但本文只以 `export` 为主线
串起公开结果。

## 1. 全流程总览

```
python -m asset_catalog --db DB_FILE export
        │  asset_catalog/__main__.py → cli.py: main()（cli.py:845）
        ▼
argparse 解析（build_parser，cli.py:35）：
--db 必填（cli.py:37-42）；export 子命令只定义了可选 --check-files
（cli.py:101-109）。缺 --db、子命令传入不支持的参数等在解析阶段即
退出码 2、标准输出为空，此时尚未打开或创建任何数据库文件
        ▼
handle_export（cli.py:534）编排：
  ① open_database(args.db)（cli.py:538，定义于 cli.py:166-218）：
     打开输入文件，按 sqlite_master 清点结果三选一——
       · 两张实体表 asset、asset_tag 都在 → verify_schema 仅核对列名集合，
         通过则沿用（cli.py:197-198、241-249）
       · 两表不全但存在任意用户表或用户视图 → 拒绝（cli.py:199-204）
       · 既无用户表也无用户视图 → create_schema 初始化为空目录库
         （cli.py:205-208、221-238）
     connect/清点阶段打不开（父目录缺失、路径为目录、非 SQLite、损坏等）
     或后续 I/O 失败 → 包装为 CliError（cli.py:186-189、209-211）
  ② SELECT id, path, type FROM asset ORDER BY id ASC（cli.py:540-545）
  ③ build_records 补全每条记录按 position 升序的完整标签（cli.py:547、
     362-393）；默认不带 --check-files 时完全不访问素材文件
        ▼
print(json.dumps(result, ensure_ascii=False))   # 单行 JSON 数组
退出码 0；成功时标准错误为空；空目录输出 []（cli.py:565-566）
```

任何阶段抛出 `CliError` 都由 `main()`（cli.py:845-852）统一收敛：向标准错误
输出单行 `asset_catalog: 错误: <原因>`，返回退出码 2，此前标准输出为空，且不含
调用栈。`EXIT_OK = 0`、`EXIT_ERROR = 2` 定义在 cli.py:16-17。

## 2. 公开入口与参数处理

- `python -m asset_catalog` 执行 `asset_catalog/__main__.py`，其中
  `sys.exit(main())` 进入 `cli.main`（cli.py:845）。
- `main()` 先调用 `build_parser()`（cli.py:35）解析参数：
  - `--db` 为全局必填选项，元变量 `DB_FILE`，帮助文本即写明“不存在时自动
    创建文件”（cli.py:37-42）；
  - `export` 子命令只挂载了一个可选开关 `--check-files`（cli.py:101-109），
    不接受 `--tag`、`--type`、位置参数等任何其他参数。
- argparse 层面的参数错误由 `_Parser.error`（cli.py:30-32）处理：标准错误先
  打印用法行，再给 `asset_catalog: 错误: ...` 单行原因，退出码 2、标准输出
  为空。参数错误全部发生在 `open_database` 之前，**不会创建数据库文件**：
  - 缺少 `--db`：退出码 2，标准错误含用法行与
    `the following arguments are required: --db`；
  - `--db` 显式传空字符串时通过参数解析，但在 `open_database` 入口即被
    拦截：`数据库路径不能为空`（cli.py:171-172），退出码 2、标准输出为空；
  - `export` 传入不支持的参数（如 `--tag demo`、多余位置参数）时在解析
    阶段拒绝，退出码 2，不触碰 `--db` 指向的文件。

## 3. open_database 的输入判定

`open_database(db_path)`（cli.py:166-218）是所有子命令共用的唯一入口，判定
只依据一次 `sqlite_master` 清点（cli.py:182-185）：

```sql
SELECT type, name FROM sqlite_master WHERE type IN ('table', 'view')
```

清点结果被分为两个集合（cli.py:191-194）：

- `table_names`：全部表名；再剔除内部表集合 `{"sqlite_sequence"}` 得到
  `user_tables`——**`sqlite_sequence` 是 SQLite 为 AUTOINCREMENT 自动维护的
  内部表，不计作用户表**；
- `view_names`：全部用户视图名。清点用户对象时表与视图并列统计，因为视图
  可以只由常量表达式构成（如 `CREATE VIEW ... AS SELECT 'keep' AS note`）
  而不依赖任何表，“没有用户表”不等于“没有已有业务内容”（注释见
  cli.py:178-181）。

随后三选一（cli.py:196-208）：

| 条件 | 处理 | 源码位置 |
|------|------|----------|
| `asset` 与 `asset_tag` 两张实体表都在 `table_names` 中 | 调 `verify_schema` 核对列名集合，通过则**沿用**该连接 | cli.py:197-198 |
| 两表不全，但 `user_tables` 或 `view_names` 非空 | 抛 `数据库 <db_path> 结构不属于本产品，拒绝覆盖或重建`，**拒绝** | cli.py:199-204 |
| 既无用户表也无用户视图 | 调 `create_schema` **初始化**为空目录库 | cli.py:205-208 |

被拒绝时 `except CliError` 分支只关闭连接并原样抛出，不做任何写入，保证被
拒绝的文件不留下日志或页缓存改动（cli.py:212-216）。

### 3.1 三种会被初始化为空目录的形态

以下三种形态都落入“既无用户表也无用户视图”分支，由 `create_schema`
（cli.py:221-238）在同一文件内创建 `asset` 表、`asset_tag` 表与索引
`idx_asset_tag_tag` 并提交；随后的 `export` 查得零行，成功输出 `[]`：

1. **父目录存在而数据库文件尚不存在**：`sqlite3.connect` 延迟到首次 I/O
   才创建文件，清点 `sqlite_master` 时得到空结果，随即初始化。这是帮助文本
   承诺的“不存在时自动创建文件”（cli.py:41）。
2. **已存在的零字节文件**：SQLite 把零字节文件视为全新空库，清点结果同样
   为空，在原路径上初始化为空目录库（不改变文件路径）。
3. **没有用户表也没有用户视图的空 SQLite 库**：包括
   - 带有效 SQLite 文件头但 `sqlite_master` 完全为空的库（例如建连后仅
     执行过 `VACUUM`）；
   - 只含内部表 `sqlite_sequence` 的库（曾建过 AUTOINCREMENT 表又删除后的
     残留）——它被内部表集合剔除，`user_tables` 与 `view_names` 均为空。

初始化后该文件就是一个可再次打开读取的空目录库：新进程再次执行同一
`export` 仍退出码 0、标准错误为空、输出 `[]`；之后也可以正常 `add` 登记并由
新进程读回。

### 3.2 沿用兼容目录库：只核对列名集合

两张所需实体表都存在时，`verify_schema`（cli.py:241-249）执行的全部校验是：

```python
asset_cols = {row[1] for row in conn.execute("PRAGMA table_info(asset)")}
tag_cols   = {row[1] for row in conn.execute("PRAGMA table_info(asset_tag)")}
if asset_cols != ASSET_COLUMNS or tag_cols != TAG_COLUMNS:
    raise CliError("数据库表结构与本产品不兼容，拒绝覆盖或重建")
```

其中 `ASSET_COLUMNS = {"id", "path", "type"}`、
`TAG_COLUMNS = {"asset_id", "tag", "position"}`（cli.py:19-20）。因此：

- 校验的只是两表**列名构成的集合是否恰好相等**（多列、少列、换名都算
  不符）；**不验证字段类型、`NOT NULL`/`UNIQUE` 等约束、外键或索引**，也
  不检查表的行数据；
- 库中**附加的用户表、用户视图或触发器不会单独导致拒绝**：清点后两表齐全
  即走 `verify_schema`，校验范围只限这两张表，附加对象保持原样并继续可独立
  查询；它们同样不影响 `export` 的输出口径。

### 3.3 只含用户视图的库不能按空库初始化

用户视图计入“已有业务内容”，所以一个只有视图、没有任何用户表的 SQLite 文件
**不满足空库条件**，即使视图名为 `asset` 或 `asset_tag` 也不能代替同名实体
表：判定要求的是两表都在 `table_names` 中（视图只进入 `view_names`），故
落入“有用户对象却不具备两张兼容表”的拒绝分支，错误为
`数据库 <db_path> 结构不属于本产品，拒绝覆盖或重建`。视图数量与视图名均不
改变结论；拒绝时不在其中补建素材表与标签表，原视图定义与查询结果保留。

## 4. 拒绝分支与公开结果

下列分支的公开结果完全一致：**退出码 2、标准输出为空、标准错误单行说明原因
且不含调用栈**。拒绝其他用途或不兼容库时**原文件字节保留**，不覆盖、不补表、
不重建，也不留下 `-journal`/`-wal`/`-shm` 等侧车文件；再次调用得到相同错误。

| 输入情形 | 判定阶段（源码位置） | 标准错误中的原因文案 |
|----------|----------------------|----------------------|
| 缺少所需表却存在用户对象（只有其他业务表；只有 `asset` 一张表；只有用户视图，含视图名为 `asset`/`asset_tag`；表与视图混存但两表不全） | cli.py:199-204 | `数据库 <db_path> 结构不属于本产品，拒绝覆盖或重建`（含传入的数据库路径） |
| 两张实体表都在，但任一表列名集合与规定不符（多列、少列、换名） | verify_schema，cli.py:241-249 | `数据库表结构与本产品不兼容，拒绝覆盖或重建` |
| 文件不是 SQLite 数据库（如纯文本） | connect 后首次清点即失败，cli.py:182-189 | `无法打开数据库 <db_path>: file is not a database` |
| 文件是损坏的 SQLite 镜像（如截断） | 同上；清点阶段报 `database disk image is malformed` | `无法打开数据库 <db_path>: <数据库给出的原因>` |
| `--db` 路径指向一个目录 | 首次 I/O 报 `unable to open database file`，cli.py:186-189 | `无法打开数据库 <db_path>: unable to open database file` |
| 父目录缺失 | 同上；**不补建任何缺失目录** | `无法打开数据库 <db_path>: unable to open database file` |
| 结构判定之后的建表/校验等 I/O 失败 | cli.py:209-211 | `数据库 <db_path> 已损坏或无法读写: <数据库给出的原因>` |
| `--db` 为空字符串 | open_database 入口，cli.py:171-172 | `数据库路径不能为空` |

文案中的 `<db_path>` 是命令行收到的数据库路径原文（相对路径就原样出现在
错误中）。标准错误统一由 `main()` 加上 `asset_catalog: 错误: ` 前缀
（cli.py:850-851）。

两个错误包装分支的边界（cli.py:186-189 与 cli.py:209-211）：`sqlite3.connect`
与 `sqlite_master` 清点这一步抛出的 `sqlite3.Error` 归为“无法打开数据库”；
进入结构分支后，`create_schema`/`verify_schema` 等后续 I/O 抛出的
`sqlite3.Error` 归为“已损坏或无法读写”。`verify_schema` 抛出的列名不符是
`CliError`，走 cli.py:212-216 原样上抛，文案固定为“表结构与本产品不兼容”。

## 5. 沿用或初始化成功后的导出结果

`handle_export`（cli.py:534-566）在成功拿到连接后：

1. `SELECT id, path, type FROM asset ORDER BY id ASC`（cli.py:540-545）：
   按 `asset.id` 升序，即**首次登记顺序**取出全部素材，无任何筛选；
2. `build_records`（cli.py:362-393）为每条素材按 `position` 升序补全完整
   标签，每条记录只出现一次、只含 `path`、`type`、`tags` 三个键；空表时
   返回 `[]`，不访问标签表；
3. 默认（不传 `--check-files`）不检查登记路径的文件状态、不读取素材内容
   （cli.py:535-537）：**源文件事后被删除或原路径变成目录都不影响记录
   返回**；
4. `print(json.dumps(result, ensure_ascii=False))` 输出单行 JSON 数组
   （cli.py:565），退出码 0、标准错误为空；空目录库输出 `[]`。

因此新初始化的空目录库导出结果必然是 `[]`；既有兼容库则按登记顺序返回完整
元数据。导出为只读操作，不改动数据库与素材文件。

## 6. 固定对照样例

以下两个样例在现有演示目录（项目根，含 `demo.png` 与 `asset_catalog` 包）
中执行；JSON 按解析后的内容核对，不依赖空白。环境中的 Python 解释器命令名
可能是 `python` 或 `python3`，二者等价。

### 样例一：尚不存在的 fresh.sqlite —— 初始化为空目录库

**准备状态**：演示目录中不存在 `fresh.sqlite`，其父目录（当前目录）存在。

**完整命令**：

```bash
python -m asset_catalog --db fresh.sqlite export
```

**预期结果**：

- 退出码 0；
- 标准错误为空；
- 标准输出为单行：

```json
[]
```

- 命令后在该路径**留下一个可再次读取的空目录库**：文件已创建，库中含实体
  表 `asset`、`asset_tag`（以及 AUTOINCREMENT 对应的内部表
  `sqlite_sequence`，它不是用户表），无任何素材记录；
- 再次执行同一命令，仍退出码 0、标准错误为空、输出 `[]`。

### 样例二：只含一个视图的 foreign.sqlite —— 原样拒绝

**准备状态**：演示目录中的 `foreign.sqlite` 是一个有效 SQLite 文件，唯一的
业务对象是以下视图（建样例连接已提交并关闭）：

```sql
CREATE VIEW source_note AS SELECT 'keep' AS note
```

**完整命令**：

```bash
python -m asset_catalog --db foreign.sqlite export
```

**预期结果**：

- 退出码 2；
- 标准输出为空；
- 标准错误为单行、不含调用栈，指出结构不属于本产品并包含数据库路径：

```
asset_catalog: 错误: 数据库 foreign.sqlite 结构不属于本产品，拒绝覆盖或重建
```

- **原文件保持不变**：命令前后文件字节完全一致；库中不新增 `asset`、
  `asset_tag` 等素材表或任何侧车文件；视图 `source_note` 的定义保留，
  `SELECT note FROM source_note` 仍得到 `keep`；再次执行得到相同错误。

## 7. 回归对照

- `tests/test_export_regression.py`：尚不存在的 `fresh.sqlite` 输出 `[]`
  且新进程可重复读取；零字节、非 SQLite、其他业务表、缺列不兼容、截断损坏、
  路径为目录、父目录缺失不补建、`--db` 为空或缺失等分支的退出码/输出/
  文案与字节保持；默认导出按登记顺序返回完整记录且源文件删除不影响结果。
- `tests/test_view_database_rejection_regression.py`：固定样例
  `foreign.sqlite`（仅视图 `source_note AS SELECT 'keep' AS note`）对
  add/query/export/retag 均退出码 2、标准输出为空、标准错误含“结构不属于
  本产品”与数据库路径、无调用栈，字节与视图查询结果（`keep`）保留；多视图
  及视图名为 `asset`/`asset_tag` 同样拒绝；零字节文件、`sqlite_master`
  为空与仅含 `sqlite_sequence` 的空 SQLite 库保持初始化；父目录缺失不补建；
  含兼容素材表且附带用户视图与触发器的目录继续可用。

**验证状态**：以上两个固定样例已在本仓库当前实现上按原文命令实际执行
（Python 3、Linux、临时目录与项目根各一次），退出码、标准错误（空或指定
单行文案）、标准输出（`[]`）、文件创建/字节保持与视图查询结果均与上述预期
一致；上述两个回归模块共 27 个用例全部通过。验证用的 `fresh.sqlite`、
`foreign.sqlite` 为 `*.sqlite` 忽略产物，执行后已清理，工作区无残留改动。
