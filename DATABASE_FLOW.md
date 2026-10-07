# 默认数据库打开过程流程说明

本文对应 `asset_catalog/cli.py` 中每个公开子命令都要经过的“打开默认导出
数据库”这一步：`--db` 指定的输入文件在什么条件下会被**初始化**为一个空的
本产品目录库、在什么条件下被**沿用**、在什么条件下被**拒绝**。说明以默认
导出 `export` 串起公开入口的参数处理、数据库判定与结果输出，便于逐条核对。
行号对应当前版本，函数名是稳定锚点。本文只描述既有行为：命令参数风格、
JSON 输出格式、退出码与数据库兼容规则均保持不变，不新增功能或脚本。

## 1. 全流程总览

```
python -m asset_catalog --db DB_FILE export [--check-files]
        │  asset_catalog/__main__.py（__main__.py:1-7）导入 cli.main
        ▼
main(argv)（cli.py:845-852）
  ├─ build_parser()（cli.py:35-163）解析参数：
  │    --db 必填（cli.py:37-42）；export 子命令只接受可选 --check-files
  │    （cli.py:101-109）。参数错误由 _Parser.error（cli.py:27-32）收敛为
  │    退出码 2、标准输出为空，不打开任何数据库
  └─ args.handler → handle_export（cli.py:534-566）
        ▼
open_database(db_path)（cli.py:166-218）——三种结局：
  ① 初始化：既无用户表也无用户视图 → create_schema（cli.py:221-238）
  ② 沿用：asset、asset_tag 两张实体表都在且列名集合相符
           → verify_schema（cli.py:241-249）通过，附加对象不干预
  ③ 拒绝：其他用途/不兼容/打不开/损坏 → 抛 CliError，不做任何写入
        ▼
handle_export 读取与输出（cli.py:538-565）：
  SELECT id, path, type FROM asset ORDER BY id ASC（cli.py:540-545）
  → build_records（cli.py:362-393）按 position 组装完整标签
  → 标准输出一行 JSON 数组；空目录为 []，退出码 0
```

任何阶段抛出 `CliError` 都由 `main()`（cli.py:848-852）统一收敛：标准错误
单行 `asset_catalog: 错误: <原因>`（不含调用栈），退出码 2（`EXIT_ERROR`，
cli.py:17），标准输出为空。

## 2. 公开入口与参数处理

- **入口链**：`python -m asset_catalog` 执行 `asset_catalog/__main__.py`
  （__main__.py:1-7），其中 `sys.exit(main())` 进入 `cli.main`
  （cli.py:845-856）；`main` 的返回值即进程退出码。
- **`--db` 为全局必填选项**（cli.py:37-42），取值原样传给各处理器，不做
  路径规范化；是否创建文件、能否打开全部交给 `open_database` 判定。缺少
  `--db` 由 argparse 在解析阶段以退出码 2 拒绝，标准输出为空。
- **export 子命令**定义在 cli.py:101-109，无位置参数，唯一选项是
  `--check-files`；传入标签、类型、多余位置参数或其他子命令选项时，
  `_Parser.error`（cli.py:27-32）输出用法与单行错误后以退出码 2 结束。
  这类参数错误发生在 `open_database` 之前，**不会创建或修改数据库文件**。
- **七个处理器共用同一打开规则**：`handle_add`（cli.py:302）、
  `handle_query`（cli.py:470）、`handle_export`（cli.py:538）、
  `handle_tags`（cli.py:582）、`handle_show`（cli.py:632）、
  `handle_retag`（cli.py:752）、`handle_retype`（cli.py:802）都调用
  `open_database`。因此下文的初始化、沿用与拒绝判定对所有子命令一致；
  各命令在打开数据库之前自行做的参数校验（如 add 的源路径检查、
  query/retag 的标签校验）失败时同样不触发建库。

## 3. open_database 的判定：初始化、沿用、拒绝

`open_database`（cli.py:166-218）的全部逻辑只依赖一次 `sqlite_master`
清点与两张表名/列名集合比较，不读取任何业务数据。

### 3.1 清点用户对象（cli.py:177-194）

1. 空路径立即拒绝（cli.py:171-172）：`数据库路径不能为空`。
2. `sqlite3.connect(db_path)`（cli.py:177）**不创建缺失的父目录**，代码中
   也没有任何建目录操作；连接与首次 I/O 是惰性的。
3. 随即清点表与视图（cli.py:182-185）：
   ```sql
   SELECT type, name FROM sqlite_master WHERE type IN ('table', 'view')
   ```
   这一步失败（含路径为目录、父目录缺失、无权限、文件不是 SQLite 库等）
   走 cli.py:186-189，抛
   `无法打开数据库 <db_path>: <数据库给出的原因>`，并关闭连接。
4. 清点结果分成两个集合（cli.py:191-192）：`table_names` 与 `view_names`；
   内部表集合 `internal = {"sqlite_sequence"}`（cli.py:193）被剔除，
   `user_tables = table_names - internal`（cli.py:194）。**`sqlite_sequence`
   不计作用户表**：它是 SQLite 为 `AUTOINCREMENT` 自动维护的内部表，
   单独存在不代表已有业务内容。

### 3.2 三类输入会被初始化为空目录库（cli.py:205-208 → create_schema）

当两张实体表不同时存在、且既无任何用户表也无任何用户视图时，调用
`create_schema(conn)`（cli.py:221-238）在同一文件内建表并提交：

| 输入形态 | 清点结果 | 处理 |
|----------|----------|------|
| 父目录存在、数据库文件**尚不存在** | `sqlite3.connect` 惰性创建空文件，`sqlite_master` 无行 | 初始化为空目录库 |
| 已存在的**零字节文件** | 无头页可读，清点结果为空 | 初始化为空目录库（沿用该文件，不另建文件） |
| **没有用户表和用户视图的空 SQLite 库**：`sqlite_master` 完全为空，或仅含内部表 `sqlite_sequence` | `user_tables`、`view_names` 均为空集 | 初始化为空目录库 |

`create_schema`（cli.py:221-238）创建 `asset`（id/path/type）、
`asset_tag`（asset_id/tag/position 及主键、外键）与索引
`idx_asset_tag_tag`，随后 `conn.commit()`。提交后文件就是一个可被再次
打开的本产品空目录库（SQLite 同时为 `AUTOINCREMENT` 准备内部表
`sqlite_sequence`，它仍不属于用户对象）。对这三类空库执行 `export` 的
公开结果相同：退出码 0、标准错误为空、标准输出一行 `[]`。

### 3.3 兼容目录库被沿用：只核对两张表的列名集合（cli.py:197-198）

当 `asset` 与 `asset_tag` **两张表名都在 `table_names` 中**（必须是实体
表；同名视图不算，见 3.4）时，调用 `verify_schema(conn)`
（cli.py:241-249）：

```python
asset_cols = {行[1] for PRAGMA table_info(asset)}      # cli.py:242-244
tag_cols   = {行[1] for PRAGMA table_info(asset_tag)}  # cli.py:245-247
if asset_cols != ASSET_COLUMNS or tag_cols != TAG_COLUMNS:   # cli.py:248
    raise CliError("数据库表结构与本产品不兼容，拒绝覆盖或重建")  # cli.py:249
```

- 要求的列名集合固定为 `ASSET_COLUMNS = {"id", "path", "type"}` 与
  `TAG_COLUMNS = {"asset_id", "tag", "position"}`（cli.py:19-20），必须
  **恰好相等**：少列、多列都判不符。
- **只核对列名集合**：不验证字段类型与类型亲和性，不验证 `NOT NULL`、
  `UNIQUE`、主键、外键等约束，也不验证索引是否存在或其定义是否一致。
- 一旦走到这一支，清点中出现的**附加用户表、用户视图或触发器都不会单独
  导致拒绝**：判定不再回看其他对象，附加对象原样保留、可独立查询。
  触发器不属于 `type IN ('table','view')` 的清点范围，本就不参与判定。
- 校验通过后连接原样返回给命令使用，库中的既有登记记录保持不变。

### 3.4 拒绝：缺少所需表却存在用户对象，或只有用户视图（cli.py:199-204）

两张实体表没有同时存在时：

```python
elif user_tables or view_names:
    raise CliError(f"数据库 {db_path} 结构不属于本产品，拒绝覆盖或重建")
```

- **其他用途的数据库**：含任意与本产品无关的用户表（如
  `business_record`）而缺少 `asset`/`asset_tag` 时拒绝，不在其中补表或
  重建，原有表与数据保留。
- **只有用户视图的库不能按空库初始化**：视图可以只由常量表达式构成而不
  依赖任何表（如 `CREATE VIEW source_note AS SELECT 'keep' AS note`），
  因此“没有用户表”不等于“没有业务内容”。只要 `view_names` 非空，即使
  一个用户表都没有，也按不属于本产品拒绝。
- **同名视图不能代替实体表**：视图名为 `asset` 或 `asset_tag` 时它仍只
  出现在 `view_names` 中，`table_names` 里没有这两张表，判定结果不变；
  不会因为名字相同而在视图库里补建实体表。

该 `CliError` 由 cli.py:212-216 单独捕获：关闭连接后原样抛出，**不执行
任何写入**，保证被拒绝的文件字节不变。

### 3.5 拒绝：两表列名不符（cli.py:248-249）

两张实体表都在但列名集合不相等（缺列、多列、列名拼写不同）时，
`verify_schema` 抛 `数据库表结构与本产品不兼容，拒绝覆盖或重建`。
同样不比对类型、约束与索引，因此错误原因只可能是列名集合不符；该错误
经 cli.py:212-216 关闭连接后抛出，库文件不被改动。

### 3.6 拒绝：打不开、父目录缺失、路径为目录（cli.py:171-189）

| 情形 | 检出点 | CliError 文案 |
|------|--------|---------------|
| `--db` 为空字符串 | cli.py:171-172 | `数据库路径不能为空` |
| 父目录（或中间目录）缺失 | `sqlite3.connect` 后首次 I/O，cli.py:186-189 | `无法打开数据库 <路径>: unable to open database file` |
| 数据库路径本身是一个目录 | 同上 | 同上（原因同为 `unable to open database file`） |
| 无权限等其他打开错误 | 同上 | `无法打开数据库 <路径>: <数据库给出的原因>` |

父目录缺失时**不补建任何目录**：实现中没有创建目录的步骤，连接失败即
结束，缺失的路径层级保持缺失。

### 3.7 拒绝：文件损坏的三个检出点

损坏内容在不同阶段被发现，错误包装不同，但公开外观一致（退出码 2、
标准输出为空、标准错误单行且无调用栈）：

1. **清点阶段即不可读**：连第一次 `sqlite_master` 查询都失败时，由
   cli.py:186-189 包装为 `无法打开数据库 <路径>: <原因>`。例如非 SQLite
   文件得到 `file is not a database`，镜像被截断得到
   `database disk image is malformed`。
2. **初始化/校验阶段 I/O 失败**：`create_schema` 或 `PRAGMA
   table_info` 执行中抛 `sqlite3.Error` 时，由 cli.py:209-211 包装为
   `数据库 <路径> 已损坏或无法读写: <原因>`（例如对只读的零字节文件尝试
   初始化时为 `attempt to write a readonly database`）。
3. **通过校验后命令读取失败**：`open_database` 已成功返回，export 自身的
   `SELECT` 失败时由 `handle_export` 包装为
   `数据库读取失败: <原因>`（cli.py:548-549）。例如头页与目录页可读、
   数据页损坏的库会走到这一支。

### 3.8 被拒绝文件的保持性

- 结构类拒绝（3.4、3.5）在 cli.py:212-216 中只关闭连接、原样抛出，
  **不写入、不创建表、不留下日志或页缓存改动**；拒绝其他用途或不兼容库
  时原文件保留，原有用户表、视图（含其定义与查询结果）、触发器均不变，
  也不产生 `-journal`/`-wal`/`-shm` 侧车文件。
- 打不开类拒绝（3.6）发生在任何写入之前；父目录缺失时不补建目录。
- 再次以相同参数调用得到相同的错误结果。

## 4. export 的结果输出

`handle_export`（cli.py:534-566）在成功打开数据库后：

1. `SELECT id, path, type FROM asset ORDER BY id ASC`
   （cli.py:540-545）：按 `id` 升序，即**首次登记顺序**取全部素材，
   每条只取一次。
2. `build_records(conn, rows)`（cli.py:362-393）组装记录：每条记录只含
   `path`（登记时保存的规范绝对路径）、`type`、`tags`；标签按
   `asset_tag.position` 升序读出，保留登记（或最近一次 retag）的完整
   顺序（cli.py:377-389）。rows 为空时直接返回 `[]`，不访问标签表。
3. 默认导出不读取素材内容、不检查登记路径的文件状态（cli.py:534-537
   注释明确）：**源文件事后缺失或变成目录都不影响记录返回**。仅当传入
   `--check-files` 时才经 `apply_file_status_rules`（cli.py:558-562）
   逐条追加 `file_status`，空目录仍输出 `[]`。
4. `print(json.dumps(result, ensure_ascii=False))`（cli.py:565）输出一行
   JSON 数组，返回 `EXIT_OK`（cli.py:566，值为 0）。空目录库的输出就是
   一行 `[]`。

读取阶段任何 `sqlite3.Error` 包装为 `数据库读取失败: <原因>`
（cli.py:548-549），连接在 `finally` 中关闭（cli.py:550-551）；唯一的
`print` 在全部读取之后，因此失败时不会先输出半截 JSON。

## 5. 错误分支对照（公开结果统一为：退出码 2、标准输出为空、标准错误说明原因且不含调用栈）

| 输入情形 | 判定位置 | 标准错误中的原因（前缀均为 `asset_catalog: 错误: `） | 文件系统副作用 |
|----------|----------|------------------------------------------------------|----------------|
| 父目录存在、文件不存在 | cli.py:205-208 | 不报错：初始化后导出成功，输出 `[]`、退出码 0 | 创建并初始化该文件 |
| 零字节文件 | cli.py:205-208 | 同上 | 在原文件内初始化 |
| 无用户表无用户视图的空 SQLite 库（含仅 `sqlite_sequence`） | cli.py:205-208 | 同上 | 在原文件内初始化 |
| 两表齐全、列名集合相符（即使另有用户表、视图、触发器） | cli.py:197-198 → 241-249 | 不报错：沿用并正常导出 | 无 |
| 缺所需表但存在其他用户表 | cli.py:199-204 | `数据库 <路径> 结构不属于本产品，拒绝覆盖或重建`（含路径） | 无，原文件保留 |
| 只有用户视图（含同名 `asset`/`asset_tag` 视图） | cli.py:199-204 | 同上（含路径） | 无，视图定义与查询结果保留 |
| 两表都在但列名集合不符 | cli.py:248-249 | `数据库表结构与本产品不兼容，拒绝覆盖或重建` | 无，原文件保留 |
| 非 SQLite 文件 / 镜像截断等 | cli.py:186-189 或 209-211 / 548-549 | `无法打开数据库 …` / `… 已损坏或无法读写: …` / `数据库读取失败: …` | 无写入 |
| 数据库路径是目录 | cli.py:186-189 | `无法打开数据库 <路径>: unable to open database file` | 无，不建目录 |
| 父目录缺失 | cli.py:186-189 | 同上 | **不补建父目录** |
| `--db` 为空字符串 | cli.py:171-172 | `数据库路径不能为空` | 无 |
| export 传不支持的参数、缺 `--db` | argparse / cli.py:27-32 | 单行参数错误（含用法行） | 不打开、不创建数据库 |

## 6. 固定对照样例

以下两个样例在现有演示目录（项目根目录，内含 `demo.png` 与
`asset_catalog` 包）内执行；JSON 按解析后的内容核对，不依赖键序与空白。
两个样例只新建各自的 `.sqlite` 文件，不登记任何素材。

### 6.1 样例一：尚不存在的 fresh.sqlite 被初始化为空目录库

**准备状态**：演示目录中不存在 `fresh.sqlite`，其父目录（当前目录）存在。

```bash
python -m asset_catalog --db fresh.sqlite export
```

**预期**：退出码 0；标准错误为空；标准输出恰为一行空数组：

```json
[]
```

执行后 `fresh.sqlite` 留在演示目录中，是一个可再次读取的空目录库：
再次执行同一条 `export` 命令仍为退出码 0、标准错误为空、输出 `[]`；
库内已存在实体表 `asset`、`asset_tag`（以及 SQLite 为
`AUTOINCREMENT` 准备的内部表 `sqlite_sequence`），但没有任何素材记录。
此后对该库执行 `add` 登记、再由新进程 `export`/`query` 均可正常读回。

### 6.2 样例二：仅含一个视图的 foreign.sqlite 被原样拒绝

**准备状态**：在演示目录中准备 `foreign.sqlite`，其唯一业务对象是视图
`source_note`，定义为返回常量 `'keep'`：

```bash
python -c "import sqlite3; c=sqlite3.connect('foreign.sqlite'); c.execute(\"CREATE VIEW source_note AS SELECT 'keep' AS note\"); c.commit(); c.close()"
```

**命令**：

```bash
python -m asset_catalog --db foreign.sqlite export
```

**预期**：退出码 2；标准输出为空（连空行也没有）；标准错误为单行、不含
调用栈，指出结构不属于本产品并包含数据库路径（相对路径按传入文本显示）：

```
asset_catalog: 错误: 数据库 foreign.sqlite 结构不属于本产品，拒绝覆盖或重建
```

**保持性核对**：

- `foreign.sqlite` 的文件字节与调用前完全一致（可用备份 `cmp` 比对），
  不产生 `-journal`/`-wal`/`-shm` 侧车文件；
- 库中不新增 `asset`、`asset_tag` 素材表（不存在同名实体表）；
- 原视图保留且查询结果不变：

  ```bash
  python -c "import sqlite3; c=sqlite3.connect('foreign.sqlite'); print(c.execute('SELECT note FROM source_note').fetchall())"
  # [('keep',)]
  ```

- 再次执行同一条 export 仍得到退出码 2 与逐字相同的标准错误。

### 6.3 默认导出仍返回完整元数据，源文件缺失不影响记录

在任一兼容目录库上，默认 `export` 按首次登记顺序（id 升序）返回每条素材
的完整元数据，记录仅含 `path`、`type`、`tags`，标签顺序完整；删除某个
已登记素材的源文件、或把源路径替换为目录后再次 `export`，该记录仍照常
出现、字段不变（见 4 与 cli.py:534-537）。默认导出为只读操作，不改动
数据库与素材文件。

**验证状态**：上述两类样例及 3.2–3.7 的各分支已在本仓库当前实现上实际
执行（Python 3、Linux、WSL2；样例一、样例二在项目根演示目录内执行后已
清理临时库，其余分支在独立临时目录内执行），退出码、标准输出为空/`[]`、
标准错误文案与文件保持性均与本文预期一致。

## 7. 回归对照

- `tests/test_export_regression.py`：尚不存在的 `fresh.sqlite` 导出 `[]`
  并可被新进程再次读取（`test_empty_database_exports_empty_array`）；
  父目录缺失不补建（`test_missing_parent_directory_is_not_created`）；
  数据库路径为目录（`test_database_path_is_directory_rejected`）、空路径
  与缺 `--db`、非 SQLite 文件、含其他业务表的库、缺列的不兼容库
  （`test_corrupt_and_incompatible_databases_rejected_and_untouched`，
  含拒绝后字节与原有数据保留）、镜像截断无部分输出
  （`test_malformed_database_produces_no_partial_output`）；默认导出按
  登记顺序返回完整元数据且源文件删除不影响
  （`test_export_returns_all_records_in_registration_order`、
  `test_deleted_source_file_still_exported_without_file_status`、
  `test_export_does_not_change_registrations_or_files`）。
- `tests/test_view_database_rejection_regression.py`：固定样例
  `foreign.sqlite`（仅 `source_note AS SELECT 'keep' AS note`）对
  add/query/export/retag 均退出码 2、标准输出为空、标准错误含“结构不属于
  本产品”与数据库路径、无调用栈，且字节与视图查询结果 `keep` 保留
  （`test_export_rejects_view_only_database` 等）；多视图及视图名为
  `asset`/`asset_tag` 同样拒绝
  （`test_view_count_and_names_do_not_change_result`）；重复调用错误一致
  （`test_repeated_calls_fail_identically_and_keep_bytes`）；参数错误不
  初始化视图库（`test_invalid_arguments_still_follow_existing_validation`）；
  尚不存在的 `fresh.sqlite`、零字节文件、`sqlite_master` 为空或仅含
  `sqlite_sequence` 的空 SQLite 库均初始化为空目录库
  （`EmptyDatabaseInitializationRegressionTest` 中
  `test_fresh_sqlite_export_initializes_then_register_and_read`、
  `test_zero_byte_file_is_initialized`、
  `test_empty_sqlite_without_user_objects_is_initialized`）；父目录缺失
  不补建；兼容目录库附带用户视图与触发器仍继续可用
  （`test_normal_catalog_with_view_and_trigger_remains_usable`）。
