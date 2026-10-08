# tags 标签使用数量统计流程说明

本文对应 `asset_catalog/cli.py` 中 tags（列出全部已用标签及使用各标签的已
登记素材数）这条既有流程的实际函数，说明标签使用数量如何从数据库中已保存的
记录生成，便于逐条核对。行号对应当前版本，函数名是稳定锚点。本次只补充说明：
命令入口、参数、JSON 输出格式、SQLite 结构与数据库兼容规则均未改变，不新增
功能，也不改变任何测试预期。

## 1. 全流程总览

公开入口（README 同款用法）：

```
python -m asset_catalog --db catalog.sqlite tags [--type TYPE]
        │  python -m asset_catalog
        │  asset_catalog/__main__.py → from .cli import main（__main__.py:3-6）
        ▼
argparse 解析（build_parser，cli.py:35；--db 全局必填定义在 cli.py:37-42；
tags 子命令定义在 cli.py:111-118）：
tags 只有一个可选参数 --type；位置参数、--tag、--check-files 等均不支持，
--type 缺值由 argparse 拒绝。参数错误退出码 2、标准输出为空、不创建数据库
        ▼
handle_tags（cli.py:569）按顺序编排下列步骤：
  ① --type 校验与规范化（cli.py:576-580）：不传时 asset_type 为 None；
     传入则去除首尾空白，去空白后为空即报错。① 在打开数据库之前，
     参数错误不建库
  ② open_database（cli.py:166）：打开（必要时创建）数据库并校验结构
  ③ 统计查询（cli.py:587-607），按是否传 --type 分两条路径：
     · 不传：直接对 asset_tag 按 tag 分组（cli.py:587-594）
     · 传入：JOIN asset 后按 a.type 完整匹配再分组（cli.py:595-607）
  ④ 排序与组装（cli.py:615-618）：按标签文本 Unicode 码点字典序升序，
     每项只组装 tag 与 asset_count
        ▼
标准输出仅一行 JSON 数组：[{"tag": ..., "asset_count": N}, ...]，退出码 0
（cli.py:619-620）；空目录或无匹配类型输出 []
```

任何阶段抛出 `CliError` 都由 `main()`（cli.py:845-852）统一收敛：标准错误
输出 `asset_catalog: 错误: <原因>`（不含调用栈），退出码 2，标准输出为空。
argparse 自身的参数错误走 `_Parser.error`（cli.py:30-32）：先向标准错误打印
usage，再以退出码 2 输出单行 `asset_catalog: 错误: <message>`，同样保证标准
输出为空。

## 2. 参数校验：参数失败发生在打开数据库之前

tags 子命令只注册一个可选参数 `--type`（cli.py:114-117），帮助文本明确其语义
为“去除首尾空白后与登记类型完整匹配（区分大小写）”。

- **不传 `--type`**：`args.type is None`，`asset_type` 保持 `None`
  （cli.py:576-577），统计全目录，见第 4 节路径 A。
- **传入 `--type`**：执行 `args.type.strip()`（cli.py:578）。去除首尾空白后
  必须非空，否则抛 `CliError("素材类型 --type 去除首尾空白后不能为空")`
  （cli.py:579-580）。规范化后的值保留内部空白与大小写，接受任意非空文本，
  不按扩展名推断。
- **`--type` 缺值**（命令行写了 `--type` 却没有后续值）：由 argparse 在解析
  阶段拒绝，标准错误含 usage 与 `argument --type: expected one argument`，
  退出码 2。
- **不支持的参数**：tags 不接受素材位置路径、`--tag`、`--check-files` 等，
  argparse 以 “unrecognized arguments” 拒绝，退出码 2。
- **缺少全局必填的 `--db`、`--db` 为空字符串**：同样在解析阶段或
  `open_database` 入口（cli.py:171-172，“数据库路径不能为空”）以退出码 2
  拒绝。

以上全部发生在 `open_database`（cli.py:582）之前，因此**参数错误不创建数据
库文件**：指向尚不存在的路径时，失败后该路径仍不存在。

## 3. 数据库打开与兼容规则（沿用 open_database，tags 不特殊处理）

`open_database`（cli.py:166-218）是所有子命令共用的同一处规则，tags 原样
沿用：

1. `sqlite3.connect` 打开（cli.py:177）。父目录缺失、路径是目录、无权限等在
   此处或首次 I/O 报 `sqlite3.Error`，收敛为
   `无法打开数据库 <路径>: <数据库给出的原因>`（cli.py:186-189），**不补建
   缺失的父目录**。
2. 清点库内用户表与用户视图（cli.py:182-185）：
   - 同时存在 `asset` 与 `asset_tag` 两张表 → `verify_schema`
     （cli.py:241-249）用 `PRAGMA table_info` 核对列集合必须恰好为
     `ASSET_COLUMNS = {"id", "path", "type"}`（cli.py:19）与
     `TAG_COLUMNS = {"asset_id", "tag", "position"}`（cli.py:20）；列名不
     兼容（如 `asset` 缺少 `type` 列）即报
     `数据库表结构与本产品不兼容，拒绝覆盖或重建`。
   - 有任意其他用户表或用户视图却不具备两张兼容表 → 报
     `结构不属于本产品，拒绝覆盖或重建`（cli.py:199-204），不补表、不重建。
   - 既无用户表也无用户视图（路径尚不存在、零字节文件或空白 SQLite 库）→
     `create_schema`（cli.py:221-238）初始化为空目录库。即**父目录存在而
     数据库文件不存在时，沿用建空库规则**，随后统计得到 `[]`。
3. 查询中途的 `sqlite3.Error`（含镜像损坏等读取失败）由 handle_tags 收敛为
   `数据库读取失败: <原因>`（cli.py:608-609），`finally` 中关闭连接
   （cli.py:610-611）；唯一的 `print` 在这之后，因此**不输出部分数组**。

所有数据库失败均为退出码 2、标准输出为空、标准错误单行说明原因且不含调用栈；
被拒绝的文件字节保持不变，不被覆盖或重建。

## 4. 统计范围与两条计数路径

统计只读取数据库中**当前保存的登记记录**（cli.py:570-575 的注释即此口径）：

- 不读取素材内容、不解码文件；
- 不检查登记路径当前的文件状态，也不扫描目录——源文件事后被删除或原路径变成
  目录的素材仍参与统计；
- 统计是只读操作，不写入数据库，也不创建、移动或改写素材文件。

标签唯一性的结构前提：`asset_tag` 的主键是 `PRIMARY KEY (asset_id, tag)`
（cli.py:233，建表语句见 cli.py:229-234），同一素材的同一标签在存储层只有
一行；`add` 的 `normalize_tags`（cli.py:252-267）还会在登记时按首次出现顺序
去重。因此“同一素材的同一标签只计一次”由存储结构与查询双重保证。

### 路径 A：不传 `--type` —— 全目录统计（cli.py:587-594）

```sql
SELECT tag, COUNT(DISTINCT asset_id) AS asset_count
FROM asset_tag
GROUP BY tag
```

- 分组键是标签的完整文本，**区分大小写、不改写文本**：`ui` 与 `UI` 是两个
  独立分组。
- 计数使用 `COUNT(DISTINCT asset_id)`：同一素材的同一标签只计一次（主键已
  保证唯一，`DISTINCT` 使语义显式）；不同登记路径是 `asset` 表中不同的行、
  不同的 `asset_id`，即使两个文件内容完全相同，也分别计入。
- 没有任何素材使用的标签根本不在 `asset_tag` 中，自然不会产生零计数项。

### 路径 B：传入 `--type` —— 按类型筛选后统计（cli.py:595-607）

```sql
SELECT t.tag, COUNT(DISTINCT t.asset_id) AS asset_count
FROM asset_tag t
JOIN asset a ON a.id = t.asset_id
WHERE a.type = ?
GROUP BY t.tag
```

- 以第 2 节规范化后的类型值作为**唯一**绑定参数（cli.py:606），与登记类型
  做完整匹配、区分大小写：`" image "` 去空白后等于 `image` 可以命中，
  `Image` 不匹配 `image`。
- 计数、分组与去重语义与路径 A 完全相同，只是范围先收敛到该类型的素材。
- 没有任何素材匹配该类型时结果集为空，成功输出 `[]`，退出码仍为 0。

## 5. 排序与 JSON 输出

SQL 只做 `GROUP BY`，不做 `ORDER BY`；排序统一在 Python 侧完成
（cli.py:615-618）：

```python
result = [
    {"tag": tag, "asset_count": asset_count}
    for tag, asset_count in sorted(rows, key=lambda row: row[0])
]
print(json.dumps(result, ensure_ascii=False))
```

- 每个标签只出现一次，按标签文本的 **Unicode 码点字典序升序**排列
  （Python 字符串按码点比较）。例如 `U+0055`（`U`）先于 `U+0064`（`d`）
  与 `U+0075`（`u`），故顺序为 `UI` → `demo` → `ui`。
- 每项恰好含 `tag`、`asset_count` 两个键：`tag` 为保存的原文本，
  `asset_count` 为正整数；无路径、类型、位置或内部编号等其他字段。
- `json.dumps(..., ensure_ascii=False)` 后由唯一一次 `print` 输出，因此标准
  输出**仅一行 JSON 数组**，非 ASCII 标签按原字符输出；空结果为 `[]`。
- `retag` / `retype` 对保存记录的修改在下次（乃至新进程的）统计中自然生效，
  无需迁移。

## 6. 固定演示：两个演示文件的完整命令与预期 JSON

### 目录与准备

- 命令在项目根目录执行（保证 `asset_catalog` 包可导入）；样例库与素材放在
  独立临时目录 `$SAMPLE` 中，仅含 `A.bin`、`B.bin` 两个演示文件与数据库
  `catalog.sqlite`。
- `A.bin` 类型为 `image`，标签按登记顺序依次为 `demo`、`ui`、`UI`；
  `B.bin` 类型为 `audio`，标签为 `demo`。

```bash
printf 'demo asset A\n' > "$SAMPLE/A.bin"
printf 'demo asset B\n' > "$SAMPLE/B.bin"

python -m asset_catalog --db "$SAMPLE/catalog.sqlite" \
    add "$SAMPLE/A.bin" --type image --tag demo --tag ui --tag UI
python -m asset_catalog --db "$SAMPLE/catalog.sqlite" \
    add "$SAMPLE/B.bin" --type audio --tag demo
```

两次 add 均退出码 0、标准错误为空；add 各自输出一行登记结果（tags 统计不
依赖这两行）：

```json
{"path": "<$SAMPLE 的规范绝对路径>/A.bin", "type": "image", "tags": ["demo", "ui", "UI"]}
{"path": "<$SAMPLE 的规范绝对路径>/B.bin", "type": "audio", "tags": ["demo"]}
```

### 统计一：全目录（不传 `--type`）

```bash
python -m asset_catalog --db "$SAMPLE/catalog.sqlite" tags
```

预期：退出码 0，标准错误为空，标准输出仅一行数组，依次为 `UI` 计一、
`demo` 计二、`ui` 计一：

```json
[{"tag": "UI", "asset_count": 1}, {"tag": "demo", "asset_count": 2}, {"tag": "ui", "asset_count": 1}]
```

逐项核对：

| 标签 | 命中的素材 | asset_count | 依据 |
|------|------------|-------------|------|
| `UI` | 仅 A.bin | 1 | 大写 `UI` 与小写 `ui` 分为两组 |
| `demo` | A.bin 与 B.bin | 2 | 不同登记路径分别计数，即使内容相似 |
| `ui` | 仅 A.bin | 1 | 同一素材只计一次（A 登记时该标签只出现一次） |

### 统计二：追加 `--type " image "`

```bash
python -m asset_catalog --db "$SAMPLE/catalog.sqlite" tags --type " image "
```

类型值去除首尾空白后完整匹配到 `image`，B.bin（`audio`）被排除；标签集合与
排列顺序不变，三个标签各计一：

```json
[{"tag": "UI", "asset_count": 1}, {"tag": "demo", "asset_count": 1}, {"tag": "ui", "asset_count": 1}]
```

两次统计的退出码均为 0、标准错误为空、标准输出均只有一行数组。排序与筛选、
计数位置的逐项对应关系：去空白匹配发生在 cli.py:578 与 SQL 的
`WHERE a.type = ?`（cli.py:603-606），计数发生在
`COUNT(DISTINCT t.asset_id)`（cli.py:600），排序发生在
`sorted(rows, key=lambda row: row[0])`（cli.py:617）。

### 同库上的两个 `[]` 边界（退出码同为 0）

```bash
# 大小写不符：Image 不匹配 image，无匹配类型成功输出 []
python -m asset_catalog --db "$SAMPLE/catalog.sqlite" tags --type Image
# 父目录存在而数据库不存在：沿用建空库规则，初始化后输出 []
python -m asset_catalog --db "$SAMPLE/fresh.sqlite" tags
```

两次标准输出均为单行 `[]`，标准错误为空，退出码 0；且 `fresh.sqlite` 在
命令结束后已作为空目录库存在。

**验证状态**：以上命令已在本仓库当前实现上实际执行（Python 3、Linux、临时
目录），两条 add、全目录统计、`--type " image "`、`--type Image` 与空库
统计的退出码、标准错误和单行 JSON 内容均与上述预期一致。

## 7. 参数失败与数据库失败的边界

两类失败外观相同（退出码 2、标准输出为空、标准错误单行且无调用栈），但发生
时机与副作用不同：

| 情形 | 发生位置 | 标准错误要点 | 副作用 |
|------|----------|--------------|--------|
| `--type` 缺值 | argparse（cli.py:30-32、114-117） | usage 加 `argument --type: expected one argument` | 不创建数据库 |
| `--type` 为空或仅空白 | handle_tags ①（cli.py:578-580） | `素材类型 --type 去除首尾空白后不能为空` | 不创建数据库 |
| 位置路径 / `--tag` / `--check-files` 等不支持参数 | argparse | `unrecognized arguments ...` | 不创建数据库 |
| 缺 `--db`、`--db` 为空 | argparse / cli.py:171-172 | 指出 `--db` 或 `数据库路径不能为空` | 不创建数据库 |
| 数据库无法打开（路径为目录、父目录缺失、无权限等） | open_database（cli.py:177、186-189） | `无法打开数据库 <路径>: <原因>` | 不补建父目录、不重建文件 |
| 数据库内容损坏 / 读取失败 | open_database 或查询（cli.py:209-211、608-609） | `已损坏或无法读写` / `数据库读取失败: <原因>` | 原文件不被覆盖或重建，不输出部分数组 |
| 列名不兼容 / 非本产品结构 | verify_schema（cli.py:241-249）、cli.py:199-204 | `数据库表结构与本产品不兼容，拒绝覆盖或重建` | 不补表、不重建，字节保持不变 |

实测的标准错误文本示例：

```
# --type 缺值（argparse，含一行 usage 后接单行错误）
asset_catalog tags: 错误: argument --type: expected one argument

# --type 为空或仅空白
asset_catalog: 错误: 素材类型 --type 去除首尾空白后不能为空

# 父目录缺失 / 路径是目录
asset_catalog: 错误: 无法打开数据库 .../no/such/c.sqlite: unable to open database file

# 非 SQLite 文件
asset_catalog: 错误: 无法打开数据库 .../bad.sqlite: file is not a database
```

源文件状态不影响统计，已实测核对：登记后删除 `B.bin`、删除 `A.bin` 并把原
路径建成目录，再执行全目录 `tags`，仍退出码 0 并输出与第 6 节统计一完全相同
的数组；正常统计前后数据库文件字节与素材文件字节均不变。

## 8. 回归对照（tests/test_tags_regression.py，现有用例）

以下均为该文件中**现有**用例，本文每条行为都可与其逐项核对，未新增功能或
改变测试预期：

- `test_tags_counts_case_sensitive_and_sorted`：每项仅含 `tag` 与
  `asset_count`、标签不重复、数量为正整数、按 Unicode 码点字典序升序
  （大写先于小写）。
- `test_same_tag_same_asset_counted_once`：同一素材重复登记同一标签只计一次。
- `test_same_content_different_paths_counted_separately`：不同登记路径即使
  文件内容相同仍分别计数。
- `test_retag_changes_reflected_in_next_run`：retag 后下次统计与新进程统计
  一致。
- `test_deleted_or_replaced_source_still_counted`：源文件删除或原路径变成
  目录后仍参与统计。
- `test_tags_does_not_change_database_or_files`：统计前后数据库与素材文件
  字节不变。
- `test_empty_database_outputs_empty_array`：父目录存在、库不存在时建空库并
  输出 `[]`，新进程复跑一致。
- `test_tags_type_filters_by_exact_type`：`--type image` 只统计 image 素材，
  不传时为全目录统计（第 4 节两条路径的直接对照）。
- `test_tags_type_strips_surrounding_whitespace`：`" image "` 去首尾空白后
  与 `image` 等价——即第 6 节统计二的核对依据。
- `test_tags_type_case_sensitive_and_unmatched_outputs_empty`：`Image` 不
  匹配 `image`，无匹配类型 `video` 成功输出 `[]`。
- `test_tags_type_empty_database_outputs_empty_array`：空目录搭配 `--type`
  沿用空库创建规则并输出 `[]`。
- `test_tags_type_reflects_retype_and_retag`：retype / retag 之后分类型统计
  相应变化。
- `test_tags_type_missing_or_blank_value_rejected`：`--type` 缺值、为空、
  仅空白均退出码 2、标准输出为空，且既有数据库字节不变。
- `test_tags_type_blank_value_does_not_create_database`：仅空白的 `--type`
  不创建数据库文件。
- `test_missing_parent_directory_is_not_created`：父目录缺失时报错且不补建
  目录。
- `test_unsupported_tags_arguments_rejected` /
  `test_unsupported_arguments_do_not_create_database`：位置路径、`--tag`、
  `--check-files` 被拒绝且不建库。
- `test_database_path_is_directory_rejected` /
  `test_empty_database_path_rejected` /
  `test_missing_db_option_rejected`：数据库路径为目录、`--db` 为空、缺少
  `--db` 均退出码 2、标准输出为空、无调用栈。
- `test_corrupt_and_incompatible_databases_rejected_and_untouched`：非
  SQLite 文件、含其他业务表的库、`asset` 缺列的不兼容结构均被拒绝，文件字节
  与原有记录保持不变。
- `test_malformed_database_produces_no_partial_output`：镜像损坏时整次失败，
  不输出部分数组。

以上 22 个用例当前全部通过（`python3 -m unittest tests.test_tags_regression`，
Python 3、Linux）。
