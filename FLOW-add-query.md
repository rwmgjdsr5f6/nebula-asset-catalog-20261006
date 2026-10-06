# 流程说明：素材登记（add）与按标签查询（query）

本文是与当前源码核对的纯文本流程说明，只描述现有基线已实现的步骤，
不新增功能、不改动源码、目录数据、README 及源素材。
每条结论标注依据（源码文件与符号/行号，或 README 行号）；
无法从代码确定的内容统一标注「未定义」，未实现的步骤标注「未实现」。

核对基线：仓库根目录提交 `61c8dec`，涉及文件：

- `asset_catalog/cli.py`（全部命令行逻辑，唯一实现文件）
- `asset_catalog/__main__.py`（`python -m asset_catalog` 入口）
- `asset_catalog/__init__.py`（仅声明 `__all__ = ["cli"]`，无逻辑）
- `README.md`（公开用法与规则描述）
- `TESTS.md`、`tests/`（回归测试说明与用例，仅作旁证）

文中示例命令已于 2026-10-06 在 `/tmp` 独立沙箱中按 README 公开入口实际执行，
输出与本文「预期结果」一致；沙箱已删除，未触碰仓库内任何文件。

## 1. 公开入口与进程模型

唯一公开入口是命令行：

```sh
python -m asset_catalog --db DB_FILE <add|query> ...
```

- 入口链路：`asset_catalog/__main__.py:3-6` 调用 `cli.main()`；
  `cli.py:247-254` 的 `main()` 解析参数并分派到子命令处理器。
- 子命令仅有两个：`add`（`cli.py:42-51`，处理器 `handle_add`）
  和 `query`（`cli.py:53-55`，处理器 `handle_query`）。
  除此之外没有列表、删除、更新、预览、指纹等命令——这些均属「未实现」。
- 全局必填选项 `--db` 指定 SQLite 数据库文件（`cli.py:34-39`）；
  文件不存在时自动创建并建表（`open_database` → `create_schema`，
  `cli.py:60-114`），但不创建缺失的父目录（`cli.py:63` 注释及实测，
  见第 4 节）。
- 每次命令都是独立进程；记录持久化在 SQLite 文件中，
  进程结束后由新进程重新打开同一 `--db` 文件即可读到（见第 3 节步骤 4）。
- 退出码约定（`cli.py:13-14`、`cli.py:247-254`）：成功 `0`；
  所有可预期错误（`CliError` 及参数解析错误）为 `2`，
  此时标准输出为空、标准错误为单行原因、不输出调用栈。

## 2. 演示准备

两个内容任意的普通小文件（本文以 `text` 作为素材类型示例；
`--type` 是自由文本，产品不识别也不校验具体类型取值，
依据：`handle_add` 仅做去空白与非空检查，`cli.py:143-145`）：

```sh
printf 'hello a\n' > demo-a.txt
printf 'hello b\n' > demo-b.txt
```

标签约定：两个文件共用标签 `demo`；`selected` 仅属于 `demo-a.txt`。

## 3. 主流程（逐步）

### 步骤 1：登记 demo-a.txt

```sh
python -m asset_catalog --db catalog.sqlite add demo-a.txt --type text --tag demo --tag selected
```

- 输入含义：`add` 的位置参数 `path` 为待登记文件路径；
  `--type` 必填，素材类型；`--tag` 可重复且至少一个
  （`cli.py:42-50`）。
- 处理：路径经 `os.path.realpath` 解析为规范绝对路径
  （`cli.py:147-148`）；类型与标签去除首尾空白，
  标签按首次出现顺序去重、大小写保留
  （`cli.py:143`、`normalize_tags`，`cli.py:128-139`）。
- 预期结果（退出码 0，标准输出为单行 JSON 对象，标准错误为空）：

  ```json
  {"path": "/abs/dir/demo-a.txt", "type": "text", "tags": ["demo", "selected"]}
  ```

  （`/abs/dir` 为实际所在目录的规范绝对路径。输出构造见 `cli.py:183-188`。）

### 步骤 2：登记 demo-b.txt

```sh
python -m asset_catalog --db catalog.sqlite add demo-b.txt --type text --tag demo
```

- 预期结果（退出码 0）：

  ```json
  {"path": "/abs/dir/demo-b.txt", "type": "text", "tags": ["demo"]}
  ```

### 步骤 3：记录的存储位置

- 记录写入 `--db` 指定的 SQLite 文件。表结构（`create_schema`，`cli.py:97-114`）：
  - `asset(id, path, type)`：`path` 为规范绝对路径，`UNIQUE` 约束；
  - `asset_tag(asset_id, tag, position)`：主键 `(asset_id, tag)`，
    `position` 记录登记时的标签顺序；另有索引 `idx_asset_tag_tag`。
- 写入在单次提交中完成（`cli.py:164-173`）；进程退出后记录保留在数据库文件中。
- 源文件本身只读，不被修改或复制（README 第 5 行；
  代码中除 `os.path.exists`/`os.path.isfile` 检查外不访问源文件，`cli.py:149-152`）。

### 步骤 4：结束进程后，按共同标签查询

重新启动进程（查询本身就是新进程）：

```sh
python -m asset_catalog --db catalog.sqlite query --tag demo
```

- 输入含义：`query` 的 `--tag` 必填，做精确（完整）匹配，
  区分大小写、不做子串匹配（`cli.py:53-54`；
  SQL 为 `WHERE t.tag = ?`，`cli.py:199-208`）。
- 重新读取方式：`open_database` 重新打开同一 SQLite 文件并校验表结构
  （`verify_schema`，`cli.py:117-125`），随后联表查询。
- 预期结果（退出码 0，标准输出为 JSON 数组，按首次登记顺序排列，
  每条记录带该素材的完整标签集，`cli.py:210-243`）：

  ```json
  [{"path": "/abs/dir/demo-a.txt", "type": "text", "tags": ["demo", "selected"]}, {"path": "/abs/dir/demo-b.txt", "type": "text", "tags": ["demo"]}]
  ```

  即共同标签 `demo` 对应 demo-a.txt 与 demo-b.txt 两条记录。

### 步骤 5：按单独标签查询

```sh
python -m asset_catalog --db catalog.sqlite query --tag selected
```

- 预期结果（退出码 0）：

  ```json
  [{"path": "/abs/dir/demo-a.txt", "type": "text", "tags": ["demo", "selected"]}]
  ```

  即单独标签 `selected` 只对应 demo-a.txt 一条记录；
  注意返回的 `tags` 仍是该素材的完整标签集，不只是匹配到的标签
  （`cli.py:222-236` 及注释）。

## 4. 异常与边界情况核对（同一流程中）

以下均为代码可确定的现有行为；退出码 2 时标准输出为空、
标准错误为单行说明（`cli.py:24-29`、`cli.py:252-254`，已实测流分离）。

| 情况 | 现有处理 | 依据 |
| --- | --- | --- |
| 登记路径不存在 | `CliError`，退出码 2，stderr：`asset_catalog: 错误: 登记路径不存在: <原样路径>` | `cli.py:149-150` |
| 登记路径存在但不是普通文件（如目录） | `CliError`，退出码 2，stderr：`... 登记路径不是普通文件: <原样路径>` | `cli.py:151-152` |
| `--type` 去除首尾空白后为空 | `CliError`，退出码 2，stderr：`... 素材类型 --type 去除首尾空白后不能为空` | `cli.py:143-145` |
| 某个 `--tag` 去除首尾空白后为空 | `CliError`，退出码 2，stderr：`... 标签 --tag 去除首尾空白后不能为空` | `cli.py:128-135` |
| 缺少 `--type` 或 `--tag`（add）/ `--tag`（query）/ `--db` | argparse 参数错误：退出码 2，stderr 为 usage 加单行说明，stdout 为空 | `cli.py:24-29`、`cli.py:34-54` |
| 再次登记同一规范路径（含 `./`、`..`、符号链接等等价写法） | `CliError`，退出码 2，stderr：`... 素材已登记，拒绝重复登记: <规范路径>`；原有记录不被覆盖、合并或更新（写入前显式检查 + `path UNIQUE` 兜底，冲突时回滚） | `cli.py:158-162`、`cli.py:174-176`；旁证 `tests/test_add_dedup_regression.py` |
| 查询未匹配的标签 | 不是错误：退出码 0，stdout 为 `[]`，stderr 为空 | `cli.py:242-243`（空结果集直接序列化） |
| 查询标签去除首尾空白后为空 | `CliError`，退出码 2，stderr：`... 查询标签 --tag 去除首尾空白后不能为空` | `cli.py:193-195` |
| `--db` 文件不存在 | 自动创建文件并建表；`query` 返回 `[]`（退出码 0） | `cli.py:68-83` |
| `--db` 父目录不存在 / 路径为目录 / 无权限 | `CliError`，退出码 2，stderr：`... 无法打开数据库 <路径>: <原因>` | `cli.py:68-75` |
| 数据库已存在但损坏或表结构不属于本产品 | `CliError`，退出码 2，拒绝覆盖或重建 | `cli.py:84-92`、`cli.py:117-125` |

## 5. 规范化处理（仅与本次样例相关）

- 路径：`os.path.realpath` 解析相对路径、`.`、`..` 与符号链接，
  得到规范绝对路径后存储与查重（`cli.py:147-148`）。
  本说明不扩展为全平台规则（如 Windows 盘符、大小写不敏感文件系统等未核对）。
- 类型与标签：保存前去除首尾空白；标签按首次出现顺序去重；
  大小写保留（`Demo` 与 `demo` 是两个不同标签）
  （`cli.py:128-145`；旁证 `TESTS.md` 第 94-98 行）。
- 查询标签同样先去除首尾空白再精确匹配（`cli.py:193`），
  因此 `query --tag " demo "` 与登记标签 `demo` 能匹配。

## 6. README 与源码一致性核对

就本流程而言，README 与源码未发现不一致：

- README 第 13-21 行的 add/query 示例及输出形态与 `cli.py:183-188`、
  `cli.py:242-243` 一致。
- README 第 26 行「规范绝对路径、重复登记拒绝」对应 `cli.py:147-162`。
- README 第 27 行「去空白、去重、保留大小写」对应 `cli.py:128-145`。
- README 第 28 行「按首次登记顺序、源文件删除不影响记录」对应
  `cli.py:205`（`ORDER BY a.id ASC`）及「记录只存于数据库」的存储模型
  （旁证 `TESTS.md` 第 101 行）。
- README 第 29 行「损坏或异构数据库报错」对应 `cli.py:84-92`。
- README 第 30 行错误约定对应 `cli.py:13-29`、`cli.py:252-254`。

## 7. 未实现与未定义清单

未实现（解析器中不存在对应子命令或公开入口，`cli.py:40-55`）：

- 列出全部素材、删除记录、更新类型或标签、按类型查询、模糊/子串查询；
- 预览、指纹/内容哈希及任何长期模块；
- 任何 `add`/`query` 之外的进程间或网络接口。

未定义（代码与 README 均未给出可核对的依据）：

- 非 `CliError` 的未预期异常（如键盘中断、SQLite 内部错误以外的运行时异常）
  的退出码与输出形态——`main()` 只捕获 `CliError`（`cli.py:250-254`），
  其余异常如何呈现缺少依据；
- 多进程并发写同一数据库时的行为（无锁或重试逻辑，代码未涉及）；
- 路径规范化的全平台规则（仅核对了本次样例所在的 Linux 环境）；
- 数据库文件的具体内部编码/兼容版本（依赖 Python 标准库 `sqlite3`，
  未在代码或文档中声明）。
