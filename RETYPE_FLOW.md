# retype 类型修改流程说明

本文对应 `asset_catalog/cli.py` 中 retype（修改一条已登记素材的类型）这条
既有流程的实际函数，说明修改如何保存、失败时旧记录如何保留，便于逐条核对。
行号对应当前版本，函数名是稳定锚点。本次只补充说明：命令参数、JSON 输出、
SQLite 结构与源文件只读规则均未改变。

## 1. 全流程总览

```
python -m asset_catalog --db DB retype PATH --type NEW_TYPE
        │  asset_catalog/__main__.py → cli.py: main()（cli.py:715）
        ▼
argparse 解析（build_parser，cli.py:34；retype 子命令定义在 cli.py:128-135）：
path 为位置参数、--type 必填；缺参或出现不支持的参数时退出码 2、
标准输出为空、不创建数据库
        ▼
handle_retype（cli.py:659）按顺序编排下列步骤：
  ① 路径校验与规范化（cli.py:662-664）：空路径报错；
     os.path.realpath 解析为规范绝对路径（与 add 同一规则）
  ② 类型规范化（cli.py:668-670）：去除首尾空白、保留大小写，
     接受任意非空文本；去空白后为空报错。①② 均在打开数据库之前，
     参数错误不建库
  ③ open_database（cli.py:140）：打开/创建并校验结构
  ④ 定位记录（cli.py:674-679）：按规范路径查 asset 表；未登记报错
     （错误含规范路径）
  ⑤ 读取标签（cli.py:682-689）：按 position 升序读出完整标签，用于输出
  ⑥ 更新提交（cli.py:693-697）：UPDATE asset SET type 与 commit
     在同一事务中完成；失败时整体 rollback（cli.py:698-700）
        ▼
标准输出仅一行 JSON：{"path": ..., "type": ..., "tags": [...]}，退出码 0
（cli.py:706-712）
```

任何阶段抛出 `CliError` 都由 `main()`（cli.py:715-722）统一收敛：标准错误
单行 `asset_catalog: 错误: <原因>`（不含调用栈），退出码 2，标准输出为空。

## 2. 路径与类型处理规则

- **路径**（cli.py:662-664）：`os.path.realpath` 解析相对路径、`.`/`..` 与
  符号链接，得到规范绝对路径；等价写法定位同一条登记记录。retype 不检查
  源文件当前状态：源文件已删除或已变成目录，仍可按原登记路径修改类型。
- **类型**（cli.py:668-670）：`args.type.strip()` 去除首尾空白，保留大小写，
  接受任意非空文本（含空格、斜杠、非 ASCII 字符），不按扩展名猜测类型。
  提交与当前类型相同的规范化值同样成功，输出不变。
- **源文件只读**：整个流程只读写数据库，不读取素材内容，也不创建、移动或
  改写源文件。

## 3. 成功时的可核对结果

- 退出码 0，标准错误为空。
- 标准输出仅一行 JSON 对象，只含 `path`、`type`、`tags` 三个键：
  `path` 为规范绝对路径，`type` 为规范化后的新类型，`tags` 为按登记顺序
  （position 升序）读出的完整标签（cli.py:682-689）。
- 只改类型：路径、完整标签及其顺序、首次登记顺序（asset.id 升序）与其他
  素材的记录保持不变；修改已提交，新进程的 query / export 读取结果与本次
  输出一致。

## 4. 失败边界与记录状态

所有失败统一为：退出码 2、标准输出为空、标准错误单行说明原因且不含调用栈。

| 失败情形 | 发生阶段 | 结果 |
|----------|----------|------|
| 路径缺失、`--type` 缺值、传入不支持的参数 | argparse 解析（build_parser，cli.py:34） | 退出码 2，不创建数据库 |
| 路径为空字符串 | 路径校验 ①（cli.py:662-663） | 退出码 2，不创建数据库 |
| `--type` 去空白后为空 | 类型规范化 ②（cli.py:668-670） | 退出码 2，不创建数据库 |
| 目标未登记 | 定位记录 ④（cli.py:677-678） | 错误含规范路径；不新增素材、不改变已有记录 |
| 数据库无法打开（路径为目录、父目录缺失、无权限等） | open_database（cli.py:140-163） | 按既有口径拒绝，不补建缺失的父目录 |
| 数据库损坏或无法读写 | open_database（cli.py:183-185） | 拒绝且不覆盖原库 |
| 结构不兼容（非本产品结构） | open_database / verify_schema（cli.py:170-190、215-223） | 拒绝且不覆盖、不重建原库 |
| 更新或提交被数据库拒绝（如触发器、约束） | 更新提交 ⑥（cli.py:693-700） | 事务整体回滚，旧记录完整保留，不留部分修改 |

数据库创建时机（沿用 `open_database` 的既有规则）：

- 参数错误发生在 `open_database` 之前，**不创建数据库**。
- 参数合法、数据库文件尚不存在且父目录存在时，先创建空库并初始化结构，
  再在阶段 ④ 报告目标未登记（错误含规范路径）。
- 父目录缺失时不补建目录，直接在打开阶段报错。

写入失败的原子性：`UPDATE` 与 `conn.commit()` 构成同一事务；任一步抛出
`sqlite3.Error` 时 `handle_retype` 整体 `rollback`（cli.py:698-700），
标准错误为 `数据库读写失败: <数据库给出的原因>`，旧类型、标签及其顺序、
其他素材的记录全部保留，不留下部分修改。

## 5. 固定样例（成功路径）

### 工作目录与路径约定

- 命令在任意工作目录下执行均可，前提是 `asset_catalog` 包可导入（例如在
  项目根目录执行，或把项目根加入 `PYTHONPATH`）。
- 样例使用独立临时目录 `$SAMPLE`（下文以 `$SAMPLE` 代指），其中只有
  `A.bin`、`B.bin` 两个演示文件与数据库 `catalog.sqlite`；输出中的 `path`
  为 `$SAMPLE` 下路径的规范绝对路径（`os.path.realpath` 结果）。
- JSON 核对按解析后的内容判断，不依赖对象键序或空白。

### 准备：按 A、B 顺序登记

```bash
python -m asset_catalog --db "$SAMPLE/catalog.sqlite" add "$SAMPLE/A.bin" --type image --tag demo --tag ui
python -m asset_catalog --db "$SAMPLE/catalog.sqlite" add "$SAMPLE/B.bin" --type audio --tag demo
```

### 修改 A 的类型

```bash
python -m asset_catalog --db "$SAMPLE/catalog.sqlite" retype "$SAMPLE/A.bin" --type texture
```

预期：退出码 0，标准错误为空，标准输出仅一行（键序不限）：

```json
{"path": "<$SAMPLE/A.bin 的规范绝对路径>", "type": "texture", "tags": ["demo", "ui"]}
```

### 随后导出完整目录

```bash
python -m asset_catalog --db "$SAMPLE/catalog.sqlite" export
```

预期：退出码 0，标准输出为按 A、B 登记顺序排列的数组，只有 A 的类型变化，
两条素材的标签保持原样：

```json
[
  {"path": "<$SAMPLE/A.bin 的规范绝对路径>", "type": "texture", "tags": ["demo", "ui"]},
  {"path": "<$SAMPLE/B.bin 的规范绝对路径>", "type": "audio", "tags": ["demo"]}
]
```

**验证状态**：以上成功样例已在本仓库当前实现上实际执行（Python 3、Linux、
临时目录），退出码、标准错误为空、retype 与 export 的 JSON 内容均与上述
预期一致。对应回归测试：`tests/test_retype_regression.py` 中的
`test_retype_changes_type_and_query_filters_by_new_type`。

## 6. 固定样例（写入被拒绝，对照回归测试）

本样例对照 `tests/test_retype_write_failure_regression.py` 的
`test_retype_blocked_keeps_old_record_then_succeeds_when_condition_removed`，
拒绝条件与该测试完全一致。

### 场景设置

- 目录与路径约定同第 5 节；按 A、B 顺序登记同样的两条素材
  （A：image，标签 demo、ui；B：audio，标签 demo）。
- 在样例库上附加一个固定拒绝条件（保留原有兼容表结构，仅加触发器）：

```sql
CREATE TRIGGER reject_retype_to_texture
BEFORE UPDATE ON asset
WHEN NEW.type = 'texture'
BEGIN
    SELECT RAISE(ABORT, 'retype blocked');
END;
```

### 输入与预期结果

```bash
python -m asset_catalog --db "$SAMPLE/catalog.sqlite" retype "$SAMPLE/A.bin" --type texture
```

预期：退出码 2，标准输出为空，标准错误为单行、不含调用栈，且同时包含
`数据库读写失败` 与数据库给出的拒绝文本 `retype blocked`：

```
asset_catalog: 错误: 数据库读写失败: retype blocked
```

失败后由新进程 `export` 核对：仍按 A、B 顺序返回原记录——A 的类型仍为
`image`、标签仍为 `["demo", "ui"]`，B 的记录不变；旧记录完整保留，不留
部分修改。撤去拒绝条件（`DROP TRIGGER reject_retype_to_texture`）后用同一
输入再次提交，则按第 5 节的成功结果完成修改。

**验证状态**：以上失败样例已在本仓库当前实现上实际执行（Python 3、Linux、
临时目录），退出码 2、标准输出为空、标准错误单行内容、失败后 export 的
原记录保留结果均与上述预期一致。对应测试位置：
`tests/test_retype_write_failure_regression.py`（触发器 SQL 见该文件
`CREATE_REJECT_TRIGGER_SQL`）。

## 7. 回归对照

- `tests/test_retype_regression.py`：成功修改、路径等价写法、相同类型重复
  提交、源文件删除或变为目录、参数错误、未登记、数据库打开/结构问题、
  空库创建与父目录缺失等全部边界；
- `tests/test_retype_write_failure_regression.py`：类型写入被数据库拒绝时
  事务整体回滚、旧记录完整保留，以及撤去拒绝条件后同一输入成功。
