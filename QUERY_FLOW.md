# query 查询流程：入选、排除与整次失败

本文说明一条 `query` 命令如何决定素材**入选**、**排除**与**整次失败**：从
`python -m asset_catalog` 的入口开始，沿实际源码讲到标准输出上的 JSON 数组。
文中关键结论均标注源码位置（文件与函数名），命令、数据库结构与 README 中
的公开语义保持不变；本文只做说明，并给出一组可复现的固定示例。

## 1. 入口与分发

调用链（均在本仓库内）：

1. `python -m asset_catalog` 由 Python 以模块方式执行
   `asset_catalog/__main__.py`，其中仅 `from .cli import main` 并
   `sys.exit(main())`（`asset_catalog/__main__.py:1`）。
2. `main()`（`asset_catalog/cli.py:663`）先调用 `build_parser()`
   （`asset_catalog/cli.py:34`）解析参数，再按子命令分发；`query` 子命令
   在 `build_parser()` 中注册，处理器固定为 `handle_query`
   （`asset_catalog/cli.py:55` 起、`:98` 绑定）。
3. `handle_query(args)`（`asset_catalog/cli.py:422`）是查询主流程；
   `main()` 捕获它抛出的 `CliError`，在标准错误打印单行
   `asset_catalog: 错误: …` 并返回退出码 `2`，不输出调用栈
   （`asset_catalog/cli.py:666-670`，常量 `EXIT_OK = 0`、
   `EXIT_ERROR = 2` 见 `asset_catalog/cli.py:15-16`）。

`query` 接受的选项即 README 公开的那些：可重复且必填的 `--tag`、
`--tag-mode {all,any}`（默认 `all`）、可重复的 `--exclude-tag`、
可选 `--type`、开关 `--check-files` 与 `--file-status
{present,missing,not_file}`（`asset_catalog/cli.py:55-98`）。

## 2. 打开数据库之前：参数规范化与参数错误

`handle_query` 先规范化参数，**再**打开数据库（`asset_catalog/cli.py:426-444`），
因此参数错误不会创建任何数据库文件。

- **命中标签**：`normalize_tags(args.tag)`（定义于
  `asset_catalog/cli.py:226`）对每个 `--tag` 分别去除首尾空白，按首次出现
  顺序去重，大小写原样保留。任一标签去空白后为空即抛 `CliError`
  （“标签 --tag 去除首尾空白后不能为空”）。`--tag` 必填，缺少时由
  argparse 以退出码 2 拒绝。
- **排除标签**：未传 `--exclude-tag` 时为空列表；传入时同样经
  `normalize_tags(..., option="--exclude-tag")` 去除首尾空白、按首次出现
  顺序去重，空值报错信息指向 `--exclude-tag`
  （`asset_catalog/cli.py:432-436`）。
- **类型**：`--type` 缺省为不过滤；传入时 `args.type.strip()` 去首尾空白，
  去空白后为空即报错（“素材类型 --type 去除首尾空白后不能为空”，
  `asset_catalog/cli.py:438-442`）。大小写保留，匹配时区分大小写。
- **标签模式与状态取值**：`--tag-mode` 只接受小写 `all`/`any`，
  `--file-status` 只接受小写 `present`/`missing`/`not_file`；非法值（含
  大小写不符、带空白、其他取值）由 argparse 直接拒绝：退出码 2、标准输出
  为空、标准错误指出对应选项（`choices` 定义见
  `asset_catalog/cli.py:57-63` 与 `:89-97`，解析错误统一收敛在
  `_Parser.error`，`asset_catalog/cli.py:29-31`）。

匹配规则对标签和类型一致：**按完整文本、区分大小写**，不做子串匹配。

## 3. 打开（必要时初始化）数据库

规范化通过后调用 `open_database(args.db)`（定义于
`asset_catalog/cli.py:140`）：

- 已存在且含本产品 `asset`、`asset_tag` 两张表时，经
  `verify_schema()`（`asset_catalog/cli.py:215`）核对列结构后使用。
- 文件尚不存在、零字节或为既无用户表也无用户视图的空白 SQLite 库，且
  **父目录存在**时，由 `create_schema()`（`asset_catalog/cli.py:195`）
  初始化为空目录库。因此对一个尚不存在的数据库执行合法查询是成功的，
  只是结果为 `[]`（见第 8 节）。父目录缺失、路径为目录、文件损坏或属于
  其他产品结构时抛 `CliError`，不补建目录、不覆盖文件。
- 查询只读数据库；结构由两张表组成（`create_schema`，
  `asset_catalog/cli.py:195-212`）：
  - `asset(id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL)`；
  - `asset_tag(asset_id, tag, position, PRIMARY KEY(asset_id, tag))`，
    `position` 记录标签登记顺序，另有 `idx_asset_tag_tag` 索引。

## 4. 元数据候选：all / any、类型与标签排除

候选完全由一条 SQL 在数据库内确定（`asset_catalog/cli.py:446-486`）：
从 `asset_tag t` 连接 `asset a`，`WHERE t.tag IN (…命中标签…)`，再接可选
类型条件与可选排除条件，`GROUP BY a.id`，最后 `ORDER BY a.id ASC`。

四种条件的组合语义：

1. **标签命中（all / any）**
   - `--tag-mode all`（默认）：追加 `HAVING COUNT(DISTINCT t.tag) = ?`，
     参数为去重后的条件标签数——素材必须同时具备全部不同标签才入选
     （`asset_catalog/cli.py:464-468`）。
   - `--tag-mode any`：不加 HAVING——行本身来自命中任一条件标签的素材，
     分组后每个素材只入选一次（`asset_catalog/cli.py:470-472`）。
   - 因为 `normalize_tags` 已去重，重复 `--tag` 等同于只传一次；条件顺序
     不影响结果；单个标签时两种模式等价。
2. **类型匹配**：传了 `--type` 时追加 `AND a.type = ?`
   （`asset_catalog/cli.py:446`）。与 any 同用时，素材既要命中任一标签，
   也要类型完整相等。
3. **标签排除**：传了 `--exclude-tag` 时追加 `AND NOT EXISTS (…
   et.asset_id = a.id AND et.tag IN (…排除标签…))`
   （`asset_catalog/cli.py:451-459`）。即先按标签命中方式与可选类型确定
   范围，再剔除**具备任一**排除标签的素材。排除按完整文本、区分大小写；
   不存在的排除标签不影响结果；**同一标签同时用于命中与排除时仍执行
   排除**（不存在“冲突”错误，见 `asset_catalog/cli.py:447-450` 注释）。
4. **顺序与基数**：`GROUP BY a.id` 保证每条素材至多一行，`ORDER BY
   a.id ASC` 使结果按**首次登记顺序**排列（id 为自增主键）。被排除或未
   命中标签、类型的素材根本不进入候选，因此后续也不会检查它们的路径。

## 5. 组装输出记录：完整标签与原顺序

SQL 取回 `(id, path, type)` 行后，由 `build_records(conn, rows)`
（`asset_catalog/cli.py:336`）组装记录：

- 对候选 id 批量查询 `asset_tag`，`ORDER BY asset_id ASC, position ASC`
  （`asset_catalog/cli.py:351-358`），因此每条记录的 `tags` 是该素材的
  **完整标签列表且保持登记顺序**——查询条件只决定入选，不裁剪返回标签
  （例如命中 `ui` 的素材仍返回其全部标签）。
- 每条素材在输出中**只出现一次**，相对顺序与输入行一致（即首次登记
  顺序）；候选为空时直接返回 `[]`，不访问标签表
  （`asset_catalog/cli.py:349`、`:367`）。
- 此时每条记录只有三个字段：`path`（登记时保存的规范绝对路径）、
  `type`、`tags`。

## 6. 文件状态：何时检查、如何归类

状态规则集中在 `apply_file_status_rules(records, check_files=…,
status_filter=…)`（`asset_catalog/cli.py:370`），单条路径的判定在
`check_file_status(path)`（`asset_catalog/cli.py:317`）。

**不启用任何状态选项**（无 `--check-files` 且无 `--file-status`）时，
函数原样返回候选，**完全不访问文件系统**
（`asset_catalog/cli.py:394-396`）：素材路径事后被删除或变成目录，记录
照常入选。

启用任一状态选项后，只检查**元数据筛选剩下的候选**（未命中、类型不符、
被排除的路径不检查），并且先读完全部候选的状态再决定取舍
（`asset_catalog/cli.py:400`）。状态不写入数据库、不保存到目录，每次查询
现场判定。单条路径的判定（`check_file_status`，
`asset_catalog/cli.py:324-333`）：

- `os.stat` 遇到 `FileNotFoundError` 或 `NotADirectoryError` →
  `missing`：路径不存在、中间组件不是目录（含断开的符号链接）都归此类。
- 能 stat 且 `stat.S_ISREG` 为真 → `present`：普通文件。
- 能 stat 但不是普通文件（目录等）→ `not_file`。
- 符号链接由 `os.stat` 按**目标**判断：指向普通文件为 `present`，指向
  目录等为 `not_file`，断链为 `missing`。
- 遇到权限不足或其他 `OSError` → 抛 `CliError`（“无法确定文件状态:
  <路径>: <系统原因>”）。

两个选项的四种组合（`asset_catalog/cli.py:374-381` 的对照表与
`:394-419` 的实现）：

| `--check-files` | `--file-status` | 行为 |
|---|---|---|
| 否 | 无 | 不检查文件系统；记录仅含 `path`、`type`、`tags` |
| 是 | 无 | 保留全部候选，逐条追加 `file_status`（取当前实际状态） |
| 否 | 指定状态 | 只保留状态相符的候选，**不**增加字段（仍仅三个字段） |
| 是 | 指定状态 | 只保留状态相符的候选，并追加 `file_status`，其值必与筛选值一致 |

状态筛选保持入选候选的相对顺序，不重排、不去重标签。

## 7. 整次失败：退出码 2 且无部分输出

- **任一候选**因权限或其他系统错误无法判定状态时，
  `check_file_status` 抛出的 `CliError` 会中止整次查询：
  `apply_file_status_rules` 在读完所有状态前不落任何输出
  （`asset_catalog/cli.py:398-400`），`handle_query` 不执行 `print`，
  `main` 统一在标准错误给出单行原因并返回退出码 2
  （`asset_catalog/cli.py:666-670`）。此时**标准输出为空、标准错误指出
  相关路径与原因、不含调用栈**，即使排在前面的候选已成功读取状态也不
  输出部分结果。
- 参数错误（空白 `--tag`、空白 `--type`、空白 `--exclude-tag`、非法
  `--tag-mode`/`--file-status`、缺少必填项等）同样退出码 2、标准输出为
  空、标准错误指出对应选项，且因为校验先于 `open_database`，**不会创建
  数据库**（见第 2 节）。
- 数据库无法打开、结构不兼容或查询时发生 SQLite 错误时，`handle_query`
  包装为 `CliError`（“数据库读取失败: …”，`asset_catalog/cli.py:489-490`），
  同样按退出码 2 失败。

成功路径只有一个出口：`print(json.dumps(result, ensure_ascii=False))`
（`asset_catalog/cli.py:504`），标准输出为单个 JSON 数组、退出码 0、
标准错误为空。无匹配（候选为空或状态筛选后为空）时输出 `[]`。

## 8. 固定示例

以下示例只依赖 Python 3 标准库。样例目录固定为
`/tmp/asset_catalog_query_demo`，数据库为该目录下的 `catalog.sqlite`；
下列路径为示例使用的规范绝对路径。

### 8.1 准备数据（add 仅用于准备，行为与 README 一致）

按 **A.bin、B.bin、C.bin** 的顺序登记；三者类型均为 `image`，标签依次为
`["demo","ui"]`、`["demo","archived"]`、`["ui"]`：

```sh
mkdir -p /tmp/asset_catalog_query_demo
cd /tmp/asset_catalog_query_demo
printf 'content A\n' > A.bin
printf 'content B\n' > B.bin
printf 'content C\n' > C.bin

python3 -m asset_catalog --db /tmp/asset_catalog_query_demo/catalog.sqlite \
  add /tmp/asset_catalog_query_demo/A.bin --type image --tag demo --tag ui
python3 -m asset_catalog --db /tmp/asset_catalog_query_demo/catalog.sqlite \
  add /tmp/asset_catalog_query_demo/B.bin --type image --tag demo --tag archived
python3 -m asset_catalog --db /tmp/asset_catalog_query_demo/catalog.sqlite \
  add /tmp/asset_catalog_query_demo/C.bin --type image --tag ui

# 查询前删除 A：B、C 仍为普通文件
rm /tmp/asset_catalog_query_demo/A.bin
```

三条登记回执（退出码 0、标准错误为空）依次为：

```json
{"path": "/tmp/asset_catalog_query_demo/A.bin", "type": "image", "tags": ["demo", "ui"]}
{"path": "/tmp/asset_catalog_query_demo/B.bin", "type": "image", "tags": ["demo", "archived"]}
{"path": "/tmp/asset_catalog_query_demo/C.bin", "type": "image", "tags": ["ui"]}
```

元数据候选推导：`--tag-mode any` 命中 `demo` 或 `ui`，三条素材全部命中；
`--type image` 三条均相符；`--exclude-tag archived` 剔除 B（B 带
`archived`），剩下候选 A、C，按首次登记顺序排列。

### 8.2 查询一：`any` + 类型 + 排除，带 `--check-files`

对候选 A、C 逐条检查状态：A 的路径已删除 → `missing`；C 仍是普通文件 →
`present`。

```sh
python3 -m asset_catalog --db /tmp/asset_catalog_query_demo/catalog.sqlite \
  query --tag-mode any --tag demo --tag ui \
  --type image --exclude-tag archived --check-files
```

退出码 `0`，标准错误为空，标准输出（一行；这里为便于阅读折行）：

```json
[
  {"path": "/tmp/asset_catalog_query_demo/A.bin", "type": "image", "tags": ["demo", "ui"], "file_status": "missing"},
  {"path": "/tmp/asset_catalog_query_demo/C.bin", "type": "image", "tags": ["ui"], "file_status": "present"}
]
```

实际标准输出为紧凑单行：

```json
[{"path": "/tmp/asset_catalog_query_demo/A.bin", "type": "image", "tags": ["demo", "ui"], "file_status": "missing"}, {"path": "/tmp/asset_catalog_query_demo/C.bin", "type": "image", "tags": ["ui"], "file_status": "present"}]
```

注意 A 虽已删除，仍凭登记元数据入选并报告 `missing`；被排除的 B 不检查
状态、不出现在输出中；A 的 `tags` 仍是完整的 `["demo","ui"]`。

### 8.3 查询二：同样条件，改用 `--file-status missing` 且不带 `--check-files`

候选仍为 A、C；只保留当前状态为 `missing` 的候选（A），且不附加状态
字段。

```sh
python3 -m asset_catalog --db /tmp/asset_catalog_query_demo/catalog.sqlite \
  query --tag-mode any --tag demo --tag ui \
  --type image --exclude-tag archived --file-status missing
```

退出码 `0`，标准错误为空，标准输出：

```json
[{"path": "/tmp/asset_catalog_query_demo/A.bin", "type": "image", "tags": ["demo", "ui"]}]
```

记录仅含 `path`、`type`、`tags` 三个字段，标签保持登记时的完整列表与
顺序。

## 9. 其他边界与不变性

- **无匹配输出 `[]`**：标签、类型或排除之后候选为空，或状态筛选后没有
  相符候选，成功退出（0）且输出 `[]`；候选为空时连标签表都不查询
  （`build_records`，`asset_catalog/cli.py:349`）。
- **对尚不存在的数据库做合法查询**：父目录存在时自动初始化为空目录库，
  查询成功并输出 `[]`（`open_database` → `create_schema`，
  `asset_catalog/cli.py:179-182`、`:195`）；父目录缺失则退出码 2，不补建
  目录。
- **查询不改写任何东西**：查询路径上没有对素材文件或数据库记录的写入；
  状态只在本次进程内判定，不保存到目录。素材文件不被读取内容、不被
  创建或移动。
- **其他子命令保持原行为**：`add`、`retag`、`retype`、`export` 不受本文
  影响。其中 `export` 不做任何标签、类型或文件状态筛选，始终按 id 升序
  导出全部记录，记录仅含 `path`、`type`、`tags`（见
  `handle_export`，`asset_catalog/cli.py:508`）。
