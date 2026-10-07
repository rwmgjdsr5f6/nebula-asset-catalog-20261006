# query 查询流程说明

本文围绕 **一条 `query` 查询**，说明一次 `python -m asset_catalog ... query ...`
如何决定素材**入选**、**排除**与**整次失败**：从进程入口讲到标准输出，并为关键
结论标注实际源码路径与函数名（行号对应当前版本，函数名是稳定锚点）。

本文只描述现有行为，不改变任何命令、数据库结构与 [README.md](README.md) 的公开
语义；除文档与固定示例外不交付代码改动。`add`、`retag`、`retype`、`export` 的
行为不在此展开，查询也不影响它们。

## 1. 全流程总览

```
python -m asset_catalog
        │  asset_catalog/__main__.py  →  asset_catalog/cli.py: main()
        ▼
argparse 解析参数（_Parser.error：非法选项值在此直接退出码 2）
        ▼
handle_query（asset_catalog/cli.py:422）
  ① 规范化标签条件与类型：去首尾空白、去重；空白即拒绝（此时尚未打开数据库）
  ② open_database：打开库；不存在但父目录存在时初始化为空目录库
  ③ 一条 SQL 得出“元数据候选”：标签命中(all/any) → --type → --exclude-tag
     结果按 asset.id 升序（首次登记顺序）
  ④ build_records：补全每条候选的完整标签及原顺序，记录仅含 path/type/tags
  ⑤ apply_file_status_rules：仅当启用状态选项时才访问文件系统，
     追加或按 file_status 筛选
        ▼
print(json.dumps(result, ensure_ascii=False))   # 单行 JSON 数组
退出码 0；成功时标准错误为空
```

任何阶段抛出 `CliError` 都由 `main()`（`asset_catalog/cli.py:663`，捕获于
`cli.py:668-670`）统一收敛：向标准错误输出 `asset_catalog: 错误: <原因>`，
返回退出码 2，此前标准输出为空，且不输出调用栈。`EXIT_OK = 0`、
`EXIT_ERROR = 2` 定义在 `cli.py:15-16`。

## 2. 入口与参数解析

- `python -m asset_catalog` 执行 `asset_catalog/__main__.py`，其中
  `sys.exit(main())` 进入 `cli.main`（`asset_catalog/cli.py:663`）。
- `main()` 先调用 `build_parser()`（`cli.py:34`）解析参数；`query` 子解析器
  定义在 `cli.py:55-98`，接受：
  - `--tag-mode`：仅小写 `all`（默认）/ `any`（`cli.py:56-64`）；
  - `--tag`：可重复、必填（`cli.py:65-70`）；
  - `--exclude-tag`：可重复、可选（`cli.py:71-79`）；
  - `--type`：可选（`cli.py:80-83`）；
  - `--check-files`：开关，为入选记录追加 `file_status`（`cli.py:84-88`）；
  - `--file-status`：仅小写 `present`/`missing`/`not_file`
    （`cli.py:89-97`）。
- argparse 层面的非法值（如 `--file-status Present`、`--tag-mode ALL`）由
  `_Parser.error`（`cli.py:29-31`）处理：标准错误包含用法行、对应选项名与
  原因，退出码 2。此时 `handle_query` 尚未执行，**不会创建数据库文件**。

## 3. 条件规范化（打开数据库之前）

`handle_query`（`cli.py:422`）在调用 `open_database` 之前完成全部条件校验：

1. **标签条件去空白、去重**：`normalize_tags(args.tag)`
   （`cli.py:226-241`）。每个标签分别 `strip()` 去除首尾空白，按首次出现
   顺序去重，大小写原样保留。任一标签去空白后为空，抛出
   `标签 --tag 去除首尾空白后不能为空`，退出码 2。
2. **排除标签同规则**：`normalize_tags(args.exclude_tag, option="--exclude-tag")`
   （`cli.py:432-436`）。错误信息指向 `--exclude-tag`；不传该选项时为空列表。
3. **类型去空白**：`--type` 存在时 `strip()`（`cli.py:438-442`），去空白后
   为空则报 `素材类型 --type 去除首尾空白后不能为空`，退出码 2。

以上校验全部先于 `open_database`（`cli.py:444`），因此空白标签、空白类型等
参数错误**不会创建数据库文件**，即使其他条件有效也不返回部分结果。

匹配规则（命中与排除共用）：按**完整文本**、**区分大小写**比较，不做子串
匹配；`ui` 与 `UI` 是两个标签。重复条件等同于只传一次，条件顺序不影响结果。

## 4. 打开数据库

`open_database`（`cli.py:140-192`）打开 `--db` 指定的 SQLite 文件：

- 文件**尚不存在但父目录存在**（含零字节文件、空白库）时，由
  `create_schema`（`cli.py:195-212`）初始化为空目录库并提交。因此一次**合法**
  查询指向新路径也会留下一个空库，随后正常输出 `[]`（见第 8 节）。
- 已存在 `asset`、`asset_tag` 两表时经 `verify_schema`（`cli.py:215-223`）
  校验列结构；含其他用户表或用户视图、损坏、父目录缺失、路径为目录等情况
  抛出 `CliError`，退出码 2，不覆盖、不重建。

数据库结构即 README 公开的两张表（`cli.py:197-210`），查询只读不写：

- `asset(id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT UNIQUE, type TEXT)`；
  `id` 的自增顺序就是**首次登记顺序**。
- `asset_tag(asset_id, tag, position, PRIMARY KEY(asset_id, tag))`；
  `position` 保存标签登记时的次序。

## 5. 元数据候选：入选与排除（一条 SQL）

`handle_query` 在 `cli.py:446-486` 用**一条** SQL 确定候选，组合语义固定：

1. **标签命中**：`FROM asset_tag t JOIN asset a` 且
   `t.tag IN (?, ...)`，按素材 `GROUP BY a.id`。
   - `--tag-mode all`（默认）：追加 `HAVING COUNT(DISTINCT t.tag) = ?`
     （参数为去重后的条件数，`cli.py:464-468`）——素材必须同时具备全部
     不同标签才入选。
   - `--tag-mode any`：无 HAVING（`cli.py:471-472`）——具备任一条件标签
     即入选；命中多个条件的素材分组后仍只出现一次。
2. **类型筛选**：传了 `--type` 时追加 `AND a.type = ?`（`cli.py:446`、
   `cli.py:461-462`），与去空白后的登记类型完整、区分大小写匹配。`any`
   与 `--type` 同用时，素材既要命中任一标签，也要类型相符。
3. **标签排除**：传了 `--exclude-tag` 时追加 `NOT EXISTS` 子查询
   （`cli.py:451-459`、参数绑定于 `cli.py:463`）：在上述命中范围与类型
   范围**之内**，再剔除具备任一排除标签的素材。
   - 不存在的排除标签不影响结果；
   - **同一标签同时用于命中与排除时仍执行排除**（不存在“冲突”报错）；
   - 排除只作用于本次查询，不修改任何记录。
4. **排序与去重**：`ORDER BY a.id ASC`（`cli.py:483`）。候选按首次登记
   顺序排列，每个素材至多出现一次。未命中标签、类型不符或被排除的素材
   不进入候选，其路径之后也**不会被检查文件状态**。

读取过程若发生 SQLite 错误，包装为 `数据库读取失败: ...`（`cli.py:489-490`），
退出码 2，不输出部分结果。

## 6. 组装输出记录：完整标签与原顺序

`build_records`（`cli.py:336-367`）把 SQL 行组装为记录：

- 每条记录只含 `path`（规范绝对路径）、`type`（登记类型）、`tags`；
- `tags` 是该素材的**完整**已保存标签（不是查询条件本身），按 `position`
  升序读取（`cli.py:351-358`），即保留登记/最近一次 retag 后的原顺序；
- 保持输入行顺序（即首次登记顺序），每个素材只出现一次；
- 候选为空时返回 `[]`，不访问标签表。

## 7. 文件状态：检查范围、判定与整次失败

文件状态规则集中在 `apply_file_status_rules`（`cli.py:370-419`），单条路径
的判定在 `check_file_status`（`cli.py:317-333`）。

### 7.1 何时访问文件系统

- `--check-files` 与 `--file-status` **都未启用**时直接返回候选
  （`cli.py:394-396`）：**完全不访问文件系统**，源文件已删除仍可按登记
  元数据入选。
- 任一选项启用后，**只检查元数据筛选剩下的候选**（`cli.py:400`）：未命中
  标签/类型、被 `--exclude-tag` 排除的素材不检查状态，其路径即使权限异常
  也不会导致查询失败。
- 状态是**本次运行临时计算**的，只出现在标准输出中，**不写回、不保存到
  目录**，也不改动素材文件；重复查询按当前文件系统重新判定。

### 7.2 状态判定（`check_file_status`）

对登记路径调用跟随符号链接的 `os.stat`（`cli.py:325`）：

| 当前路径情形 | 判定 | 代码位置 |
|---|---|---|
| 普通文件 | `present` | `cli.py:331-332`（`stat.S_ISREG`） |
| 路径不存在；中间组件不是目录；断开的符号链接 | `missing` | `cli.py:326-328`（捕获 `FileNotFoundError`/`NotADirectoryError`） |
| 目录等存在但非普通文件 | `not_file` | `cli.py:333` |
| 权限不足或其他 `OSError` | 抛 `CliError`：`无法确定文件状态: <路径>: <原因>` | `cli.py:329-330` |

符号链接按其**目标**判断（`os.stat` 跟随链接）；不读取文件内容。

### 7.3 两个选项的组合语义

与 `cli.py:374-382` 中的对照表一致：

| `--check-files` | `--file-status` | 行为 |
|---|---|---|
| 否 | 无 | 不检查文件；原样输出候选（仅 `path`/`type`/`tags`） |
| 是 | 无 | 保留全部候选，逐条**追加** `file_status`（不影响入选） |
| 否 | 指定状态 | 只保留状态相符的候选以**缩小结果**，不增加字段 |
| 是 | 指定状态 | 只保留状态相符的候选，并为入选记录追加 `file_status`，其值必与筛选值一致 |

筛选保持入选候选的相对顺序；`--check-files` 单独使用时 `missing`/`not_file`
的素材**仍在结果中**，只是带相应状态。

### 7.4 任一候选状态不可判定 → 整次失败

实现先读取**全部**候选的状态到本地列表，再决定取舍与输出
（`cli.py:398-411`）。因此任一候选因权限或其他系统错误无法判定状态，
`check_file_status` 抛出的 `CliError` 会直接传播到 `main()`：

- 退出码 **2**；
- **标准输出为空**（`print` 在 `cli.py:504`，位于状态处理之后，不会输出
  部分结果）；
- 标准错误为单行 `asset_catalog: 错误: 无法确定文件状态: <路径>: <原因>`，
  指出相关路径与原因，**不含调用栈**；
- 不保存状态、不改动记录与素材文件。

### 7.5 参数非法同样整次拒绝

- 空白 `--tag` / 空白 `--exclude-tag` / 去空白后为空的 `--type`：第 3 节
  的规范化阶段拒绝，标准错误指出对应选项，**不创建数据库**；
- 非法 `--file-status`（缺值、为空、含首尾空白、大小写不符、其他取值）与
  非法 `--tag-mode`：第 2 节的 argparse 阶段拒绝，标准错误指出对应选项，
  退出码 2，不创建数据库。

## 8. 标准输出

全部步骤成功后，`handle_query` 在 `cli.py:504` 执行
`print(json.dumps(result, ensure_ascii=False))` 并返回 0（`cli.py:505`）：

- 标准输出恰好是**一行 JSON 数组**，成功时标准错误为空，退出码 0；
- 元素为第 6 节的记录；仅当 `--check-files` 生效时入选记录才追加
  `file_status`；记录中无内部 id；
- 无匹配（无标签命中、类型无匹配、全部被排除或被状态筛掉）时输出 `[]`；
- 合法查询指向**尚不存在的数据库**且父目录存在时：库被初始化为空目录，
  查询输出 `[]`，退出码 0。

查询全程只读素材状态、只读数据库记录（新库初始化除外），不改写素材或已有
记录；`add`、`retag`、`retype`、`export` 保持各自原有行为。

## 9. 固定示例

样例目录统一为 `/srv/asset-catalog-example/`，下文中的路径均为素材登记后
保存的**规范绝对路径**，与当前工作目录无关。

### 9.1 样例数据与查询前状态

按 A、B、C 顺序登记，三者类型均为 `image`：

```sh
python -m asset_catalog --db /srv/asset-catalog-example/catalog.sqlite \
  add /srv/asset-catalog-example/A.bin --type image --tag demo --tag ui
python -m asset_catalog --db /srv/asset-catalog-example/catalog.sqlite \
  add /srv/asset-catalog-example/B.bin --type image --tag demo --tag archived
python -m asset_catalog --db /srv/asset-catalog-example/catalog.sqlite \
  add /srv/asset-catalog-example/C.bin --type image --tag ui
```

登记的元数据（`path` / `type` / `tags`）：

| 素材 | 规范绝对路径 | 类型 | 标签（原顺序） |
|---|---|---|---|
| A | `/srv/asset-catalog-example/A.bin` | `image` | `["demo","ui"]` |
| B | `/srv/asset-catalog-example/B.bin` | `image` | `["demo","archived"]` |
| C | `/srv/asset-catalog-example/C.bin` | `image` | `["ui"]` |

查询前删除 A，B、C 仍为普通文件：

```sh
rm /srv/asset-catalog-example/A.bin
```

### 9.2 查询一：any 命中 demo 或 ui → image → 排除 archived，并检查文件

```sh
python -m asset_catalog --db /srv/asset-catalog-example/catalog.sqlite query \
  --tag-mode any --tag demo --tag ui \
  --type image --exclude-tag archived --check-files
```

候选推演：

1. `any` 命中 `demo`/`ui`：A（demo、ui）、B（demo、archived）、C（ui）
   全部命中；
2. `--type image`：三者类型均为 image，仍为 A、B、C；
3. `--exclude-tag archived`：B 带 `archived` 被排除（B 文件本身存在与否不
   影响，排除只看元数据），候选为按登记顺序的 A、C；
4. `--check-files` 只追加状态、不缩小范围：A 路径已删除为 `missing`，
   C 为普通文件即 `present`。

退出码 0，标准错误为空，标准输出（单行 JSON）：

```json
[{"path": "/srv/asset-catalog-example/A.bin", "type": "image", "tags": ["demo", "ui"], "file_status": "missing"}, {"path": "/srv/asset-catalog-example/C.bin", "type": "image", "tags": ["ui"], "file_status": "present"}]
```

### 9.3 查询二：同样条件，改用 `--file-status missing` 且不带 `--check-files`

```sh
python -m asset_catalog --db /srv/asset-catalog-example/catalog.sqlite query \
  --tag-mode any --tag demo --tag ui \
  --type image --exclude-tag archived --file-status missing
```

元数据候选同样是 A、C；`--file-status missing` 只保留当前状态为 `missing`
的候选：A 入选，C 为 `present` 被筛掉，B 在元数据阶段已被排除、不参与状态
检查。不带 `--check-files`，记录仅含 `path`、`type`、`tags`。

退出码 0，标准错误为空，标准输出（单行 JSON）：

```json
[{"path": "/srv/asset-catalog-example/A.bin", "type": "image", "tags": ["demo", "ui"]}]
```

两条查询都不保存状态、不改写记录或素材；再次执行 `export` 仍按登记顺序返回
A、B、C 三条完整记录。
