# export 登记路径变为符号链接时的文件状态流程说明

本文专门说明一条**已有**流程：目录中某条记录的登记路径在登记**之后**被替换成
符号链接时，`export` 与 `export --check-files` 如何在**保留登记记录**的前提下
读取与输出。文章从公开命令入口讲到标准输出，依次串起：公开入口与参数、数据库
打开、登记记录读取、完整标签组装、**保存路径**当前状态的判断，以及最终输出；
并为每个关键结论标注实际源码文件、函数与行号（行号对应当前版本，函数名是稳定
锚点），便于逐段核对。

本次交付**只新增本文档**：产品源码（`asset_catalog/`）、SQLite 数据库格式与
所有公开命令的行为均保持不变，旧数据库无需迁移。本文只描述现有行为，固定样例
与结论均可在当前实现上复算。

## 1. 前提与范围

- 以一个**已存在且结构兼容**的目录数据库为前提：其中有 README 公开的两张表
  `asset`、`asset_tag`（建表语句见 `asset_catalog/cli.py:222-237`）。导出对
  数据库只读，不依赖素材文件此刻是否仍在原处。
- `asset.id` 为 `INTEGER PRIMARY KEY AUTOINCREMENT`（`cli.py:225`），其自增
  顺序就是**首次登记顺序**；`asset_tag.position` 保存标签登记时的次序
  （`cli.py:232`）。
- 本文聚焦 export 这一条流程。`query`、`show` 复用同一套记录组装与状态判定
  函数，但它们各自的入选/定位语义不在此展开。

## 2. 全流程总览

```
python -m asset_catalog --db catalog.sqlite export [--check-files]
        │  asset_catalog/__main__.py → asset_catalog/cli.py: main()（cli.py:845）
        ▼
argparse 解析（build_parser，cli.py:35；export 子命令定义在 cli.py:101-109）：
export 不接受标签、类型等任何筛选参数；--check-files 是唯一可选开关
（cli.py:104-108）；非法参数在此直接退出码 2、标准输出为空、不创建数据库
        ▼
handle_export（cli.py:534）按顺序编排：
  ① open_database（cli.py:538 → 定义 cli.py:166）：打开已存在的兼容库并校验结构
  ② 读取登记记录（cli.py:540-545）：
       SELECT id, path, type FROM asset ORDER BY id ASC
     —— 直接枚举登记表，不做路径解析、不读取素材内容、不检查文件状态
  ③ build_records（cli.py:547 → 定义 cli.py:362）：按 position 升序补全每条
     记录的完整标签，记录仅含 path、type、tags，顺序即 id 升序
  ④ apply_file_status_rules（cli.py:558-562 → 定义 cli.py:396）：
     默认直接返回；仅当传入 --check-files 时，对每条记录的“保存路径”调用
     check_file_status（cli.py:343），先读完全部状态再统一追加 file_status
        ▼
print(json.dumps(result, ensure_ascii=False))（cli.py:565），返回 0（cli.py:566）
标准输出仅一行 JSON 数组；成功时标准错误为空
```

任何阶段抛出 `CliError` 都由 `main()` 统一收敛（`cli.py:850-852`）：向标准错误
输出单行 `asset_catalog: 错误: <原因>`（不含调用栈），返回退出码 2，此前标准
输出为空。`EXIT_OK = 0`、`EXIT_ERROR = 2` 定义在 `cli.py:16-17`。

## 3. 公开入口与参数

- `python -m asset_catalog` 执行 `asset_catalog/__main__.py`，其中
  `sys.exit(main())` 进入 `cli.main`（`asset_catalog/cli.py:845`）。
- export 子解析器在 `build_parser` 中创建（`cli.py:101-103`），只挂一个可选
  开关 `--check-files`（`cli.py:104-108`），帮助文本为“为每条记录追加
  file_status 字段，报告登记路径当前的文件状态”；处理函数绑定为
  `handle_export`（`cli.py:109`）。
- export **没有** `--tag`、`--type`、`--file-status`、`--exclude-tag` 或位置
  参数：向 export 传这些参数会在 argparse 阶段被拒（回归用例
  `test_unsupported_export_arguments_rejected`，见第 11 节）。
- argparse 层面的错误由 `_Parser.error`（`cli.py:30-32`）处理：退出码 2、
  标准输出为空，此时 `handle_export` 尚未执行，不会创建数据库文件。

## 4. 打开数据库：以已存在的兼容库为前提

`handle_export` 第一步调用 `open_database(args.db)`（`cli.py:538`，函数定义
`cli.py:166-218`）：

- 已存在 `asset`、`asset_tag` 两表时，经 `verify_schema`（`cli.py:241-249`）
  逐列校验：`asset` 列恰为 `{id, path, type}`、`asset_tag` 列恰为
  `{asset_id, tag, position}`（常量在 `cli.py:19-20`）。本文的固定样例就落在
  这样一个由 `add` 正常建立的兼容库上。
- 含其他用户表或用户视图、路径为目录、父目录缺失、文件损坏等情况，
  `open_database` 抛 `CliError`，不覆盖、不补表、不重建（这些边界在现有
  导出回归测试中有覆盖，见第 11 节）。它们属于“打不开/不兼容库”的边界，
  不是本文状态流程的主体。

## 5. 读取登记记录：只枚举登记表，不解析路径

数据库打开后，`handle_export` 在 `cli.py:540-545` 执行：

```sql
SELECT id, path, type FROM asset
ORDER BY id ASC
```

要点（可对照 `handle_export` 开头注释，`cli.py:535-537`）：

- **直接反映登记记录**：导出的范围就是 `asset` 表的全部行，按 `id` 升序，即
  首次登记顺序。每条素材恰好出现一次，没有筛选、没有去重之外的处理。
- **不做路径解析**：读出的 `path` 是登记时保存的规范绝对路径字符串，原样进入
  下一步。即使该路径在登记后变成了符号链接，export 也**不会**在这里改读链接
  目标路径，更不会把目标当作另一条记录。
- **不读取素材内容**：全程没有打开/读取文件字节的动作。
- **默认不检查文件状态**：不带 `--check-files` 时，本步之后根本不访问文件
  系统，因此源文件被删除或原路径变成目录/符号链接都不会让素材从结果中消失。

读取若发生 SQLite 错误，被包装为 `数据库读取失败: <原因>`（`cli.py:548-549`），
连接在 `finally` 中关闭（`cli.py:550-551`），不输出部分记录。

## 6. 组装完整标签：build_records

第 5 步的 `(id, path, type)` 行交给 `build_records(conn, rows)`
（`cli.py:547` 调用，函数定义 `cli.py:362-393`）。这是 query 与 export 之间
**唯一**的记录组装处：

1. 先按输入行为每个素材建 `{"path": path, "type": type, "tags": []}`
   （`cli.py:369-372`），`path` 直接取登记表里保存的值。
2. 有记录时，用一条查询取这些素材的全部标签（`cli.py:377-384`）：

   ```sql
   SELECT asset_id, tag FROM asset_tag
   WHERE asset_id IN (?, ...)
   ORDER BY asset_id ASC, position ASC
   ```

   `position` 升序保证标签按**登记顺序**（或最近一次 retag 后的顺序）还原
   （`cli.py:374`、`cli.py:381`）。
3. 按素材把标签归组后，按输入行顺序（即 `id` 升序）产出记录列表
   （`cli.py:385-393`）。

因此本样例中 A 先于 B（A 的 id 更小），A 的标签恒为 `["demo", "ui"]`、
B 的标签恒为 `["music"]`，与登记路径此刻是不是符号链接无关。

## 7. 保存路径的状态判断：check_file_status 与整次失败

`build_records` 之后，`handle_export` 调用 query/export 共用的
`apply_file_status_rules`（`cli.py:558-562`，函数定义 `cli.py:396-445`），
export 固定以 `status_filter=None` 调用（`cli.py:561`），即只用“追加状态”
这一半语义，不做状态筛选。

### 7.1 默认完全不访问文件系统

`check_files` 为假、`status_filter` 为 `None` 时，函数在 `cli.py:420-422`
原样返回记录：**不调用任何文件系统函数**。这就是默认导出在文件缺失、断链、
路径变目录时仍稳定输出原元数据的原因。

### 7.2 状态按“保存路径”的链接目标判定

传入 `--check-files` 后，函数先对每条记录的**保存路径**逐条求状态
（`cli.py:426`）：

```python
statuses = [check_file_status(record["path"]) for record in records]
```

注意传入的是 `record["path"]`——第 6 步从登记表原样取出的路径字符串。判定在
`check_file_status`（`cli.py:343-359`）中完成，核心是一次**跟随符号链接**的
`os.stat`（`cli.py:351`）：

| 保存路径当前情形 | 判定 | 代码位置 |
|---|---|---|
| 普通文件（含“路径本身是指向普通文件的符号链接”，按链接目标判断） | `present` | `cli.py:357-358`（`stat.S_ISREG`） |
| 路径不存在；中间组件不是目录；**断开的符号链接** | `missing` | `cli.py:352-354`（捕获 `FileNotFoundError`/`NotADirectoryError`） |
| 目录等存在但非普通文件（含指向目录的符号链接） | `not_file` | `cli.py:359` |
| 权限不足或其他 `OSError` | 抛 `CliError`：`无法确定文件状态: <路径>: <原因>` | `cli.py:355-356` |

关键区别（函数 docstring 已写明，`cli.py:345-349`）：

- `os.stat` **跟随**符号链接，所以链接本身不参与 present/not_file 的区分，
  一切按链接**指向的对象**判定；断链（目标不存在）归为 `missing`。
- 状态只读取 inode 元数据，**不读取文件内容**。
- 状态被追加到记录上（`cli.py:439-443`），但记录里的 `path` 字符串自始至终
  是登记时保存的原路径，**不会被改写成链接目标路径**。于是 A 的登记路径变成
  “指向未登记文件 T 的符号链接”时：状态按 T 判定，输出的 `path` 仍是 A 的
  原登记路径；T 从未写入 `asset` 表，目录里也不存在 T 的记录。

### 7.3 先读完全部状态，再决定输出

实现先用列表推导读完全部候选状态（`cli.py:426`），随后才给记录追加
`file_status`（`cli.py:439-443`）。export 不筛选，保留全部记录及其相对顺序
（`cli.py:435-437`）。因此任一记录因权限或其他系统错误无法判定状态时，
`check_file_status` 抛出的 `CliError` 会跳过追加与输出，直接传播到 `main()`：

- 退出码 **2**；
- **标准输出为空（0 字节）**：唯一的 `print` 在 `cli.py:565`，位于状态处理
  之后，异常时根本不执行，不会先吐出半截 JSON；
- 标准错误为单行 `asset_catalog: 错误: 无法确定文件状态: <保存路径>: <原因>`，
  同时给出原因与出问题的那条**保存路径**，**不含调用栈**；
- 状态只在当次运行的内存里，**不写回数据库**；失败前后登记记录与素材文件
  不变。

不带 `--check-files` 的默认导出即使在同样的权限条件下也不受影响（第 7.1 节的
短路），仍退出码 0、输出完整元数据。

## 8. 最终输出

成功时 `handle_export` 在 `cli.py:565` 打印一行 JSON 数组并返回 0
（`cli.py:566`）：

- **默认 `export`**：每条记录恰好三个键 `path`、`type`、`tags`，无
  `file_status`、无内部 id；按首次登记顺序（`id` 升序）每条素材一次；空目录
  输出 `[]`。
- **`export --check-files`**：在同样的记录上**追加** `file_status`
  （`present`/`missing`/`not_file`），不改变条数、顺序与原有三键；缺失或已
  变成目录/断链的记录照常保留；空目录输出 `[]`。
- 成功时标准错误为空，标准输出恰好一行（末尾一个换行符）。
- 状态反映**本次检查的即时结果**，不持久化：文件系统变化后，下一次导出由新
  进程重新 `os.stat` 判定，无需重新登记；默认导出则从不包含状态字段。

## 9. 固定样例（临时目录，需支持本地符号链接）

样例放在一个支持本地符号链接的临时目录，下文以
`/tmp/asset_catalog_export_link_demo`（记作 `$DEMO`）为**明确的规范绝对路径**
示例。所有命令在该目录内执行（`cd $DEMO`，并保证 `asset_catalog` 可导入，
例如把项目根加入 `PYTHONPATH`）。JSON 按解析后的内容核对，不依赖键序或空白。

样例文件内容固定（导出全程不会读取这些字节，仅用于让对象成为真实普通文件/
目录）：

| 文件 | 是否登记 | 内容（字节） |
|---|---|---|
| `A.bin` | 登记（image，demo/ui） | `demo asset A\n` |
| `B.bin` | 登记（audio，music），全程保持普通文件 | `demo asset B\n` |
| `T.bin` | **从不登记**，仅作 A 的符号链接目标 | `unregistered target T\n` |

### 9.1 准备：按 A、B 顺序登记

```sh
cd /tmp/asset_catalog_export_link_demo
printf 'demo asset A\n' > A.bin
printf 'demo asset B\n' > B.bin

python -m asset_catalog --db catalog.sqlite \
  add /tmp/asset_catalog_export_link_demo/A.bin --type image --tag demo --tag ui
python -m asset_catalog --db catalog.sqlite \
  add /tmp/asset_catalog_export_link_demo/B.bin --type audio --tag music
```

两条 `add` 均退出码 0、标准错误为空，分别回显：

```json
{"path": "/tmp/asset_catalog_export_link_demo/A.bin", "type": "image", "tags": ["demo", "ui"]}
```

```json
{"path": "/tmp/asset_catalog_export_link_demo/B.bin", "type": "audio", "tags": ["music"]}
```

（`add` 仅用于准备数据，不是本文描述的对象。）随后把 **A 的原路径删除**，在
原位置创建一个指向**未登记普通文件 T** 的符号链接，再写入 T（即链接先建、
目标后写，最终是一条有效链接）；B 保持原样：

```sh
rm /tmp/asset_catalog_export_link_demo/A.bin
ln -s /tmp/asset_catalog_export_link_demo/T.bin /tmp/asset_catalog_export_link_demo/A.bin
printf 'unregistered target T\n' > /tmp/asset_catalog_export_link_demo/T.bin
# readlink A.bin -> /tmp/asset_catalog_export_link_demo/T.bin
```

此时目录关系为：登记记录仍是 A、B 两条；文件系统中 `A.bin` 是指向未登记
`T.bin` 的符号链接，`B.bin` 是普通文件。

### 9.2 命令一：默认导出（不检查文件状态）

```sh
python -m asset_catalog --db catalog.sqlite export
```

退出码 0、标准错误为空，标准输出一行：

```json
[{"path": "/tmp/asset_catalog_export_link_demo/A.bin", "type": "image", "tags": ["demo", "ui"]}, {"path": "/tmp/asset_catalog_export_link_demo/B.bin", "type": "audio", "tags": ["music"]}]
```

仍按 A、B 顺序各返回一次，每条只有 `path`、`type`、`tags`，没有
`file_status`。A 的 `path` 仍是**原登记值** `…/A.bin`，不是链接目标
`…/T.bin`；结果中也没有 T 的记录。

### 9.3 命令二：检查文件状态导出

```sh
python -m asset_catalog --db catalog.sqlite export --check-files
```

退出码 0、标准错误为空，标准输出一行：

```json
[{"path": "/tmp/asset_catalog_export_link_demo/A.bin", "type": "image", "tags": ["demo", "ui"], "file_status": "present"}, {"path": "/tmp/asset_catalog_export_link_demo/B.bin", "type": "audio", "tags": ["music"], "file_status": "present"]}]
```

A 路径处是符号链接，`os.stat` 跟随到普通文件 T，故 A 为 `present`；B 本就是
普通文件，也为 `present`。两条记录都在原三键之上追加 `file_status`，顺序与
元数据不变；A 的 `path` 仍为 `…/A.bin`，T 不成为新增记录。

### 9.4 同一样例的状态变化

以下每一步都**不重新登记**，只改文件系统，再各跑一次默认导出与
`--check-files` 导出。A、B 的元数据与顺序在全过程中始终不变。

**① 删除 T，形成断链 → A 为 missing**

```sh
rm /tmp/asset_catalog_export_link_demo/T.bin
python -m asset_catalog --db catalog.sqlite export --check-files
```

```json
[{"path": "/tmp/asset_catalog_export_link_demo/A.bin", "type": "image", "tags": ["demo", "ui"], "file_status": "missing"}, {"path": "/tmp/asset_catalog_export_link_demo/B.bin", "type": "audio", "tags": ["music"], "file_status": "present"}]
```

断链被成功报告为 `missing`（不报错、不丢记录），B 仍 `present`。此时默认
`export` 仍返回 9.2 的两条原元数据，不含 `file_status`。导出**不会**重新创建
被删除的 T。

**② 在原位置按原内容重建 T → 下次导出恢复 present，无需重新登记**

```sh
printf 'unregistered target T\n' > /tmp/asset_catalog_export_link_demo/T.bin
python -m asset_catalog --db catalog.sqlite export --check-files
```

新启动的导出进程重新 `os.stat`，A、B 均回到 `present`，结果与 9.3 完全相同。
状态是当次计算的，不保存历史值，所以恢复文件即恢复状态，登记记录原封不动。

**③ 链接目标改为目录 → A 为 not_file**

用一组独立样例（或在本样例上）把 A 路径处的链接改指向一个目录 D，B 保持普通
文件：

```sh
rm /tmp/asset_catalog_export_link_demo/A.bin
mkdir /tmp/asset_catalog_export_link_demo/D
ln -s /tmp/asset_catalog_export_link_demo/D /tmp/asset_catalog_export_link_demo/A.bin
python -m asset_catalog --db catalog.sqlite export --check-files
```

```json
[{"path": "/tmp/asset_catalog_export_link_demo/A.bin", "type": "image", "tags": ["demo", "ui"], "file_status": "not_file"}, {"path": "/tmp/asset_catalog_export_link_demo/B.bin", "type": "audio", "tags": ["music"], "file_status": "present"}]
```

`os.stat` 跟随链接到目录，目录不是普通文件，A 判为 `not_file`；B 仍
`present`。默认 `export` 在此状态下仍返回 9.2 的两条原元数据、不含
`file_status`，链接指向的目录内容也不被改动。

以上四种状态（有效链接、断链、重建、指向目录）下：

- 默认导出的 JSON **逐字节一致**（都是 9.2 的两条三键记录）；
- 带 `--check-files` 的导出只改变 A 的 `file_status`，顺序、条数、`path`、
  `type`、`tags` 始终不变；
- 状态从不写入数据库，重复运行按当前文件系统即时判定。

### 9.5 任一记录状态无法判定：整次失败

为确定性复现“权限或其他系统错误”，可在子进程里让**某一条保存路径**的
`os.stat` 必抛 `PermissionError`（与回归测试相同的 `sitecustomize` 注入手法，
见 `tests/test_export_regression.py` 的 `make_stat_failure_env`，
`tests/test_export_regression.py:88-124`；不依赖真实权限位或账号身份）。对 B
的保存路径注入该错误后：

```sh
python -m asset_catalog --db catalog.sqlite export --check-files
```

结果（实测）：

- 退出码 **2**；
- 标准输出**为空（0 字节）**，没有 A 的半截记录，也没有部分 JSON；
- 标准错误为单行，含原因与 B 的保存路径，不含调用栈：

```
asset_catalog: 错误: 无法确定文件状态: /tmp/asset_catalog_export_link_demo/B.bin: Permission denied (regression test hook)
```

同样的故障条件下，默认 `python -m asset_catalog --db catalog.sqlite export`
不受影响：退出码 0、标准错误为空，输出 9.2 的两条完整元数据且无
`file_status`（默认不访问文件系统）。撤去注入条件后，`export --check-files`
立即恢复 A、B 均 `present`，无需任何修复性登记。

> 本文固定样例已在当前仓库实现上实际执行（Python 3、Linux、临时目录），
> 上述退出码、标准错误与各条 JSON 均与实测一致。

## 10. 只读与不变性边界

整条 export 流程（含 `--check-files`）满足：

- **不读取素材内容**：只对数据库执行 SELECT；状态检查仅 `os.stat` 读取 inode
  元数据，从不打开 A/B/T。
- **不改写数据库**：没有 INSERT/UPDATE/DELETE，状态不持久化；每次导出前后
  数据库字节一致。
- **不改写链接目标、不创建缺失目标**：断链时不会重建 T，也不会改动链接本身
  或链接指向目录的内容；`readlink` 指向保持不变。
- **不改变登记结果**：路径仍是原登记的规范绝对路径，类型、完整标签及顺序、
  首次登记顺序（A 在 B 前）都不变；链接目标 T 不会因为“被状态检查跟随”而
  成为目录记录。

## 11. 与现有导出回归测试的逐条对照

固定样例的每个结论都由现有回归测试锁定，可直接对照运行：

| 本文结论 | 回归测试用例 |
|---|---|
| 有效链接：A、B 均 `present`；A 的 path/type/tags 保持登记值；输出路径不是 T；T 不成为记录 | `tests/test_export_symlink_status_regression.py::test_link_to_unregistered_file_present_and_no_t_record`（`tests/test_export_symlink_status_regression.py:252-261`） |
| 删除 T 形成断链 → A `missing`、B `present`，两条都保留；重建 T 后新进程恢复 `present`，无需重新登记 | `tests/test_export_symlink_status_regression.py::test_broken_link_reports_missing_then_rebuilt_reports_present`（`tests/test_export_symlink_status_regression.py:263-280`） |
| 链接目标为目录 → A `not_file`、B `present`，顺序与元数据不变 | `tests/test_export_symlink_status_regression.py::test_link_to_directory_reports_not_file`（`tests/test_export_symlink_status_regression.py:319-348`） |
| 三种状态下导出均不改写数据库字节、B 与仍存在的 T 的内容及链接指向；不重建被删除的 T、不改链接目标目录 | `tests/test_export_symlink_status_regression.py::test_exports_readonly_database_b_and_target_bytes`（`tests/test_export_symlink_status_regression.py:282-317`） |
| 默认导出按登记顺序、每条一次、仅 path/type/tags；带选项导出追加 `file_status` 且保留全部记录与顺序 | `tests/test_export_regression.py::test_export_returns_all_records_in_registration_order`（`tests/test_export_regression.py:169-180`）、`test_check_files_appends_status_and_keeps_all_records`（`tests/test_export_regression.py:229-252`） |
| 默认导出不检查文件状态：源文件删除后记录仍在、无 `file_status` | `tests/test_export_regression.py::test_deleted_source_file_still_exported_without_file_status`（`tests/test_export_regression.py:182-190`） |
| 目录等非普通文件（直接替换原路径）判为 `not_file`，输出 path 保留登记值 | `tests/test_export_regression.py::test_check_files_reports_directory_as_not_file`（`tests/test_export_regression.py:254-265`） |
| 任一记录状态无法判定 → 退出码 2、标准输出为空、标准错误含原因与保存路径、无调用栈、无部分 JSON；默认导出在同条件下仍成功；撤去条件后恢复 | `tests/test_export_regression.py::test_check_files_status_error_fails_whole_export`（`tests/test_export_regression.py:274-325`，确定性 `os.stat` 注入构造于 `make_stat_failure_env`，`tests/test_export_regression.py:88-124`） |
| 空目录默认与带选项均输出 `[]` | `tests/test_export_regression.py::test_empty_database_exports_empty_array`（`tests/test_export_regression.py:208-215`）、`test_check_files_empty_database_outputs_empty_array`（`tests/test_export_regression.py:267-272`） |
| 导出不改登记内容与素材文件（导出后 query 结果不变、字节一致） | `tests/test_export_regression.py::test_export_does_not_change_registrations_or_files`（`tests/test_export_regression.py:192-206`） |
| export 拒绝 `--tag`/`--type`/`--file-status`/多余位置参数等不支持参数 | `tests/test_export_regression.py::test_unsupported_export_arguments_rejected`（`tests/test_export_regression.py:222-227`） |

符号链接专项测试在**确实无法创建符号链接**的环境会 `skipTest` 跳过并说明原因
（见 `tests/test_export_symlink_status_regression.py:102-120`），其他任何不符
都会使测试失败。运行入口：

```sh
python -m unittest tests.test_export_symlink_status_regression -v
python -m unittest tests.test_export_regression -v
```
