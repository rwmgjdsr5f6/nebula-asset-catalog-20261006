# tags 标签统计流程说明

本文围绕 **一次 `tags` 统计**，说明一次
`python -m asset_catalog --db catalog.sqlite tags` 如何从**已保存的登记记录**
生成每个标签的素材使用数量：从公开入口讲到参数校验、数据库打开、统计范围、
计数、前缀筛选、排序与 JSON 输出，并区分**不传筛选选项**、**只传 `--type`**
与**传入 `--prefix`（可与 `--type` 同用）**的路径。关键结论均标注实际源码
路径与函数名（行号对应当前版本，函数名是稳定锚点）。

本文描述现有行为；`--prefix` 是在原有 `tags` 上新增的可选筛选，不改变任何
命令、数据库结构与 [README.md](README.md) 的其余公开语义。
`add`、`query`、`show`、`retag`、`retype`、`export` 的行为不在此展开。

## 1. 全流程总览

```
python -m asset_catalog --db catalog.sqlite tags [--type TYPE] [--prefix PREFIX]
        │  asset_catalog/__main__.py  →  asset_catalog/cli.py: main()
        ▼
argparse 解析参数（_Parser.error：非法选项、缺值在此直接退出码 2）
        ▼
handle_tags（asset_catalog/cli.py:577）
  ① 可选类型校验：传了 --type 就 strip()，为空即拒绝（此时尚未打开数据库）
  ② 可选前缀校验：传了 --prefix 就 strip()，为空即拒绝（此时尚未打开数据库）
  ③ open_database：打开库；不存在但父目录存在时初始化为空目录库
  ④ 统计范围只取数据库中已保存的记录：不扫描目录、不检查文件、不读素材内容
  ⑤ 计数（两条路径之一）：
       不传 --type：asset_tag 全表按 tag 分组
       传入 --type：JOIN asset 后按登记类型完整匹配再分组
     每组 COUNT(DISTINCT asset_id)
  ⑥ 传了 --prefix：在 Python 侧按标签开头逐字符保留匹配的完整标签
  ⑦ Python 按标签文本的 Unicode 码点字典序升序排序
        ▼
print(json.dumps(result, ensure_ascii=False))   # 单行 JSON 数组
退出码 0；成功时标准错误为空
```

任何阶段抛出 `CliError` 都由 `main()`（`asset_catalog/cli.py:871`，捕获于
`cli.py:874-878`）统一收敛：向标准错误输出 `asset_catalog: 错误: <原因>`，
返回退出码 2，此前标准输出为空，且不输出调用栈。`EXIT_OK = 0`、
`EXIT_ERROR = 2` 定义在 `cli.py:16-17`。

## 2. 入口与参数解析

- `python -m asset_catalog` 执行 `asset_catalog/__main__.py`，其中
  `sys.exit(main())` 进入 `cli.main`（`asset_catalog/cli.py:871`）。
- `main()` 先调用 `build_parser()`（`cli.py:35`）解析参数；`tags` 子解析器
  定义在 `cli.py:111-126`，只接受两个可选选项：
  - `--type`（`cli.py:114-117`）：可选素材类型，去除首尾空白后与登记类型
    完整匹配（区分大小写）；
  - `--prefix`（`cli.py:118-125`）：可选标签前缀，去除首尾空白后与保存的
    标签文本逐字符比较，只匹配标签开头；
  - 子命令通过 `set_defaults(handler=handle_tags)`（`cli.py:126`）绑定到
    `handle_tags`。
- `tags` 不接受素材路径位置参数，也不接受 `--tag`、`--check-files` 等其他
  子命令的选项；这些在 argparse 阶段即被拒绝。
- argparse 层面的错误（缺 `--db`、`--type` 或 `--prefix` 缺值、传入不支持
  的选项或位置参数）由 `_Parser.error`（`cli.py:30-32`）处理：标准错误先
  输出用法行，再输出一行 `asset_catalog tags: 错误: <选项>: <原因>`，退出码
  2。此时 `handle_tags` 尚未执行，**不会创建数据库文件**。
- `--prefix` 只在 `tags` 子解析器上注册：`add`、`query`、`export`、`show`、
  `retag`、`retype` 收到 `--prefix` 时由各自的 argparse 以
  `unrecognized arguments` 拒绝（退出码 2、标准输出为空、无调用栈）。

## 3. 参数校验（打开数据库之前）

`handle_tags`（`cli.py:577`）在调用 `open_database`（`cli.py:599`）之前
完成两项手工校验：

### 3.1 `--type`（`cli.py:587-591`）

- **不传 `--type`**：`args.type is None`，`asset_type` 保持 `None`，表示
  统计全目录，不做任何校验。
- **传入 `--type`**：对取值调用 `str.strip()` 去除首尾空白；去空白后为空
  字符串（即传入 `""` 或仅含空白，如 `"   "`）时抛出
  `素材类型 --type 去除首尾空白后不能为空`，经 `main()` 收敛为退出码 2。

类型匹配规则在第 5 节随 SQL 一并说明：去首尾空白后的文本与登记类型**完整
匹配**、**区分大小写**，不做子串匹配，也不按扩展名推断；`Image` 不匹配
`image`，`" image "` 去空白后与 `image` 等价。

### 3.2 `--prefix`（`cli.py:593-597`）

- **不传 `--prefix`**：`args.prefix is None`，`tag_prefix` 保持 `None`，
  不做前缀筛选，输出全部标签（即原有行为）。
- **传入 `--prefix`**：对取值调用 `str.strip()` 去除**首尾**空白；去空白后
  为空字符串（传入 `""` 或仅含空白，如 `"   "`、`"\t "`）时抛出
  `标签前缀 --prefix 去除首尾空白后不能为空`，经 `main()` 收敛为退出码 2。
- 去空白只作用于取值两端：**中间空白逐字符保留**，中文等非 ASCII 字符也
  原样保留，比较规则见第 6 节。

两项校验都先于 `open_database`，因此空白类型或空白前缀等参数错误**不会创建
数据库文件**：即使 `--db` 指向一个尚不存在的路径，文件也不会出现（见
`tests/test_tags_regression.py:559-562`）。

## 4. 打开数据库

`open_database`（`cli.py:174-226`）打开 `--db` 指定的 SQLite 文件，规则对
全部子命令一致，`tags` 沿用，不做特殊处理：

1. `--db` 为空字符串时直接抛 `数据库路径不能为空`（`cli.py:179-180`）。
2. `sqlite3.connect(db_path)` 并清点 `sqlite_master` 中的用户表与用户视图
   （`cli.py:185-193`）。父目录缺失、路径指向目录、无权限、文件不是 SQLite
   数据库等情况在此处或首次 I/O 时抛出 `sqlite3.Error`，被包装为
   `无法打开数据库 <路径>: <原因>`（`cli.py:194-197`）。
3. 已存在 `asset`、`asset_tag` 两张表时，调用 `verify_schema`
   （`cli.py:249-257`）用 `PRAGMA table_info` 核对列集合：
   - `asset` 必须恰为 `{id, path, type}`（`ASSET_COLUMNS`，`cli.py:19`）；
   - `asset_tag` 必须恰为 `{asset_id, tag, position}`（`TAG_COLUMNS`，
     `cli.py:20`）；
   - 列不一致时抛 `数据库表结构与本产品不兼容，拒绝覆盖或重建`
     （`cli.py:256-257`）。
4. 存在其他用户表或用户视图却不具备两张兼容表时，抛
   `数据库 <路径> 结构不属于本产品，拒绝覆盖或重建`（`cli.py:207-212`）。
5. 既无用户表也无用户视图（文件尚不存在、零字节文件或完全空白的 SQLite
   库）时，由 `create_schema`（`cli.py:229-246`）初始化为空目录库并提交：

   - `asset(id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE,
     type TEXT NOT NULL)`；`id` 的自增顺序即首次登记顺序。
   - `asset_tag(asset_id INTEGER NOT NULL REFERENCES asset(id), tag TEXT
     NOT NULL, position INTEGER NOT NULL, PRIMARY KEY (asset_id, tag))`，
     外加标签索引 `idx_asset_tag_tag`。

因此**父目录存在而数据库文件不存在**时，一次合法的 `tags`（即使带
`--type` 或 `--prefix`）会留下一个空库并输出 `[]`；**父目录缺失**时第 2 步
即报错，目录不会被补建。结构被拒绝时走 `except CliError` 分支
（`cli.py:220-224`），只关闭连接、不做任何写入，原文件不被覆盖或重建。
已有数据库无需迁移，本次改动不涉及表结构。

## 5. 统计范围与计数：两条路径

统计逻辑全部在 `handle_tags` 的 `try` 块中（`cli.py:600-628`），读取结束
后在 `finally` 中关闭连接（`cli.py:631-632`）。`tags` 只查询数据库中**当前
保存的记录**（注释见 `cli.py:578-586`）：

- **不扫描目录**：不遍历任何磁盘目录寻找素材；
- **不检查文件状态**：不调用 `os.stat`，因此源文件已删除或登记路径已变成
  目录的素材**仍参与统计**；
- **不读取素材内容**：只读取 `asset`、`asset_tag` 两张表的元数据；
- **不写入**：正常统计不改动数据库与素材文件（唯一例外是第 4 节第 5 条的
  新库初始化）。

### 5.1 路径一：不传 `--type`（全目录统计）

`asset_type is None` 时执行（`cli.py:607-614`）：

```sql
SELECT tag, COUNT(DISTINCT asset_id) AS asset_count
FROM asset_tag
GROUP BY tag
```

对标签表中的**每个不同标签文本**各产生一行，数量为使用该标签的不同素材数。

### 5.2 路径二：传入 `--type`（按类型筛选后统计）

`asset_type` 非空时执行（`cli.py:615-628`），类型值以参数绑定传入：

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
  计算；`asset_tag` 的主键是 `(asset_id, tag)`（`cli.py:241`），每个素材的
  每个标签在表中至多一行——`add` 登记时重复传入同一标签会由
  `normalize_tags`（`cli.py:260-275`）按首次出现顺序去重。`DISTINCT` 与
  主键约束共同保证该语义显式且不依赖偶然数据。
- **不同登记路径即使内容相同仍分别计数。** 不同路径在 `asset` 表中是不同
  的行、不同的 `id`（`path` 有 UNIQUE 约束，`cli.py:234`），统计数的是
  素材（`asset_id`）而非文件内容；内容字节一致不产生合并。
- **标签区分大小写、不改写文本。** `GROUP BY tag` 按标签完整文本分组，
  SQLite 的 TEXT 比较不做大小写折叠，因此 `UI` 与 `ui` 是两个标签，各自
  独立计数、各自输出一行。
- **不输出零计数标签。** 标签不是来自一张独立的标签字典，而只来自
  `asset_tag` 中实际存在的行；`GROUP BY` 只产生至少有一条标签行的分组，
  所以没有任何素材使用的标签无处产生，输出中不会出现 `asset_count` 为 0
  的项；每项的 `asset_count` 必为正整数。
- 读取过程若发生 SQLite 错误（例如查询中途遇到损坏页），包装为
  `数据库读取失败: <原因>`（`cli.py:629-630`），退出码 2，不输出部分数组。

## 6. 前缀筛选：`--prefix`

前缀筛选**不在 SQL 中使用 LIKE**，而是在分组计数完成之后、排序之前，于
Python 侧对每个分组标签做开头比较（`cli.py:634-637`）：

```python
if tag_prefix is not None:
    rows = [row for row in rows if row[0].startswith(tag_prefix)]
```

这一顺序带来三项直接保证：

1. **计数不被合并或重算。** 进入筛选的每一行已经是一个完整标签及其
   `COUNT(DISTINCT asset_id)`；筛选只决定整行保留与否，`ui` 与
   `ui.button` 永远各自独立计数，不存在按前缀归并。
2. **与 `--type` 同用时范围先收窄。** SQL 先只统计指定类型素材的标签
   （5.2 路径），前缀再作用于这批计数；其他类型素材既不影响计数，也不会
   让某个标签仅因前缀相同而入选项（见第 8.2 节）。
3. **通配字符天然按字面处理。** 取值从不进入 SQL 模式匹配，`%`、`_` 与
   反斜线都是普通字符，无需也不会做转义；例如 `--prefix 'ui%'` 只匹配以
   `ui%` 三个字符开头的标签，不会匹配 `ui_x`。

比较规则（`str.startswith`，注释见 `cli.py:635-636`）：

- 前缀值已在第 3.2 节去除**首尾**空白；比较时不再做任何裁剪。
- **逐字符（按 Unicode 码点）比较、区分大小写**，不做大小写折叠：`UI` 不
  以 `ui` 开头。
- **只匹配标签开头**，不匹配中间子串：标签 `ui.button` 能被前缀 `ui` 与
  `ui.` 选中，但前缀 `button` 选不中它。
- **保留中间空白**：`ui 中` 以 `ui ` 开头而不以 `ui中` 开头。
- **保留中文等非 ASCII 文本**：`中文 标签` 以 `中文` 开头，以 `标签`
  开头则不成立。
- 前缀可以比标签长：此时自然不匹配；没有任何标签满足时保留为空列表，
  随后输出 `[]`，命令仍然成功，不输出零计数项。

## 7. 排序与 JSON 输出

SQL 本身不带 `ORDER BY`；排序在 Python 侧完成（`cli.py:641-644`）：

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
  整数），不输出素材 id、路径、类型等其他字段。前缀筛选只去掉整行，不
  改写入选标签文本，也不增减键。
- 空目录、无匹配类型或无匹配前缀时，列表为空。

随后 `handle_tags` 在 `cli.py:645` 执行
`print(json.dumps(result, ensure_ascii=False))` 并返回 0（`cli.py:646`）：

- 标准输出**恰好一行** JSON 数组（`print` 追加唯一换行符），成功时标准
  错误为空，退出码 0；中文等非 ASCII 标签按原文输出（`ensure_ascii=False`）。

## 8. 固定演示

样例数据与第 5 节的最小演示不同：本节固定为三条登记记录，便于直接对照
前缀计数。两个类型、标签如下（文件内容任意，统计不读取内容）：

| 素材 | 类型 | 已保存标签（登记顺序） |
|---|---|---|
| `A` | `image` | `ui`, `ui.button`, `UI` |
| `B` | `image` | `ui.button` |
| `C` | `audio` | `ui.button`, `audio` |

### 8.1 前缀一：`tags --prefix ui`（不传 `--type`，走 5.1 路径 + 第 6 节筛选）

先对全目录分组计数，再只保留以 `ui` 开头的完整标签：

| 标签 | 命中的素材 | `asset_count` |
|---|---|---|
| `ui` | A | 1 |
| `ui.button` | A、B、C（不同登记路径分别计数） | 3 |

`UI` 因区分大小写不入选；`audio` 不以 `ui` 开头，不入选。退出码 0、标准
错误为空，标准输出仅一行数组：

```json
[{"tag": "ui", "asset_count": 1}, {"tag": "ui.button", "asset_count": 3}]
```

### 8.2 前缀二：`tags --prefix ui --type image`（走 5.2 路径 + 第 6 节筛选）

先把范围限定为 image 素材（A、B；C 为 audio，整行不参与），再按前缀
保留：

| 标签 | 命中的 image 素材 | `asset_count` |
|---|---|---|
| `ui` | A | 1 |
| `ui.button` | A、B（C 为 audio，不计入） | 2 |

退出码 0、标准错误为空，标准输出仅一行数组：

```json
[{"tag": "ui", "asset_count": 1}, {"tag": "ui.button", "asset_count": 2}]
```

`--type` 与 `--prefix` 的先后次序不影响结果。补充核对（同一规则的延伸，
均退出码 0、标准错误为空、输出一行数组）：

- `tags --prefix UI`：区分大小写，只返回 `UI` 计 1；
- `tags --prefix button`：只匹配开头，`button` 是 `ui.button` 的中间子串，
  输出 `[]`；
- `tags --prefix 'ui%'`：`%` 为普通字符，不做通配，输出 `[]`（除非确有
  以 `ui%` 三个字符开头的标签）；
- `tags --prefix ui --type video`：先按类型筛选无素材，输出 `[]`；
- 不传 `--prefix` 时恢复第 7 节原有全量统计（本例为 `UI`、`audio`、`ui`、
  `ui.button` 四项）；
- 对一个父目录存在、尚不存在的数据库文件执行 `tags --prefix ui`：沿用
  第 4 节建空库规则，文件被创建为空目录库，输出 `[]`。

## 9. 失败情形：参数失败与数据库失败

两类失败**都**表现为退出码 2、标准输出为空、标准错误给出原因且不含调用栈
（`main()` 的统一收敛，`cli.py:876-878`；唯一的 `print` 在 `cli.py:645`，
任何失败都先于它发生），区别在于失败阶段与数据库是否被触碰。

### 9.1 参数失败：发生在打开数据库之前，不创建数据库

| 情形 | 拒绝位置 | 标准错误要点 |
|---|---|---|
| `--type` / `--prefix` 缺值（命令以该选项结尾） | argparse，`_Parser.error`（`cli.py:30-32`） | 用法行 + 指明 `argument --prefix: expected one argument` 等 |
| `--type ""`、`--type "   "`（为空或仅有空白） | `handle_tags`（`cli.py:588-591`） | `素材类型 --type 去除首尾空白后不能为空`，指出选项与原因 |
| `--prefix ""`、`--prefix "   "`（为空或仅有空白） | `handle_tags`（`cli.py:594-597`） | `标签前缀 --prefix 去除首尾空白后不能为空`，指出选项与原因 |
| 其他子命令（`add`/`query`/`export`/`show`/`retag`/`retype`）收到 `--prefix` | argparse | 用法行 + `unrecognized arguments: --prefix ...` |
| 传入素材路径位置参数、`--tag`、`--check-files` 等不支持参数 | argparse | 用法行 + 指出不被识别的参数 |
| 缺少全局必填的 `--db` | argparse | 用法行 + 指出 `--db` 必填 |

参数失败时 `handle_tags` 未执行或在校验处即返回，`open_database` 不会被
调用：**数据库文件不被创建**，已存在的数据库字节不变。

### 9.2 数据库失败：退出码 2，原文件不被覆盖或重建

| 情形 | 检测位置 | 标准错误中的原因（前缀均为 `asset_catalog: 错误:`） |
|---|---|---|
| 父目录缺失 | `open_database`（`cli.py:194-197`） | `无法打开数据库 <路径>: unable to open database file`；缺失目录不补建 |
| `--db` 指向目录 | 同上 | `无法打开数据库 <路径>: unable to open database file` |
| 文件存在但不是 SQLite 数据库 | 同上 | `无法打开数据库 <路径>: file is not a database` |
| SQLite 镜像损坏（如截断一半） | 打开/清点或首次读取时 | `无法打开数据库 <路径>: database disk image is malformed`（读取中途损坏则为 `数据库读取失败: ...`，`cli.py:629-630`） |
| 含其他用户表或用户视图 | `open_database`（`cli.py:207-212`） | `数据库 <路径> 结构不属于本产品，拒绝覆盖或重建` |
| `asset` / `asset_tag` 列名不兼容 | `verify_schema`（`cli.py:249-257`） | `数据库表结构与本产品不兼容，拒绝覆盖或重建` |

这些分支的共同点：标准输出为空，错误消息只说明原因、**不含 Traceback**；
被拒绝的文件保持原样——结构拒绝分支只关闭连接、不执行任何写语句
（`cli.py:220-224`），非 SQLite 或损坏文件也不会被初始化或覆盖。与之相对，
父目录存在而数据库不存在是**成功**路径：建空库、提交、输出 `[]`。

## 10. 与现有回归测试的核对

下列用例全部位于 `tests/test_tags_regression.py`，通过
`python -m asset_catalog` 公开入口以子进程观察行为，是本文每条结论的核对
依据。执行方式：

```sh
python -m unittest tests.test_tags_regression -v
```

- 公共断言：`run_cli`（`:56-71`）、`assertTagsOk`（退出码 0、标准错误为空、
  标准输出恰一行、JSON 为数组且逐字相等，`:101-110`）、`assertTagsError`
  （退出码 2、标准输出为空、标准错误非空且无 `Traceback`，`:112-118`）。
- `test_tags_counts_case_sensitive_and_sorted`（`:120-141`）：对应第 5.3、
  7 节——`UI` 计 1、`demo` 计 2、`ui` 计 1，每项仅含 `tag` 与
  `asset_count`，标签不重复、按码点升序、数量为正整数。
- `test_same_tag_same_asset_counted_once`（`:143-156`）：同一素材重复登记
  同一标签后该标签只计 1。
- `test_same_content_different_paths_counted_separately`（`:158-167`）：
  内容相同的两个路径分别计数，`demo` 计 2。
- `test_retag_changes_reflected_in_next_run`（`:169-188`）：统计只反映已
  保存记录，retag 后的新标签在下次及后续新进程中一致生效。
- `test_deleted_or_replaced_source_still_counted`（`:190-205`）：源文件
  删除或原路径变成目录后仍参与统计，结果不变。
- `test_tags_does_not_change_database_or_files`（`:207-222`）：统计前后
  数据库文件与素材文件按字节比较完全一致。
- `test_empty_database_outputs_empty_array`（`:224-231`）：父目录存在而
  数据库不存在时建空库并输出 `[]`，新进程复读一致。
- `test_tags_type_filters_by_exact_type`（`:252-275`）：`--type image`
  只统计 image 素材（`UI` 1、`demo` 2、`ui` 1，audio 的 `sound` 不参与），
  不带 `--type` 时恢复全目录统计。
- `test_tags_type_strips_surrounding_whitespace`（`:277-289`）：
  `--type " image "` 与 `image` 等价。
- `test_tags_type_case_sensitive_and_unmatched_outputs_empty`（`:291-296`）：
  `Image` 不匹配、无匹配类型均成功输出 `[]`。
- `test_tags_type_empty_database_outputs_empty_array`（`:298-303`）：空库
  搭配 `--type` 仍沿用建空库规则并输出 `[]`。
- `test_tags_type_reflects_retype_and_retag`（`:305-337`）：retype 与
  retag 改动保存记录后，按类型统计随之变化。
- `test_tags_type_missing_or_blank_value_rejected`（`:339-348`）与
  `test_tags_type_blank_value_does_not_create_database`（`:350-353`）：
  `--type` 缺值、为空、仅有空白时退出码 2、标准输出为空，且不创建数据库、
  不改动已有数据库。
- `test_tags_prefix_acceptance_counts`（`:374-385`）：对应第 8.1 节——
  A（image: `ui`、`ui.button`、`UI`）、B（image: `ui.button`）、
  C（audio: `ui.button`、`audio`）下 `tags --prefix ui` 得 `ui` 计 1、
  `ui.button` 计 3，`UI` 不入选。
- `test_tags_prefix_with_type_filters_scope_first`（`:387-415`）：对应
  第 8.2 节——加 `--type image` 后计数为 1、2；选项次序可互换；无匹配
  类型输出 `[]`。
- `test_tags_prefix_is_case_sensitive_and_start_only`（`:417-426`）：
  `--prefix UI` 只选 `UI`；中间子串 `button` 不匹配；比标签更长的前缀
  输出 `[]`。
- `test_tags_prefix_treats_special_characters_as_literals`（`:428-467`）：
  `%`、`_`、反斜线与句点按普通字符逐字符比较，不做通配或转义，不扩大匹配
  范围；前缀 `a` 下 `a%b`、`a.b`、`a_b` 按码点序各计 1。
- `test_tags_prefix_strips_edges_keeps_inner_whitespace_and_chinese`
  （`:469-502`）：首尾空白被去除，中间空白与中文逐字符保留；`" ui "`
  选中 `ui 中`，`ui中` 不选；中文前缀只匹配开头，`标签` 选不中
  `中文 标签`。
- `test_tags_prefix_deleted_or_directory_source_still_counted`
  （`:504-518`）：前缀统计同样不检查文件状态，源文件删除或变成目录后
  结果不变。
- `test_tags_prefix_does_not_change_database`（`:520-533`）：带 `--prefix`
  的统计前后数据库字节完全一致。
- `test_tags_prefix_no_match_and_empty_database_output_empty_array`
  （`:535-543`）：无匹配前缀成功输出 `[]`；父目录存在而数据库不存在时
  建空库并输出 `[]`。
- `test_tags_prefix_missing_or_blank_value_rejected`（`:545-557`）与
  `test_tags_prefix_blank_value_does_not_create_database`（`:559-562`）：
  对应第 9.1 节——`--prefix` 缺值、为空、仅有空白时退出码 2、标准输出
  为空，错误信息指出 `--prefix` 且无调用栈，不创建数据库、不改动已有
  数据库。
- `test_prefix_rejected_for_other_subcommands`（`:564-581`）：`--prefix`
  只属于 `tags`，其余六个子命令收到它均退出码 2、标准输出为空、无调用栈。
- `test_missing_parent_directory_is_not_created`（`:583-586`）：父目录
  缺失时报错且不补建目录。
- `test_unsupported_tags_arguments_rejected`（`:588-595`）与
  `test_unsupported_arguments_do_not_create_database`（`:597-600`）：位置
  参数、`--tag`、`--check-files` 被拒绝，且不创建数据库。
- `test_database_path_is_directory_rejected`（`:602-605`）、
  `test_empty_database_path_rejected`（`:607-612`）、
  `test_missing_db_option_rejected`（`:614-624`）：数据库路径为目录、
  `--db` 为空、缺少 `--db` 均退出码 2、标准输出为空、无调用栈。
- `test_corrupt_and_incompatible_databases_rejected_and_untouched`
  （`:626-666`）：对应第 9.2 节——非 SQLite 文件、含其他业务表的库、缺列
  的不兼容结构均被拒绝，原文件字节保持不变，原有记录保留。
- `test_malformed_database_produces_no_partial_output`（`:668-677`）：
  镜像截断损坏时整次失败，不输出部分数组。
