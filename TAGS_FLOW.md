# tags 标签统计流程说明

本文围绕 **一次 `tags` 统计**，说明一次
`python -m asset_catalog --db catalog.sqlite tags` 如何从**已保存的登记记录**
生成每个标签的素材使用数量：从公开入口讲到参数校验、数据库打开、统计范围、
计数、排序与 JSON 输出，并区分**不传 `--type`** 与**传入类型**的两条查询
路径。关键结论均标注实际源码路径与函数名（行号对应当前版本，函数名是稳定
锚点）。

本文只描述现有行为，不改变任何命令、数据库结构与 [README.md](README.md) 的
公开语义；除文档与固定示例外不交付代码改动，也不新增功能或改变测试预期。
`add`、`query`、`show`、`retag`、`retype`、`export` 的行为不在此展开。

## 1. 全流程总览

```
python -m asset_catalog --db catalog.sqlite tags [--type TYPE]
        │  asset_catalog/__main__.py  →  asset_catalog/cli.py: main()
        ▼
argparse 解析参数（_Parser.error：非法选项、缺值在此直接退出码 2）
        ▼
handle_tags（asset_catalog/cli.py:569）
  ① 可选类型校验：传了 --type 就 strip()，为空即拒绝（此时尚未打开数据库）
  ② open_database：打开库；不存在但父目录存在时初始化为空目录库
  ③ 统计范围只取数据库中已保存的记录：不扫描目录、不检查文件、不读素材内容
  ④ 计数（两条路径之一）：
       不传 --type：asset_tag 全表按 tag 分组
       传入 --type：JOIN asset 后按登记类型完整匹配再分组
     每组 COUNT(DISTINCT asset_id)
  ⑤ Python 按标签文本的 Unicode 码点字典序升序排序
        ▼
print(json.dumps(result, ensure_ascii=False))   # 单行 JSON 数组
退出码 0；成功时标准错误为空
```

任何阶段抛出 `CliError` 都由 `main()`（`asset_catalog/cli.py:845`，捕获于
`cli.py:848-852`）统一收敛：向标准错误输出 `asset_catalog: 错误: <原因>`，
返回退出码 2，此前标准输出为空，且不输出调用栈。`EXIT_OK = 0`、
`EXIT_ERROR = 2` 定义在 `cli.py:16-17`。

## 2. 入口与参数解析

- `python -m asset_catalog` 执行 `asset_catalog/__main__.py`，其中
  `sys.exit(main())` 进入 `cli.main`（`asset_catalog/cli.py:845`）。
- `main()` 先调用 `build_parser()`（`cli.py:35`）解析参数；`tags` 子解析器
  定义在 `cli.py:111-118`，只接受一个可选选项：
  - `--type`：可选素材类型，去除首尾空白后与登记类型完整匹配（区分大小写），
    帮助文本见 `cli.py:114-117`；
  - 子命令通过 `set_defaults(handler=handle_tags)`（`cli.py:118`）绑定到
    `handle_tags`。
- `tags` 不接受素材路径位置参数，也不接受 `--tag`、`--check-files` 等其他
  子命令的选项；这些在 argparse 阶段即被拒绝。
- argparse 层面的错误（缺 `--db`、`--type` 缺值、传入不支持的选项或位置
  参数）由 `_Parser.error`（`cli.py:30-32`）处理：标准错误先输出用法行，
  再输出一行 `asset_catalog tags: 错误: <选项>: <原因>`，退出码 2。此时
  `handle_tags` 尚未执行，**不会创建数据库文件**。

## 3. 参数校验（打开数据库之前）

`handle_tags`（`cli.py:569`）在调用 `open_database`（`cli.py:582`）之前
完成唯一一项手工校验（`cli.py:576-580`）：

- **不传 `--type`**：`args.type is None`，`asset_type` 保持 `None`，表示
  统计全目录，不做任何校验。
- **传入 `--type`**：对取值调用 `str.strip()` 去除首尾空白；去空白后为空
  字符串（即传入 `""` 或仅含空白，如 `"   "`）时抛出
  `素材类型 --type 去除首尾空白后不能为空`，经 `main()` 收敛为退出码 2。

该校验先于 `open_database`，因此空白类型等参数错误**不会创建数据库文件**：
即使 `--db` 指向一个尚不存在的路径，文件也不会出现（见
`tests/test_tags_regression.py:346-349`）。

类型匹配规则在第 5 节随 SQL 一并说明：去首尾空白后的文本与登记类型**完整
匹配**、**区分大小写**，不做子串匹配，也不按扩展名推断；`Image` 不匹配
`image`，`" image "` 去空白后与 `image` 等价。

## 4. 打开数据库

`open_database`（`cli.py:166-218`）打开 `--db` 指定的 SQLite 文件，规则对
全部子命令一致，`tags` 沿用，不做特殊处理：

1. `--db` 为空字符串时直接抛 `数据库路径不能为空`（`cli.py:171-172`）。
2. `sqlite3.connect(db_path)` 并清点 `sqlite_master` 中的用户表与用户视图
   （`cli.py:177-185`）。父目录缺失、路径指向目录、无权限、文件不是 SQLite
   数据库等情况在此处或首次 I/O 时抛出 `sqlite3.Error`，被包装为
   `无法打开数据库 <路径>: <原因>`（`cli.py:186-189`）。
3. 已存在 `asset`、`asset_tag` 两张表时，调用 `verify_schema`
   （`cli.py:241-249`）用 `PRAGMA table_info` 核对列集合：
   - `asset` 必须恰为 `{id, path, type}`（`ASSET_COLUMNS`，`cli.py:19`）；
   - `asset_tag` 必须恰为 `{asset_id, tag, position}`（`TAG_COLUMNS`，
     `cli.py:20`）；
   - 列不一致时抛 `数据库表结构与本产品不兼容，拒绝覆盖或重建`
     （`cli.py:248-249`）。
4. 存在其他用户表或用户视图却不具备两张兼容表时，抛
   `数据库 <路径> 结构不属于本产品，拒绝覆盖或重建`（`cli.py:199-204`）。
5. 既无用户表也无用户视图（文件尚不存在、零字节文件或完全空白的 SQLite
   库）时，由 `create_schema`（`cli.py:221-238`）初始化为空目录库并提交：

   - `asset(id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE,
     type TEXT NOT NULL)`；`id` 的自增顺序即首次登记顺序。
   - `asset_tag(asset_id INTEGER NOT NULL REFERENCES asset(id), tag TEXT
     NOT NULL, position INTEGER NOT NULL, PRIMARY KEY (asset_id, tag))`，
     外加标签索引 `idx_asset_tag_tag`。

因此**父目录存在而数据库文件不存在**时，一次合法的 `tags` 会留下一个空库
并输出 `[]`；**父目录缺失**时第 2 步即报错，目录不会被补建。结构被拒绝时
走 `except CliError` 分支（`cli.py:212-216`），只关闭连接、不做任何写入，
原文件不被覆盖或重建。

## 5. 统计范围与计数：两条路径

统计逻辑全部在 `handle_tags` 的 `try` 块中（`cli.py:583-607`），读取结束
后在 `finally` 中关闭连接（`cli.py:610-611`）。`tags` 只查询数据库中**当前
保存的记录**（注释见 `cli.py:570-575`）：

- **不扫描目录**：不遍历任何磁盘目录寻找素材；
- **不检查文件状态**：不调用 `os.stat`，因此源文件已删除或登记路径已变成
  目录的素材**仍参与统计**；
- **不读取素材内容**：只读取 `asset`、`asset_tag` 两张表的元数据；
- **不写入**：正常统计不改动数据库与素材文件（唯一例外是第 4 节第 5 条的
  新库初始化）。

### 5.1 路径一：不传 `--type`（全目录统计）

`asset_type is None` 时执行（`cli.py:587-594`）：

```sql
SELECT tag, COUNT(DISTINCT asset_id) AS asset_count
FROM asset_tag
GROUP BY tag
```

对标签表中的**每个不同标签文本**各产生一行，数量为使用该标签的不同素材数。

### 5.2 路径二：传入 `--type`（按类型筛选后统计）

`asset_type` 非空时执行（`cli.py:595-607`），类型值以参数绑定传入：

```sql
SELECT t.tag, COUNT(DISTINCT t.asset_id) AS asset_count
FROM asset_tag t
JOIN asset a ON a.id = t.asset_id
WHERE a.type = ?
GROUP BY t.tag
```

先通过 `JOIN` 与 `WHERE a.type = ?` 把范围限定为登记类型与去空白后的取值
**完整相等**的素材，再对这些素材的标签分组。无素材匹配该类型时结果为空，
随后输出 `[]`，命令仍然成功。

### 5.3 计数语义（两条路径共同遵守）

- **同一素材的同一标签只计一次。** 数量用 `COUNT(DISTINCT asset_id)`
  计算；`asset_tag` 的主键是 `(asset_id, tag)`（`cli.py:233`），每个素材的
  每个标签在表中至多一行——`add` 登记时重复传入同一标签会由
  `normalize_tags`（`cli.py:252-267`）按首次出现顺序去重。`DISTINCT` 与
  主键约束共同保证该语义显式且不依赖偶然数据。
- **不同登记路径即使内容相同仍分别计数。** 不同路径在 `asset` 表中是不同
  的行、不同的 `id`（`path` 有 UNIQUE 约束，`cli.py:226`），统计数的是
  素材（`asset_id`）而非文件内容；内容字节一致不产生合并。
- **标签区分大小写、不改写文本。** `GROUP BY tag` 按标签完整文本分组，
  SQLite 的 TEXT 比较不做大小写折叠，因此 `UI` 与 `ui` 是两个标签，各自
  独立计数、各自输出一行。
- **不输出零计数标签。** 标签不是来自一张独立的标签字典，而只来自
  `asset_tag` 中实际存在的行；`GROUP BY` 只产生至少有一条标签行的分组，
  所以没有任何素材使用的标签无处产生，输出中不会出现 `asset_count` 为 0
  的项；每项的 `asset_count` 必为正整数。
- 读取过程若发生 SQLite 错误（例如查询中途遇到损坏页），包装为
  `数据库读取失败: <原因>`（`cli.py:608-609`），退出码 2，不输出部分数组。

## 6. 排序与 JSON 输出

SQL 本身不带 `ORDER BY`；排序在 Python 侧完成（`cli.py:615-618`）：

```python
result = [
    {"tag": tag, "asset_count": asset_count}
    for tag, asset_count in sorted(rows, key=lambda row: row[0])
]
```

- 每个标签只出现一次，按标签文本的 **Unicode 码点字典序升序**排列
  （Python 字符串默认按码点比较，不做大小写折叠）。例如大写字母
  `U`（U+0055）先于小写 `d`（U+0064），`d` 又先于小写 `u`（U+0075），
  故顺序为 `UI`、`demo`、`ui`。
- 每项是且仅含两个键的对象：`tag`（标签原文）与 `asset_count`（计数
  整数），不输出素材 id、路径、类型等其他字段。
- 空目录、无匹配类型或筛选后无标签时，列表为空。

随后 `handle_tags` 在 `cli.py:619` 执行
`print(json.dumps(result, ensure_ascii=False))` 并返回 0（`cli.py:620`）：

- 标准输出**恰好一行** JSON 数组（`print` 追加唯一换行符），成功时标准
  错误为空，退出码 0；中文等非 ASCII 标签按原文输出（`ensure_ascii=False`）。

## 7. 固定演示

样例目录固定为 `/srv/asset-catalog-example/`，数据库为其中的
`catalog.sqlite`。两个素材文件内容任意（统计不读取内容），按下列顺序登记：

- `A.bin`，类型 `image`，标签依次为 `demo`、`ui`、`UI`；
- `B.bin`，类型 `audio`，标签为 `demo`。

```sh
python -m asset_catalog --db /srv/asset-catalog-example/catalog.sqlite \
  add /srv/asset-catalog-example/A.bin --type image \
  --tag demo --tag ui --tag UI
python -m asset_catalog --db /srv/asset-catalog-example/catalog.sqlite \
  add /srv/asset-catalog-example/B.bin --type audio --tag demo
```

登记后的保存记录（与文件内容无关）：

| 素材 | 类型 | 已保存标签（登记顺序） |
|---|---|---|
| `A.bin` | `image` | `demo`, `ui`, `UI` |
| `B.bin` | `audio` | `demo` |

### 7.1 统计一：全目录（不传 `--type`，走 5.1 路径）

```sh
python -m asset_catalog --db /srv/asset-catalog-example/catalog.sqlite tags
```

逐项推演：

| 标签 | 命中的素材 | `asset_count` |
|---|---|---|
| `UI` | A（`ui` 与 `UI` 是两个标签，不合并） | 1 |
| `demo` | A、B（不同登记路径分别计数） | 2 |
| `ui` | A | 1 |

退出码 0，标准错误为空，标准输出仅一行数组：

```json
[{"tag": "UI", "asset_count": 1}, {"tag": "demo", "asset_count": 2}, {"tag": "ui", "asset_count": 1}]
```

### 7.2 统计二：追加 `--type " image "`（走 5.2 路径）

```sh
python -m asset_catalog --db /srv/asset-catalog-example/catalog.sqlite \
  tags --type " image "
```

`" image "` 去除首尾空白后为 `image`，与登记类型完整、区分大小写匹配：只有
A 入选，B 的类型 `audio` 不参与。三个标签的顺序与统计一完全相同，数量各为
1：

| 标签 | 命中的 image 素材 | `asset_count` |
|---|---|---|
| `UI` | A | 1 |
| `demo` | A（B 为 audio，不计入） | 1 |
| `ui` | A | 1 |

退出码 0，标准错误为空，标准输出仅一行数组：

```json
[{"tag": "UI", "asset_count": 1}, {"tag": "demo", "asset_count": 1}, {"tag": "ui", "asset_count": 1}]
```

补充核对（同一规则的延伸，均退出码 0、标准错误为空、输出一行数组）：

- `tags --type Image`：区分大小写，`Image` 不匹配任何记录，输出 `[]`；
- `tags --type video`：无匹配类型，输出 `[]`；
- 对一个父目录存在、尚不存在的数据库文件执行 `tags`（可带任意合法
  `--type`）：沿用第 4 节建空库规则，文件被创建为空目录库，输出 `[]`。

## 8. 失败情形：参数失败与数据库失败

两类失败**都**表现为退出码 2、标准输出为空、标准错误给出原因且不含调用栈
（`main()` 的统一收敛，`cli.py:850-852`；唯一的 `print` 在 `cli.py:619`，
任何失败都先于它发生），区别在于失败阶段与数据库是否被触碰。

### 8.1 参数失败：发生在打开数据库之前，不创建数据库

| 情形 | 拒绝位置 | 标准错误要点 |
|---|---|---|
| `--type` 缺值（命令以 `--type` 结尾） | argparse，`_Parser.error`（`cli.py:30-32`） | 用法行 + 指明 `argument --type: expected one argument` |
| `--type ""`、`--type "   "`（为空或仅有空白） | `handle_tags`（`cli.py:577-580`） | `素材类型 --type 去除首尾空白后不能为空`，指出选项与原因 |
| 传入素材路径位置参数、`--tag`、`--check-files` 等不支持参数 | argparse | 用法行 + 指出不被识别的参数 |
| 缺少全局必填的 `--db` | argparse | 用法行 + 指出 `--db` 必填 |

参数失败时 `handle_tags` 未执行或在校验处即返回，`open_database` 不会被
调用：**数据库文件不被创建**，已存在的数据库字节不变。

### 8.2 数据库失败：退出码 2，原文件不被覆盖或重建

| 情形 | 检测位置 | 标准错误中的原因（前缀均为 `asset_catalog: 错误:`） |
|---|---|---|
| 父目录缺失 | `open_database`（`cli.py:186-189`） | `无法打开数据库 <路径>: unable to open database file`；缺失目录不补建 |
| `--db` 指向目录 | 同上 | `无法打开数据库 <路径>: unable to open database file` |
| 文件存在但不是 SQLite 数据库 | 同上 | `无法打开数据库 <路径>: file is not a database` |
| SQLite 镜像损坏（如截断一半） | 打开/清点或首次读取时 | `无法打开数据库 <路径>: database disk image is malformed`（读取中途损坏则为 `数据库读取失败: ...`，`cli.py:608-609`） |
| 含其他用户表或用户视图 | `open_database`（`cli.py:199-204`） | `数据库 <路径> 结构不属于本产品，拒绝覆盖或重建` |
| `asset` / `asset_tag` 列名不兼容 | `verify_schema`（`cli.py:241-249`） | `数据库表结构与本产品不兼容，拒绝覆盖或重建` |

这些分支的共同点：标准输出为空，错误消息只说明原因、**不含 Traceback**；
被拒绝的文件保持原样——结构拒绝分支只关闭连接、不执行任何写语句
（`cli.py:212-216`），非 SQLite 或损坏文件也不会被初始化或覆盖。与之相对，
父目录存在而数据库不存在是**成功**路径：建空库、提交、输出 `[]`。

## 9. 与现有回归测试的核对

下列用例全部位于 `tests/test_tags_regression.py`，通过
`python -m asset_catalog` 公开入口以子进程观察行为，是本文每条结论的核对
依据；本次未新增功能，也未改变任何测试预期。执行方式：

```sh
python -m unittest tests.test_tags_regression -v
```

- 公共断言：`run_cli`（`:52-67`）、`assertTagsOk`（退出码 0、标准错误为空、
  标准输出恰一行、JSON 为数组且逐字相等，`:97-106`）、`assertTagsError`
  （退出码 2、标准输出为空、标准错误非空且无 `Traceback`，`:108-114`）。
- `test_tags_counts_case_sensitive_and_sorted`（`:116-137`）：对应第 5.3、
  6、7.1 节——`UI` 计 1、`demo` 计 2、`ui` 计 1，每项仅含 `tag` 与
  `asset_count`，标签不重复、按码点升序、数量为正整数。
- `test_same_tag_same_asset_counted_once`（`:139-152`）：同一素材重复登记
  同一标签后该标签只计 1。
- `test_same_content_different_paths_counted_separately`（`:154-163`）：
  内容相同的两个路径分别计数，`demo` 计 2。
- `test_retag_changes_reflected_in_next_run`（`:165-184`）：统计只反映已
  保存记录，retag 后的新标签在下次及后续新进程中一致生效。
- `test_deleted_or_replaced_source_still_counted`（`:186-201`）：源文件
  删除或原路径变成目录后仍参与统计，结果不变。
- `test_tags_does_not_change_database_or_files`（`:203-218`）：统计前后
  数据库文件与素材文件按字节比较完全一致。
- `test_empty_database_outputs_empty_array`（`:220-227`）：父目录存在而
  数据库不存在时建空库并输出 `[]`，新进程复读一致。
- `test_tags_type_filters_by_exact_type`（`:248-271`）：`--type image`
  只统计 image 素材（`UI` 1、`demo` 2、`ui` 1，audio 的 `sound` 不参与），
  不带 `--type` 时恢复全目录统计。
- `test_tags_type_strips_surrounding_whitespace`（`:273-285`）：对应第
  7.2 节，`--type " image "` 与 `image` 等价。
- `test_tags_type_case_sensitive_and_unmatched_outputs_empty`（`:287-292`）：
  `Image` 不匹配、无匹配类型均成功输出 `[]`。
- `test_tags_type_empty_database_outputs_empty_array`（`:294-299`）：空库
  搭配 `--type` 仍沿用建空库规则并输出 `[]`。
- `test_tags_type_reflects_retype_and_retag`（`:301-333`）：retype 与
  retag 改动保存记录后，按类型统计随之变化。
- `test_tags_type_missing_or_blank_value_rejected`（`:335-344`）与
  `test_tags_type_blank_value_does_not_create_database`（`:346-349`）：
  对应第 8.1 节——`--type` 缺值、为空、仅有空白时退出码 2、标准输出为空，
  且不创建数据库、不改动已有数据库。
- `test_missing_parent_directory_is_not_created`（`:351-354`）：父目录
  缺失时报错且不补建目录。
- `test_unsupported_tags_arguments_rejected`（`:356-363`）与
  `test_unsupported_arguments_do_not_create_database`（`:365-368`）：位置
  参数、`--tag`、`--check-files` 被拒绝，且不创建数据库。
- `test_database_path_is_directory_rejected`（`:370-373`）、
  `test_empty_database_path_rejected`（`:375-380`）、
  `test_missing_db_option_rejected`（`:382-392`）：数据库路径为目录、
  `--db` 为空、缺少 `--db` 均退出码 2、标准输出为空、无调用栈。
- `test_corrupt_and_incompatible_databases_rejected_and_untouched`
  （`:394-434`）：对应第 8.2 节——非 SQLite 文件、含其他业务表的库、缺列
  的不兼容结构均被拒绝，原文件字节保持不变，原有记录保留。
- `test_malformed_database_produces_no_partial_output`（`:436-445`）：
  镜像截断损坏时整次失败，不输出部分数组。
