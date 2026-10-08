# export 登记路径变为符号链接时的导出流程说明

本文围绕一次 `python -m asset_catalog ... export [--check-files]`，说明一条**登记
路径在登记之后被替换为符号链接**时，导出如何在不触碰登记内容的前提下保留记录并
报告链接目标的当前状态：从公开进程入口讲到记录读取、完整标签组装、保存路径的
状态判断与最终输出，并为关键结论标注实际源码文件、函数与行号（行号对应当前
版本，函数名是稳定锚点）。

本文只描述**已有行为**，不交付任何代码改动：产品源码、SQLite 数据库格式与全部
公开命令的行为均保持不变。说明以一个**已存在且结构兼容的目录数据库**为前提
（两张表 `asset`、`asset_tag` 已由既有流程建好）；`add`、`query`、`show`、
`retag`、`retype`、`tags` 的行为不在此展开。

## 1. 全流程总览

```
python -m asset_catalog --db catalog.sqlite export [--check-files]
        │  asset_catalog/__main__.py: sys.exit(main())（__main__.py:5-6）
        ▼
argparse 解析（build_parser，cli.py:35；export 子命令定义在 cli.py:101-109）：
export 不接受标签、类型、位置参数等任何筛选条件；--check-files 是唯一可选开关
（cli.py:104-108）。非法参数在此即退出码 2，handle_export 尚未执行
        ▼
handle_export（cli.py:534）按顺序编排下列步骤：
  ① open_database（cli.py:538 → 166）：打开已存在的兼容库并校验结构，只读使用
  ② 记录读取（cli.py:540-545）：一条 SQL 取全部 (id, path, type)，
     ORDER BY id ASC —— 即首次登记顺序；不做任何路径解析
  ③ 完整标签组装（cli.py:547 → build_records cli.py:362）：按 position 升序
     补全每条记录的完整标签，记录仅含 path、type、tags，顺序与条数不变
  ④ 保存路径状态判断（cli.py:558-562 → apply_file_status_rules cli.py:396）：
     仅 --check-files 时对每条记录“数据库中保存的 path”调用 check_file_status
     （cli.py:343）；os.stat 跟随符号链接，按链接目标判 present/missing/not_file
        ▼
print(json.dumps(result, ensure_ascii=False))（cli.py:565）
退出码 0（cli.py:566），标准输出仅一行 JSON 数组，成功时标准错误为空
```

任何阶段抛出 `CliError` 都由 `main()`（cli.py:845，捕获于 cli.py:850-852）统一
收敛：向标准错误输出单行 `asset_catalog: 错误: <原因>`（不含调用栈），返回退出码
2，此前标准输出为空。`EXIT_OK = 0`、`EXIT_ERROR = 2` 定义在 cli.py:16-17。

## 2. 入口与参数解析

- `python -m asset_catalog` 执行 `asset_catalog/__main__.py`，其中
  `sys.exit(main())` 进入 `cli.main`（`__main__.py:5-6`、cli.py:845）。
- `main()` 先调用 `build_parser()`（cli.py:846）解析参数。export 子解析器
  （cli.py:101-109）只接受一个可选开关 `--check-files`（cli.py:104-108，
  `store_true`），帮助文本为“为每条记录追加 file_status 字段，报告登记路径当前
  的文件状态”，处理器绑定为 `handle_export`（cli.py:109）。
- export 不接受 `--tag`、`--type`、`--file-status` 或任何位置参数；这些会在
  argparse 阶段由 `_Parser.error`（cli.py:30-32）以退出码 2 拒绝，标准错误含用法
  行与原因，此时 `handle_export` 尚未执行。

## 3. 前提：打开一个已存在且兼容的目录数据库

`handle_export` 第一步调用 `open_database(args.db)`（cli.py:538，定义于
cli.py:166-218）。本文样例中的 `catalog.sqlite` 已由先前两次 `add` 创建，含
README 公开的两张兼容表，因此走的是**校验既有结构**分支：

- 清点 `sqlite_master` 中的用户表与用户视图（cli.py:182-185）；
- 发现 `asset`、`asset_tag` 两表后由 `verify_schema`（cli.py:241-249）按
  `ASSET_COLUMNS = {"id","path","type"}`（cli.py:19）与
  `TAG_COLUMNS = {"asset_id","tag","position"}`（cli.py:20）核对列集合；
- 结构相符即返回连接，导出全程只执行 SELECT。

表结构由 `create_schema`（cli.py:221-238）定义：`asset(id INTEGER PRIMARY KEY
AUTOINCREMENT, path TEXT NOT NULL UNIQUE, type TEXT NOT NULL)`，
`asset_tag(asset_id, tag, position, PRIMARY KEY(asset_id, tag))` 外加
`idx_asset_tag_tag` 索引。导出不依赖、也不改变这一格式；旧的兼容数据库无需迁移。
打不开、损坏或结构不兼容时在打开/校验阶段即抛 `CliError`（退出码 2），不属于
本文样例。

## 4. 记录读取：只按 id 升序回显登记内容

`handle_export` 在 cli.py:540-545 执行唯一一条查询：

```sql
SELECT id, path, type FROM asset
ORDER BY id ASC
```

关键结论（可逐行对照）：

- **无筛选、无路径解析**：导出不带任何 WHERE 条件，也不对 `path` 调用
  `os.path.realpath`（与 `add` 的 cli.py:287、`show` 的 cli.py:630 不同）。
  `path` 列里登记时保存的是什么字符串，输出就是什么字符串。
- **顺序即首次登记顺序**：`id` 为自增主键，`ORDER BY id ASC`（cli.py:543）使
  先登记的素材排在前面；每条素材只对应一行。
- **与文件系统当前状态无关**：本步只读数据库。源文件事后被删除、原路径变成
  目录或符号链接，都不会让记录从结果中消失（cli.py:535-537 的注释明确这一点）。
- 读取过程中若发生 `sqlite3.Error`，包装为 `数据库读取失败: <原因>`
  （cli.py:548-549），退出码 2，不输出部分记录；连接在 `finally` 中关闭
  （cli.py:550-551）。

## 5. 完整标签组装：build_records

行集合交给 `build_records(conn, rows)`（cli.py:547，定义于 cli.py:362-393）。
这是 query 与 export 共用的唯一一处记录组装口径：

1. 先按每行 `(id, path, type)` 建立 `{"path": ..., "type": ..., "tags": []}`
   字典（cli.py:369-372），记录键只有 `path`、`type`、`tags`，没有内部 id、
   没有状态字段。
2. 存在记录时，用一条带 `IN` 的查询取这些素材的标签，并
   `ORDER BY asset_id ASC, position ASC`（cli.py:377-384）；`position` 是登记时
   按标签次序写入的序号（见 `handle_add` 的 `enumerate(tags)`），因此每个素材的
   标签按**登记（或最近一次 retag）的原顺序**完整还原（cli.py:385-389）。
3. 以输入行顺序（即 id 升序）输出列表，每个素材只出现一次（cli.py:393）；
   `rows` 为空时直接返回 `[]`，不访问标签表（cli.py:375）。

至此得到的是纯元数据记录。标签来自数据库，与 A 路径此刻是否为符号链接、链接
指向什么完全无关。

## 6. 保存路径的状态判断：--check-files

### 6.1 默认导出完全不访问文件系统

记录交给 `apply_file_status_rules(records, check_files=args.check_files,
status_filter=None)`（cli.py:558-562；函数定义 cli.py:396-445）。当未传
`--check-files` 时，函数在 cli.py:420-422 原样返回记录，**完全不访问文件
系统**。因此默认导出：

- 只有 `path`、`type`、`tags` 三个键，不含 `file_status`；
- 即使某条记录的保存路径此刻无法 `stat`（权限错误等），默认导出也照常成功，
  因为根本不会发起状态检查（第 8.5 节有对照实验）。

### 6.2 检查对象是“数据库中保存的 path”，os.stat 跟随符号链接

传入 `--check-files` 后，函数先对**每条**记录的 `record["path"]`（即第 4 步从
`asset.path` 读出的登记值）调用 `check_file_status`（cli.py:426，函数定义
cli.py:343-359）：

```python
st = os.stat(path)            # cli.py:351，跟随符号链接判定目标
```

| 保存路径当前情形 | 判定 | 代码位置 |
|---|---|---|
| 普通文件（含“指向普通文件的有效符号链接”） | `present` | cli.py:357-358（`stat.S_ISREG`） |
| 路径不存在；中间组件不是目录；**断开的符号链接** | `missing` | cli.py:352-354（捕获 `FileNotFoundError`/`NotADirectoryError`） |
| 目录等存在但非普通文件（含“指向目录的符号链接”） | `not_file` | cli.py:359 |
| 权限不足或其他 `OSError` | 抛 `CliError`：`无法确定文件状态: <path>: <原因>` | cli.py:355-356 |

由此得出本文的核心约定：

- **状态按链接目标判断**：`os.stat` 而非 `os.lstat`，会跟随符号链接。A 的原登记
  路径被替换为指向普通文件 T 的链接时，按 T 判 `present`；T 被删除形成断链时
  归 `missing`；链接改指目录时归 `not_file`。
- **输出仍保留原登记路径**：状态检查只读取 `stat` 结果，不回写、不替换
  `record["path"]`。A 的输出 `path` 始终是登记时保存的 A 路径，不会变成 T 的
  路径；T 从未登记，也不会因为被链接引用而成为一条新记录。
- **不读内容**：只取 inode 状态（`stat`），不打开、不读取文件内容。

### 6.3 先读完全部状态，再组装输出：任一失败则整次失败

`apply_file_status_rules` 先用列表推导读完全部记录的状态
（cli.py:426），此后才在 cli.py:439-443 给记录写入 `file_status`。export 的
`status_filter` 恒为 `None`，故走 cli.py:435-437 分支：保留全部记录及其相对
顺序，仅逐条**追加**状态，`missing`/`not_file` 的记录也照常保留。

因为“读状态”整体先于“写字段”和唯一的 `print`（cli.py:565），任一条记录在
`check_file_status` 抛 `CliError`（权限或其他系统错误）时：

- 错误直接传播到 `main()`，退出码 **2**；
- **标准输出完全为空**，不会先输出已判定的前半段 JSON；
- 标准错误为单行 `asset_catalog: 错误: 无法确定文件状态: <保存路径>: <原因>`，
  包含原因与相关保存路径，**不含调用栈**；
- 状态只存在于当次进程的本地列表，**不写回数据库**。

### 6.4 状态是当次临时计算，不持久化

`file_status` 只出现在本次标准输出中：数据库里没有状态列（第 3 节的表结构未
变），导出不执行任何 INSERT/UPDATE。因此同一路径在不同时刻的导出会按当时的
文件系统重新判定——删除目标后变 `missing`、在原位置重建目标后下次导出即恢复
`present`，**无需重新登记**。

## 7. 最终输出

全部读取与状态检查成功后，`handle_export` 在 cli.py:565 执行
`print(json.dumps(result, ensure_ascii=False))` 并返回 0（cli.py:566）：

- 标准输出恰好是**一行 JSON 数组**（末尾一个换行符），成功时标准错误为空，
  退出码 0；通过 `json.loads` 比较内容即可，键序与空白不影响语义。
- 默认：元素仅含 `path`、`type`、`tags`；按 id 升序每条素材一次；空目录为 `[]`。
- `--check-files`：在原三键之外逐条追加 `file_status`，取值恒为
  `present`/`missing`/`not_file` 之一；条数、顺序与元数据同默认导出完全一致；
  空目录同样为 `[]`。

## 8. 固定样例

以下结果在 Linux 临时目录 `/tmp/asset-catalog-example/`（原生文件系统，支持
本地符号链接）中由当前实现**实际执行得到**，JSON 为真实标准输出（单行）。
`python` 在本环境为 `python3`；需保证 `asset_catalog` 包可导入（在项目根目录
执行，或把项目根加入 `PYTHONPATH`）。回归测试在各自独立的 `tempfile` 临时目录中
重复同样步骤（见第 9 节）。

### 8.1 准备：按 A、B 顺序登记

```sh
mkdir -p /tmp/asset-catalog-example && cd /tmp/asset-catalog-example
printf 'demo asset A\n' > A.bin
printf 'demo asset B\n' > B.bin
python -m asset_catalog --db catalog.sqlite add A.bin --type image --tag demo --tag ui
python -m asset_catalog --db catalog.sqlite add B.bin --type audio --tag music
```

两条 add 均退出码 0、标准错误为空。登记后库中保存的规范绝对路径与元数据：

| 素材 | 登记保存的 path | type | tags（position 升序） |
|---|---|---|---|
| A（先登记，id 小） | `/tmp/asset-catalog-example/A.bin` | `image` | `["demo", "ui"]` |
| B（后登记，id 大） | `/tmp/asset-catalog-example/B.bin` | `audio` | `["music"]` |

### 8.2 把 A 的原路径替换为指向未登记普通文件 T 的符号链接

```sh
rm A.bin                 # 删除原普通文件 A
ln -s T.bin A.bin        # 在 A 原路径建链接，此时 T 尚不存在（暂时断链）
printf 'unregistered target T\n' > T.bin   # 再写入未登记普通文件 T，链接生效
```

B 保持原样。`readlink A.bin` 为 `T.bin`；T 从未执行过 add。

### 8.3 两条导出命令及其 JSON 结果

默认导出：

```sh
python -m asset_catalog --db catalog.sqlite export
```

退出码 0、标准错误为空，标准输出为一行：

```json
[{"path": "/tmp/asset-catalog-example/A.bin", "type": "image", "tags": ["demo", "ui"]}, {"path": "/tmp/asset-catalog-example/B.bin", "type": "audio", "tags": ["music"]}]
```

检查文件状态：

```sh
python -m asset_catalog --db catalog.sqlite export --check-files
```

退出码 0、标准错误为空，标准输出为一行：

```json
[{"path": "/tmp/asset-catalog-example/A.bin", "type": "image", "tags": ["demo", "ui"], "file_status": "present"}, {"path": "/tmp/asset-catalog-example/B.bin", "type": "audio", "tags": ["music"], "file_status": "present"}]
```

核对要点：

- 默认导出按 A、B 的登记顺序各返回一次，每条只含 `path`、`type`、`tags`。
- `--check-files` 两条记录均追加 `file_status`，值均为 `present`（A 跟随链接
  命中普通文件 T，B 本身就是普通文件）。
- A 的输出 `path` 仍是原登记值 `/tmp/asset-catalog-example/A.bin`，**不是**
  `/tmp/asset-catalog-example/T.bin`；全部记录的 path 列表里也没有 T，T 不成为
  新增记录。

### 8.4 同一样例的状态变化（元数据、顺序始终不变）

下列每一步之后，带 `--check-files` 的退出码均为 0、标准错误为空；不带选项的
`export` 在每种状态下都仍返回 A、B 两条原元数据、不含 `file_status`。

**① 删除 T，形成断链 → A 为 missing**

```sh
rm T.bin
python -m asset_catalog --db catalog.sqlite export --check-files
```

```json
[{"path": "/tmp/asset-catalog-example/A.bin", "type": "image", "tags": ["demo", "ui"], "file_status": "missing"}, {"path": "/tmp/asset-catalog-example/B.bin", "type": "audio", "tags": ["music"], "file_status": "present"}]
```

`os.stat(A)` 跟随链接失败（`FileNotFoundError`，cli.py:352-354），断链归
`missing`；B 仍 `present`。两条记录都保留，不报错、不丢记录。

**② 在原位置按原内容重建 T → 下次导出恢复 present，无需重新登记**

```sh
printf 'unregistered target T\n' > T.bin
python -m asset_catalog --db catalog.sqlite export --check-files
```

```json
[{"path": "/tmp/asset-catalog-example/A.bin", "type": "image", "tags": ["demo", "ui"], "file_status": "present"}, {"path": "/tmp/asset-catalog-example/B.bin", "type": "audio", "tags": ["music"], "file_status": "present"}]
```

状态由新的导出进程按当前文件系统重新判定；没有执行任何 add，A 的 id、path、
type、tags 与 A、B 顺序均不变，T 仍不是记录。

**③ 链接目标改为目录 → A 为 not_file**

```sh
mkdir -p D
rm A.bin
ln -s D A.bin
python -m asset_catalog --db catalog.sqlite export --check-files
```

```json
[{"path": "/tmp/asset-catalog-example/A.bin", "type": "image", "tags": ["demo", "ui"], "file_status": "not_file"}, {"path": "/tmp/asset-catalog-example/B.bin", "type": "audio", "tags": ["music"], "file_status": "present"}]
```

`os.stat(A)` 跟随链接命中目录，`stat.S_ISREG` 为假（cli.py:357-359），A 判
`not_file`，B 仍 `present`；顺序与每条的 path/type/tags 不变。

**所有状态下默认导出保持一致**：上述 ①②③ 任一状态执行不带选项的 `export`，
输出都与 8.3 节的默认 JSON 完全相同（两条三键记录、无 `file_status`）；状态不
持久保存，只反映当次检查。

### 8.5 状态无法判断时：整次导出退出码 2

为不依赖文件系统权限位或账号身份（root 下普通权限位不会触发失败），该边界与
回归测试相同，通过 `PYTHONPATH` 注入的 `sitecustomize` 钩子让 B 的保存路径的
`os.stat` 确定性抛出 `PermissionError(13, ...)`，其余路径行为不变。此时：

```sh
python -m asset_catalog --db catalog.sqlite export --check-files
# 退出码 2；标准输出为空（0 字节）；标准错误为单行：
```

```
asset_catalog: 错误: 无法确定文件状态: /tmp/asset-catalog-example/B.bin: Permission denied (regression test hook)
```

即：退出码 **2**、**标准输出为空**（不输出部分 JSON、不会先打印 A）、标准错误
包含原因“无法确定文件状态”和**相关保存路径**（B 的规范绝对路径），且**不含
调用栈**（无 `Traceback`）。

对照：同样的失败条件下，不带 `--check-files` 的导出因第 6.1 节完全不访问文件
系统，仍退出码 0、标准错误为空，照常输出 A、B 的完整三键元数据；撤去钩子后
`export --check-files` 恢复为两条 `present`。失败前后数据库字节、源文件内容均
不变。

## 9. 只读边界与回归测试对照

整条导出链路是只读的（`handle_export` 注释 cli.py:535-537、553-557）：

- **不读取素材内容**：只用 `os.stat` 取状态（cli.py:351），不打开 A、B 或
  链接目标 T，不依据内容或扩展名做任何判断；
- **不改写数据库**：只执行 SELECT（cli.py:540-545、377-384），状态不写库，
  数据库字节在 present/missing/重建/not_file 各状态导出前后逐字节一致；
- **不改写链接目标、不创建缺失目标**：断链时导出不会重建被删除的 T，
  `readlink` 指向不变，链接目标目录 D 的内容也不变；
- **不改写源文件**：未删除的 B、仍存在的 T 内容在导出前后逐字节一致。

上述结论与现有导出回归测试一一对应：

| 本文结论 | 对应用例（tests/） |
|---|---|
| 有效链接（A→未登记普通文件 T）：A、B 均 present；A 的 path/type/tags 保持登记值，T 不成为记录；默认导出无状态字段 | `test_export_symlink_status_regression.py::test_link_to_unregistered_file_present_and_no_t_record` |
| 删除 T 断链 → A missing、B present；原位置重建 T 后新进程报告 present，无需重新登记；两种状态下默认导出不变 | `test_export_symlink_status_regression.py::test_broken_link_reports_missing_then_rebuilt_reports_present` |
| 三种状态（有效/断链/重建）下导出前后数据库字节、B 与仍存在的 T 内容、`readlink` 指向均不变，不重建 T | `test_export_symlink_status_regression.py::test_exports_readonly_database_b_and_target_bytes` |
| 链接指向临时目录 → A not_file、B present；顺序与元数据不变；数据库、B、链接与目录内容不被改动 | `test_export_symlink_status_regression.py::test_link_to_directory_reports_not_file` |
| 任一记录 `os.stat` 报权限错误：退出码 2、标准输出为空、标准错误含“无法确定文件状态”与相关路径、无 Traceback；同条件下默认导出不受影响 | `test_export_regression.py::test_check_files_status_error_fails_whole_export` |
| 默认导出按登记顺序、每条仅 path/type/tags | `test_export_regression.py::test_export_returns_all_records_in_registration_order` |
| `--check-files` 保留全部记录与顺序、追加状态；默认导出无状态字段 | `test_export_regression.py::test_check_files_appends_status_and_keeps_all_records` |
| 源文件删除后默认导出仍返回记录 | `test_export_regression.py::test_deleted_source_file_still_exported_without_file_status` |
| 导出不改登记内容与素材文件 | `test_export_regression.py::test_export_does_not_change_registrations_or_files` |
| 目录等非普通文件判 not_file（直接把登记路径换成目录） | `test_export_regression.py::test_check_files_reports_directory_as_not_file` |
| 空目录带/不带 `--check-files` 均输出 `[]` | `test_export_regression.py::test_check_files_empty_database_outputs_empty_array`、`test_empty_database_exports_empty_array` |

`test_export_symlink_status_regression.py` 中的符号链接用例在环境确实不支持
创建符号链接时仅 `skipTest` 跳过并说明原因，其余错误一律使测试失败；每个用例
使用独立临时目录与数据库，通过 `json.loads` 比较解析后的内容，不依赖 JSON 空白
或键顺序。
