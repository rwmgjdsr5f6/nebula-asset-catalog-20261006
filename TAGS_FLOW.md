# tags 标签统计流程说明

本文围绕 **一次 `tags` 统计**，说明一次
`python -m asset_catalog --db catalog.sqlite tags` 如何从**已保存的登记记录**
生成每个标签的素材使用数量：从公开入口讲到参数校验、数据库打开、统计范围、
计数、可选前缀筛选、排序与 JSON 输出，并区分**不传 `--type`** 与**传入类型**
的两条查询路径。关键结论均标注实际源码路径与函数名（行号对应当前版本，函数名
是稳定锚点）。

本文描述的是加入可选 `--prefix` 之后的现有行为；除该选项及其文档、测试外，
不改变任何命令、数据库结构与 [README.md](README.md) 的公开语义。`add`、
`query`、`show`、`retag`、`retype`、`export` 的行为不在此展开。

## 1. 全流程总览

```
python -m asset_catalog --db catalog.sqlite tags [--type TYPE] [--prefix PREFIX]
        │  asset_catalog/__main__.py  →  asset_catalog/cli.py: main()
        ▼
argparse 解析参数（_Parser.error：非法选项、缺值在此直接退出码 2）
        ▼
handle_tags（asset_catalog/cli.py:577）
  ① 可选类型校验：传了 --type 就 strip()，为空即拒绝（此时尚未打开数据库）
  ② 可选前缀校验：传了 --prefix 就 strip()，为空即拒绝（同样先于打开数据库）
  ③ open_database：打开库；不存在但父目录存在时初始化为空目录库
  ④ 统计范围只取数据库中已保存的记录：不扫描目录、不检查文件、不读素材内容
  ⑤ 计数（两条路径之一）：
       不传 --type：asset_tag 全表按 tag 分组
       传入 --type：JOIN asset 后按登记类型完整匹配再分组
     每组 COUNT(DISTINCT asset_id)
  ⑥ 可选前缀筛选（Python 侧 startswith）：只保留以去空白前缀开头的完整标签
  ⑦ Python 按标签文本的 Unicode 码点字典序升序排序
        ▼
print(json.dumps(result, ensure_ascii=False))   # 单行 JSON 数组
退出码 0；成功时标准错误为空
```

任何阶段抛出 `CliError` 都由 `main()`（`asset_catalog/cli.py:872`，捕获于
`cli.py:875-879`）统一收敛：向标准错误输出 `asset_catalog: 错误: <原因>`，
返回退出码 2，此前标准输出为空，且不输出调用栈。`EXIT_OK = 0`、
`EXIT_ERROR = 2` 定义在 `cli.py:16-17`。

## 2. 入口与参数解析

- `python -m asset_catalog` 执行 `asset_catalog/__main__.py`，其中
  `sys.exit(main())` 进入 `cli.main`（`asset_catalog/cli.py:872`）。
- `main()` 先调用 `build_parser()`（`cli.py:35`）解析参数；`tags` 子解析器
  定义在 `cli.py:111-126`，接受两个可选选项：
  - `--type`（`cli.py:114-117`）：可选素材类型，去除首尾空白后与登记类型
    完整匹配（区分大小写）；
  - `--prefix`（`cli.py:118-125`）：可选标签前缀，去除首尾空白后与保存的
    标签文本逐字符比较（区分大小写），只列出处以该文本开头的已用标签；
  - 子命令通过 `set_defaults(handler=handle_tags)`（`cli.py:126`）绑定到
    `handle_tags`。
- `tags` 不接受素材路径位置参数，也不接受 `--tag`、`--check-files` 等其他
  子命令的选项；这些在 argparse 阶段即被拒绝。`--prefix` 只定义在 `tags`
  子解析器上，因此其他子命令（`add`、`query`、`export`、`show`、`retag`、
  `retype`）收到 `--prefix` 时同样在 argparse 阶段被拒绝。
- argparse 层面的错误（缺 `--db`、`--type`/`--prefix` 缺值、传入不支持的
  选项或位置参数）由 `_Parser.error`（`cli.py:30-32`）处理：标准错误先输出
  用法行，再输出一行 `asset_catalog tags: 错误: <选项>: <原因>`，退出码 2。
  此时 `handle_tags` 尚未执行，**不会创建数据库文件**。
- `--prefix` 帮助文本中的 `%` 在 argparse 帮助串里写作 `%%`（`cli.py:123`），
  因为 argparse 用 `%` 做帮助串格式化；`--help` 中显示为单个 `%`。这只影响
  帮助文本，与运行时取值无关。

## 3. 参数校验（打开数据库之前）

`handle_tags`（`cli.py:577`）在调用 `open_database`（`cli.py:599`）之前
完成两项手工校验：

### 3.1 `--type` 校验（`cli.py:584-588`）

- **不传 `--type`**：`args.type is None`，`asset_type` 保持 `None`，表示
  统计全目录，不做任何校验。
- **传入 `--type`**：对取值调用 `str.strip()` 去除首尾空白；去空白后为空
  字符串（即传入 `""` 或仅含空白，如 `"   "`）时抛出
  `素材类型 --type 去除首尾空白后不能为空`，经 `main()` 收敛为退出码 2。

类型匹配规则在第 5 节随 SQL 一并说明：去首尾空白后的文本与登记类型**完整
匹配**、**区分大小写**，不做子串匹配，也不按扩展名推断；`Image` 不匹配
`image`，`" image "` 去空白后与 `image` 等价。

### 3.2 `--prefix` 校验（`cli.py:590-597`）

- **不传 `--prefix`**：`args.prefix is None`，`tag_prefix` 保持 `None`，
  不做前缀筛选，`tags` 行为与加入该选项前完全一致。
- **传入 `--prefix`**：对取值调用 `str.strip()` **只去除首尾空白**；去空白
  后为空字符串（传入 `""`、`"   "`、制表符等仅含空白的值）时抛出
  `标签前缀 --prefix 去除首尾空白后不能为空`，经 `main()` 收敛为退出码 2。
  `--prefix` 缺值（命令以 `--prefix` 结尾）由 argparse 直接拒绝。
- 去空白时**保留中间空白与中文**：`"ui 组 "` 规范为 `"ui 组"`（中间一个
  空格保留），`" 中文 "` 规范为 `"中文"`。

两项校验都先于 `open_database`，因此空白类型/空白前缀等参数错误**不会创建
数据库文件**：即使 `--db` 指向一个尚不存在的路径，文件也不会出现（见
`tests/test_tags_regression.py` 中
`test_tags_prefix_blank_value_does_not_create_database`）。

前缀的比较规则在第 6 节随筛选代码一并说明：与保存的标签文本**逐字符比较**、
**区分大小写**、**只匹配标签开头**，`%`、`_` 与反斜线均为普通字符。

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
     外加标签索引 `idx_asset_tag_tag`（`cli.py:243`）。

因此**父目录存在而数据库文件不存在**时，一次合法的 `tags`（无论是否带
`--type`、`--prefix`）都会留下一个空库并输出 `[]`；**父目录缺失**时第 2 步
即报错，目录不会被补建。结构被拒绝时走 `except CliError` 分支
（`cli.py:220-224`），只关闭连接、不做任何写入，原文件不被覆盖或重建。
已有数据库无需迁移：`--prefix` 不引入任何表结构变化。

## 5. 统计范围与计数：两条路径

统计逻辑全部在 `handle_tags` 的 `try` 块中（`cli.py:600-624`），读取结束
后在 `finally` 中关闭连接（`cli.py:625-628`）。`tags` 只查询数据库中**当前
保存的记录**（注释见 `cli.py:578-583`）：

- **不扫描目录**：不遍历任何磁盘目录寻找素材；
- **不检查文件状态**：不调用 `os.stat`，因此源文件已删除或登记路径已变成
  目录的素材**仍参与统计**；
- **不读取素材内容**：只读取 `asset`、`asset_tag` 两张表的元数据；
- **不写入**：正常统计不改动数据库与素材文件（唯一例外是第 4 节第 5 条的
  新库初始化）。

`--prefix` 不影响这两条 SQL：前缀筛选在 SQL 取回**全部**已用标签统计之后、
于 Python 侧进行（第 6 节）。

### 5.1 路径一：不传 `--type`（全目录统计）

`asset_type is None` 时执行（`cli.py:604-611`）：

```sql
SELECT tag, COUNT(DISTINCT asset_id) AS asset_count
FROM asset_tag
GROUP BY tag
```

对标签表中的**每个不同标签文本**各产生一行，数量为使用该标签的不同素材数。

### 5.2 路径二：传入 `--type`（按类型筛选后统计）

`asset_type` 非空时执行（`cli.py:612-624`），类型值以参数绑定传入：

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
  计算；`asset_tag` 的主键是 `(asset_id, tag)`（`cli.py:240-242`），每个
  素材的每个标签在表中至多一行——`add` 登记时重复传入同一标签会由
  `normalize_tags`（`cli.py:260` 起）按首次出现顺序去重。`DISTINCT` 与
  主键约束共同保证该语义显式且不依赖偶然数据。
- **不同登记路径即使内容相同仍分别计数。** 不同路径在 `asset` 表中是不同
  的行、不同的 `id`（`path` 有 UNIQUE 约束），统计数的是素材（`asset_id`）
  而非文件内容；内容字节一致不产生合并。
- **标签区分大小写、不改写文本。** `GROUP BY tag` 按标签完整文本分组，
  SQLite 的 TEXT 比较不做大小写折叠，因此 `UI` 与 `ui` 是两个标签，各自
  独立计数、各自输出一行。
- **不输出零计数标签。** 标签不是来自一张独立的标签字典，而只来自
  `asset_tag` 中实际存在的行；`GROUP BY` 只产生至少有一条标签行的分组，
  所以没有任何素材使用的标签无处产生，输出中不会出现 `asset_count` 为 0
  的项；每项的 `asset_count` 必为正整数。前缀筛选只可能整项移除不匹配的
  标签，**绝不把计数改写成 0，也不合并共享前缀的不同标签**。
- 读取过程若发生 SQLite 错误（例如查询中途遇到损坏页），包装为
  `数据库读取失败: <原因>`（`cli.py:625-626`），退出码 2，不输出部分数组。

## 6. 可选前缀筛选（`--prefix`）

前缀筛选在 SQL 统计完成后、排序之前进行（`cli.py:630-638`）：

```python
if tag_prefix is not None:
    rows = [
        (tag, asset_count)
        for tag, asset_count in rows
        if tag.startswith(tag_prefix)
    ]
```

- **只在传入 `--prefix` 时执行。** `tag_prefix is None`（未传选项）时
  `rows` 原样保留，统计、排序与输出与加入该选项前完全一致。
- **逐字符、区分大小写、只匹配开头。** Python 的 `str.startswith` 按 Unicode
  码点逐字符比较，不做大小写折叠，也不做任何归一化；它判断标签是否**以**
  前缀**开头**，而非包含该子串。因此 `--prefix ui` 命中 `ui`、`ui.button`，
  不命中 `UI`（大小写不同），也不命中 `xui`（前缀只在中间）；`--prefix
  button` 不命中 `ui.button`（`button` 不在开头）。标签本身是其自身的前缀，
  故完整标签 `ui` 也会入选。
- **`%`、`_` 与反斜线是普通字符。** 筛选在 Python 侧用字符串方法完成，
  不经过 SQL `LIKE`，因此 `%`、`_` 不作为通配符，反斜线也不作为转义符：
  `--prefix a%` 只匹配以字面量 `a%` 开头的标签（如 `a%b`），不会扩散到
  `a_b`、`axb`；`--prefix a\` 只匹配以字面量反斜杠开头的标签。
- **首尾空白已在第 3.2 节去除，中间空白与中文保留并逐字符参与比较。**
  `"ui 组"` 与 `"ui 组"`（一个空格）匹配，与 `"ui  组"`（两个空格）不匹配；
  `--prefix 中文` 命中 `中文标签`，`--prefix 标签` 不命中（只在中间）。
- **`asset_count` 不随前缀改变，也不跨标签合并。** 计数在第 5 节已按完整
  标签算好，这里只决定某个完整标签项去留：`ui` 与 `ui.button` 始终是两项，
  各自保留自己的素材数。
- **与 `--type` 的组合顺序。** 先用第 5.2 节的 SQL 把统计范围限定到指定
  类型的素材，再在其结果上做前缀筛选。即“先在指定类型素材中统计，再保留
  满足前缀的标签”；类型匹配规则不因 `--prefix` 改变。
- **无匹配即空列表。** 没有任何已用标签以前缀开头时，筛选结果为 `[]`，
  随后正常排序、输出空数组，退出码仍为 0、标准错误为空。空目录（第 4 节
  建出的空库）同样如此。

## 7. 排序与 JSON 输出

SQL 本身不带 `ORDER BY`；前缀筛选之后在 Python 侧完成排序（`cli.py:640-645`）：

```python
result = [
    {"tag": tag, "asset_count": asset_count}
    for tag, asset_count in sorted(rows, key=lambda row: row[0])
]
```

- 每个标签只出现一次，按标签文本的 **Unicode 码点字典序升序**排列
  （Python 字符串默认按码点比较，不做大小写折叠）。例如大写字母
  `U`（U+0055）先于小写 `d`（U+0064），`d` 又先于小写 `u`（U+0075），
  故顺序为 `UI`、`demo`、`ui`；空格（U+0020）先于句点（U+002E），故
  `ui 组件` 排在 `ui.button` 之前。前缀筛选只缩小集合、不改变相对顺序。
- 每项是且仅含两个键的对象：`tag`（标签原文，不改写）与 `asset_count`
  （计数整数），不输出素材 id、路径、类型等其他字段。
- 空目录、无匹配类型或前缀筛选后无标签时，列表为空。

随后 `handle_tags` 在 `cli.py:646` 执行
`print(json.dumps(result, ensure_ascii=False))` 并返回 0（`cli.py:647`）：

- 标准输出**恰好一行** JSON 数组（`print` 追加唯一换行符），成功时标准
  错误为空，退出码 0；中文等非 ASCII 标签按原文输出（`ensure_ascii=False`）。

## 8. 固定演示

样例目录固定为 `/srv/asset-catalog-example/`，数据库为其中的
`catalog.sqlite`。三个素材文件内容任意（统计不读取内容），按下列顺序登记：

- `A.bin`，类型 `image`，标签依次为 `ui`、`ui.button`、`UI`；
- `B.bin`，类型 `image`，标签为 `ui.button`；
- `C.bin`，类型 `audio`，标签为 `ui.button`、`audio`。

```sh
python -m asset_catalog --db /srv/asset-catalog-example/catalog.sqlite \
  add /srv/asset-catalog-example/A.bin --type image \
  --tag ui --tag ui.button --tag UI
python -m asset_catalog --db /srv/asset-catalog-example/catalog.sqlite \
  add /srv/asset-catalog-example/B.bin --type image --tag ui.button
python -m asset_catalog --db /srv/asset-catalog-example/catalog.sqlite \
  add /srv/asset-catalog-example/C.bin --type audio \
  --tag ui.button --tag audio
```

登记后的保存记录（与文件内容无关）：

| 素材 | 类型 | 已保存标签（登记顺序） |
|---|---|---|
| `A.bin` | `image` | `ui`, `ui.button`, `UI` |
| `B.bin` | `image` | `ui.button` |
| `C.bin` | `audio` | `ui.button`, `audio` |

### 8.1 统计一：前缀 `ui`（全目录范围 + 前缀筛选）

```sh
python -m asset_catalog --db /srv/asset-catalog-example/catalog.sqlite \
  tags --prefix ui
```

先在全目录得到各完整标签计数，再只保留以 `ui` 开头者：

| 标签 | 命中的素材 | `asset_count` |
|---|---|---|
| `ui` | A | 1 |
| `ui.button` | A、B、C（不同登记路径分别计数） | 3 |

`UI` 因区分大小写不入选；`audio` 不以 `ui` 开头不入选；不输出零计数项。
退出码 0，标准错误为空，标准输出仅一行数组：

```json
[{"tag": "ui", "asset_count": 1}, {"tag": "ui.button", "asset_count": 3}]
```

### 8.2 统计二：`--prefix ui --type image`（先限定类型再筛前缀）

```sh
python -m asset_catalog --db /srv/asset-catalog-example/catalog.sqlite \
  tags --prefix ui --type image
```

先只统计 image 素材（A、B），C（audio）不参与，再保留以 `ui` 开头的标签：

| 标签 | 命中的 image 素材 | `asset_count` |
|---|---|---|
| `ui` | A | 1 |
| `ui.button` | A、B（C 为 audio，不计入） | 2 |

退出码 0，标准错误为空，标准输出仅一行数组：

```json
[{"tag": "ui", "asset_count": 1}, {"tag": "ui.button", "asset_count": 2}]
```

### 8.3 补充核对（均退出码 0、标准错误为空、输出一行数组）

- `tags --prefix UI`：区分大小写，只命中 `UI`，输出
  `[{"tag": "UI", "asset_count": 1}]`；
- `tags --prefix button`：只匹配开头，`button` 只是 `ui.button` 的中间
  子串，输出 `[]`；
- `tags --prefix " ui "`：首尾空白去除后等价于 `ui`，结果与统计一相同；
- `tags --prefix x`：无以此开头的标签，输出 `[]`；
- `tags`（不传 `--prefix`）：保留原有全目录行为，输出 `UI` 1、`audio` 1、
  `ui` 1、`ui.button` 3，按码点升序；
- 对一个父目录存在、尚不存在的数据库文件执行 `tags --prefix ui`：沿用
  第 4 节建空库规则，文件被创建为空目录库，输出 `[]`。

## 9. 失败情形：参数失败与数据库失败

两类失败**都**表现为退出码 2、标准输出为空、标准错误给出原因且不含调用栈
（`main()` 的统一收敛，`cli.py:877-879`；唯一的 `print` 在 `cli.py:646`，
任何失败都先于它发生），区别在于失败阶段与数据库是否被触碰。

### 9.1 参数失败：发生在打开数据库之前，不创建数据库

| 情形 | 拒绝位置 | 标准错误要点 |
|---|---|---|
| `--type` 缺值（命令以 `--type` 结尾） | argparse，`_Parser.error`（`cli.py:30-32`） | 用法行 + 指明 `argument --type: expected one argument` |
| `--type ""`、`--type "   "` | `handle_tags`（`cli.py:585-588`） | `素材类型 --type 去除首尾空白后不能为空` |
| `--prefix` 缺值（命令以 `--prefix` 结尾） | argparse，`_Parser.error` | 用法行 + 指明 `argument --prefix: expected one argument` |
| `--prefix ""`、`--prefix "   "`（为空或仅含空白） | `handle_tags`（`cli.py:593-597`） | `标签前缀 --prefix 去除首尾空白后不能为空`，指出选项与原因 |
| 其他子命令带 `--prefix`（如 `query --tag ui --prefix ui`） | argparse | 用法行 + 指出不被识别的参数 |
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
| SQLite 镜像损坏（如截断一半） | 打开/清点或首次读取时 | `无法打开数据库 <路径>: database disk image is malformed`（读取中途损坏则为 `数据库读取失败: ...`，`cli.py:625-626`） |
| 含其他用户表或用户视图 | `open_database`（`cli.py:207-212`） | `数据库 <路径> 结构不属于本产品，拒绝覆盖或重建` |
| `asset` / `asset_tag` 列名不兼容 | `verify_schema`（`cli.py:249-257`） | `数据库表结构与本产品不兼容，拒绝覆盖或重建` |

这些分支的共同点：标准输出为空，错误消息只说明原因、**不含 Traceback**；
被拒绝的文件保持原样——结构拒绝分支只关闭连接、不执行任何写语句
（`cli.py:220-224`），非 SQLite 或损坏文件也不会被初始化或覆盖。`--prefix`
不改变其中任何一条：参数合法（含任意非空前缀）时这些数据库错误照常发生，
且不返回部分结果。与之相对，父目录存在而数据库不存在是**成功**路径：建空库、
提交、输出 `[]`。

## 10. 与回归测试的核对

下列用例全部位于 `tests/test_tags_regression.py`，通过
`python -m asset_catalog` 公开入口以子进程观察行为。执行方式：

```sh
python -m unittest tests.test_tags_regression -v
```

- 公共断言：`run_cli`、`assertTagsOk`（退出码 0、标准错误为空、标准输出
  恰一行、JSON 为数组且逐字相等）、`assertTagsError`（退出码 2、标准输出
  为空、标准错误非空且无 `Traceback`）。

原有 `tags` / `tags --type` 用例（`--prefix` 加入后行为保持不变）：

- `test_tags_counts_case_sensitive_and_sorted`：`UI` 计 1、`demo` 计 2、
  `ui` 计 1，按码点升序、数量为正整数。
- `test_same_tag_same_asset_counted_once`、
  `test_same_content_different_paths_counted_separately`：同一素材同一标签
  只计一次；不同路径分别计数。
- `test_retag_changes_reflected_in_next_run`、
  `test_deleted_or_replaced_source_still_counted`、
  `test_tags_does_not_change_database_or_files`：retag 后下次统计生效；
  源文件删除或变成目录仍参与；统计不改动数据库与素材文件。
- `test_empty_database_outputs_empty_array`：父目录存在而数据库不存在时
  建空库并输出 `[]`。
- `test_tags_type_filters_by_exact_type`、
  `test_tags_type_strips_surrounding_whitespace`、
  `test_tags_type_case_sensitive_and_unmatched_outputs_empty`、
  `test_tags_type_empty_database_outputs_empty_array`、
  `test_tags_type_reflects_retype_and_retag`：`--type` 的完整匹配、去首尾
  空白、区分大小写、空库与随 retype/retag 变化等语义。
- `test_tags_type_missing_or_blank_value_rejected`、
  `test_tags_type_blank_value_does_not_create_database`：`--type` 缺值、
  为空、仅空白时退出码 2 且不创建数据库。
- `test_missing_parent_directory_is_not_created`、
  `test_unsupported_tags_arguments_rejected`、
  `test_unsupported_arguments_do_not_create_database`、
  `test_database_path_is_directory_rejected`、
  `test_empty_database_path_rejected`、`test_missing_db_option_rejected`、
  `test_corrupt_and_incompatible_databases_rejected_and_untouched`、
  `test_malformed_database_produces_no_partial_output`：父目录、非法参数、
  数据库路径、损坏/不兼容结构与读取失败的统一拒绝行为。

`--prefix` 专项用例：

- `test_tags_prefix_lists_only_tags_starting_with_prefix`：对应第 8.1
  节——`--prefix ui` 依次为 `ui` 1、`ui.button` 3，`UI` 不入选，不合并、
  不输出零计数。
- `test_tags_prefix_with_type_filters_scope_first`：对应第 8.2 节——叠加
  `--type image` 时为 `ui` 1、`ui.button` 2；`--type video` 输出 `[]`。
- `test_tags_prefix_is_case_sensitive_and_start_only`：`--prefix UI` 只命中
  `UI`；`--prefix button` 不命中中间子串；`--prefix audio` 命中自身。
- `test_tags_prefix_trims_surrounding_whitespace_only`：首尾空白去除、中间
  空白与中文保留并逐字符比较（含空格 U+0020 先于句点 U+002E 的排序核对）。
- `test_tags_prefix_percent_underscore_backslash_are_literal`：`%`、`_`、
  反斜线均为普通字符，通配写法不扩散到其他标签。
- `test_tags_prefix_no_match_outputs_empty_array`、
  `test_tags_prefix_empty_database_outputs_empty_array`：无匹配与空库均成功
  输出 `[]`，后者沿用建空库规则。
- `test_tags_prefix_deleted_or_replaced_source_still_counted`：带前缀时已
  删除或变成目录的登记路径仍参与计数。
- `test_tags_prefix_does_not_change_database_or_files`：带前缀统计后数据库
  字节不变。
- `test_tags_prefix_sorted_by_unicode_codepoint`：筛选后仍按码点升序，且
  `Ui`、`UI` 等不以小写 `ui` 开头者不入选。
- `test_tags_prefix_missing_or_blank_value_rejected`、
  `test_tags_prefix_blank_value_does_not_create_database`：缺值、为空、仅
  空白时退出码 2、标准输出为空、标准错误指出 `--prefix` 且无调用栈，不
  创建、不改动数据库。
- `test_prefix_rejected_by_other_subcommands`：`add`、`query`、`export`、
  `show`、`retag`、`retype` 收到 `--prefix` 均退出码 2、标准输出为空、无
  调用栈，且不改动数据库。
