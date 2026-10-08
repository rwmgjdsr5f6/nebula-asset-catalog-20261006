# relink 重新关联本地路径流程说明

本文对应 `asset_catalog/cli.py` 中 relink（把一条已登记素材重新关联到新的
本地路径）这条流程的实际函数，说明路径更新如何保存、失败时旧记录如何保留，
便于逐条核对。行号对应当前版本，函数名是稳定锚点。本次只新增 relink：
既有七个命令的参数、JSON 输出、SQLite 结构与源文件只读规则均未改变，
旧数据库无需迁移；本次不保存历史路径，也不提供批量关联。

## 1. 全流程总览

```
python -m asset_catalog --db DB relink OLD_PATH NEW_PATH
        │  asset_catalog/__main__.py → cli.py: main()（cli.py:955）
        ▼
argparse 解析（build_parser，cli.py:35；relink 子命令定义在 cli.py:171-179）：
old_path、new_path 均为位置参数；缺参或出现不支持的参数时退出码 2、
标准输出为空、不创建数据库
        ▼
handle_relink（cli.py:882）按顺序编排下列步骤：
  ① 路径校验与规范化（cli.py:888-893）：任一路径为空字符串报错；
     两侧都用 os.path.realpath 解析为规范绝对路径
     （原路径与 show 同一规则，新路径与 add 同一规则）
  ② 新路径文件校验（cli.py:895-898）：要求当前存在且为普通文件，
     符号链接按目标解析；不存在或不是普通文件报错（错误含规范路径）
  ③ 目录数据库冲突检查（cli.py:903-905）：新路径的规范路径与 --db 的
     规范路径相同即拒绝。①②③ 均在打开数据库之前，参数错误不建库，
     已有空文件不会因此被初始化
  ④ open_database（cli.py:184）：打开/创建并校验结构
  ⑤ 定位记录（cli.py:909-914）：按原规范路径查 asset 表；未登记报错
     （错误含规范路径）
  ⑥ 冲突检查与更新提交（cli.py:916-932）：新路径属于另一条登记记录时
     拒绝（不合并、不覆盖）；否则 UPDATE asset SET path 与 commit
     在同一事务中完成，失败时整体 rollback（cli.py:938-940）。
     新旧规范路径相同且目标仍为普通文件（②已校验）时不写入，
     直接按原记录成功返回
  ⑦ 读取标签（cli.py:937）：按 position 升序读出完整标签，用于输出
        ▼
标准输出仅一行 JSON：{"path": ..., "type": ..., "tags": [...]}，退出码 0
（cli.py:946-952）
```

任何阶段抛出 `CliError` 都由 `main()`（cli.py:955-962）统一收敛：标准错误
单行 `asset_catalog: 错误: <原因>`（不含调用栈），退出码 2，标准输出为空。

## 2. 两侧路径处理规则

- **原路径**（cli.py:892）：`os.path.realpath` 解析相对路径、`.`/`..` 与
  符号链接，得到规范绝对路径，与 show 同一规则定位记录。relink 不检查
  原路径当前的文件状态：原文件已删除或原位置变成目录，仍允许重新关联。
- **新路径**（cli.py:893-898）：同样解析为规范绝对路径，与 add 同一规则
  要求当前存在且为普通文件；符号链接按目标解析，记录保存目标的规范绝对
  路径。新路径不存在或不是普通文件（如目录）时拒绝，错误含规范路径。
- **文件只读**：整个流程只读写数据库，不移动、复制、删除或改写任何文件，
  不读取素材内容，也不扫描目录。
- **目录数据库冲突**（cli.py:903-905）：新路径与 `--db` 的规范绝对路径
  相同时拒绝（与 add 的“目录数据库不能作为素材登记到自身”同一规则），
  不合并、不覆盖；检查在打开数据库之前进行。

## 3. 成功时的可核对结果

- 退出码 0，标准错误为空。
- 标准输出仅一行 JSON 对象，只含 `path`、`type`、`tags` 三个键：
  `path` 为新路径的规范绝对路径，`type` 为原类型，`tags` 为按登记顺序
  （position 升序）读出的完整标签（cli.py:937）。
- 只改路径：类型、完整标签及其顺序、首次登记顺序（asset.id 升序）与其他
  素材的记录保持不变；更新已提交，重启或新进程后的 show / query / export
  读取结果与本次输出一致；原路径不再命中（show 原路径报告未登记）。
- 新旧规范路径相同且目标仍为普通文件时成功返回原记录，不写入数据库。

## 4. 失败边界与记录状态

所有失败统一为：退出码 2、标准输出为空、标准错误单行说明原因且不含调用栈。

| 失败情形 | 发生阶段 | 结果 |
|----------|----------|------|
| 路径缺失、传入不支持的参数、缺少 `--db` | argparse 解析（build_parser，cli.py:35） | 退出码 2，不创建数据库 |
| 任一路径为空字符串、数据库路径为空 | 路径校验 ①（cli.py:888-891）、open_database（cli.py:184） | 退出码 2，不创建数据库 |
| 新路径不存在或不是普通文件 | 新路径校验 ②（cli.py:895-898） | 错误含新路径规范路径；不创建数据库 |
| 新路径等于 `--db` 规范路径 | 冲突检查 ③（cli.py:903-905） | 错误含冲突规范路径；不创建或初始化数据库 |
| 原路径未登记 | 定位记录 ⑤（cli.py:912-913） | 错误含原路径规范路径；不改变已有记录 |
| 新路径属于另一条登记记录 | 冲突检查 ⑥（cli.py:918-923） | 错误含新路径规范路径；不合并、不覆盖 |
| 数据库无法打开（路径为目录、父目录缺失、无权限等） | open_database（cli.py:184-203） | 按既有口径拒绝，不补建缺失的父目录 |
| 数据库损坏或无法读写 | open_database（cli.py:227-229） | 拒绝且不覆盖原库 |
| 结构不兼容（非本产品结构） | open_database / verify_schema（cli.py:213-233、259-267） | 拒绝且不覆盖、不重建原库 |
| 更新或提交被数据库拒绝（如触发器、约束） | 更新提交 ⑥（cli.py:928-940） | 事务整体回滚，原记录完整保留，不留部分修改 |

数据库创建时机（沿用 `open_database` 的既有规则）：

- 参数错误与新路径校验发生在 `open_database` 之前，**不创建数据库**。
- 参数合法、数据库文件尚不存在且父目录存在时，先创建空库并初始化结构，
  再在阶段 ⑤ 报告原路径未登记（错误含规范路径）。
- 父目录缺失时不补建目录，直接在打开阶段报错。

写入失败的原子性：`UPDATE` 与 `conn.commit()` 构成同一事务；任一步抛出
`sqlite3.Error` 时 `handle_relink` 整体 `rollback`（cli.py:938-940），
标准错误为 `数据库读写失败: <数据库给出的原因>`，原路径、类型、标签及其
顺序、其他素材的记录全部保留，不留下部分修改。

## 5. 固定样例（成功路径）

### 工作目录与路径约定

- 命令在任意工作目录下执行均可，前提是 `asset_catalog` 包可导入（例如在
  项目根目录执行，或把项目根加入 `PYTHONPATH`）。
- 样例使用独立临时目录 `$SAMPLE`（下文以 `$SAMPLE` 代指），其中只有
  `A.old`、`B.bin` 两个演示文件与数据库 `catalog.sqlite`；输出中的 `path`
  为 `$SAMPLE` 下路径的规范绝对路径（`os.path.realpath` 结果）。
- JSON 核对按解析后的内容判断，不依赖对象键序或空白。

### 准备：按 A.old、B.bin 顺序登记

```bash
python -m asset_catalog --db "$SAMPLE/catalog.sqlite" add "$SAMPLE/A.old" --type image --tag demo --tag ui
python -m asset_catalog --db "$SAMPLE/catalog.sqlite" add "$SAMPLE/B.bin" --type audio --tag demo
```

### 把 A 移到 A.new 并重新关联

```bash
mv "$SAMPLE/A.old" "$SAMPLE/A.new"
python -m asset_catalog --db "$SAMPLE/catalog.sqlite" relink "$SAMPLE/A.old" "$SAMPLE/A.new"
```

预期：退出码 0，标准错误为空，标准输出仅一行（键序不限）：

```json
{"path": "<$SAMPLE/A.new 的规范绝对路径>", "type": "image", "tags": ["demo", "ui"]}
```

### 随后核对

```bash
python -m asset_catalog --db "$SAMPLE/catalog.sqlite" show "$SAMPLE/A.new"
python -m asset_catalog --db "$SAMPLE/catalog.sqlite" query --tag demo
python -m asset_catalog --db "$SAMPLE/catalog.sqlite" show "$SAMPLE/A.old"
```

预期：`show A.new` 返回 A 的完整记录（path 为 A.new 的规范路径，类型
`image`，标签 `["demo", "ui"]`）；`query --tag demo` 仍依次返回 A、B，
只有 A 的路径变化；`show A.old` 退出码 2，标准错误说明素材未登记并包含
A.old 的规范绝对路径。

**验证状态**：以上成功样例已在本仓库当前实现上实际执行（Python 3、Linux、
临时目录），退出码、标准错误为空、relink / show / query 的 JSON 内容均与
上述预期一致。对应回归测试：`tests/test_relink_regression.py` 中的
`test_acceptance_move_a_then_relink`。

## 6. 固定样例（写入被拒绝，对照回归测试）

本样例对照 `tests/test_relink_regression.py` 的
`test_write_failure_rolls_back_and_keeps_original_record`，
拒绝条件与该测试完全一致。

### 场景设置

- 目录与路径约定同第 5 节；按 A.old、B.bin 顺序登记同样的两条素材
  （A：image，标签 demo、ui；B：audio，标签 demo）。
- 在样例库上附加一个固定拒绝条件（保留原有兼容表结构，仅加触发器）：

```sql
CREATE TRIGGER reject_relink
BEFORE UPDATE OF path ON asset
BEGIN
    SELECT RAISE(ABORT, 'relink blocked');
END;
```

### 输入与预期结果

```bash
python -m asset_catalog --db "$SAMPLE/catalog.sqlite" relink "$SAMPLE/A.old" "$SAMPLE/A.new"
```

预期：退出码 2，标准输出为空，标准错误为单行、不含调用栈，且同时包含
`数据库读写失败` 与数据库给出的拒绝文本 `relink blocked`：

```
asset_catalog: 错误: 数据库读写失败: relink blocked
```

失败后由新进程 `export` 核对：仍按 A、B 顺序返回原记录——A 的路径仍为
A.old 的规范路径、类型仍为 `image`、标签仍为 `["demo", "ui"]`，B 的记录
不变；原记录完整保留，不留部分修改。撤去拒绝条件
（`DROP TRIGGER reject_relink`）后用同一输入再次提交，则按第 5 节的
成功结果完成关联。

**验证状态**：以上失败样例已在本仓库当前实现上实际执行（Python 3、Linux、
临时目录），退出码 2、标准输出为空、标准错误单行内容、失败后 export 的
原记录保留结果均与上述预期一致。对应测试位置：
`tests/test_relink_regression.py`（触发器 SQL 见该文件
`test_write_failure_rolls_back_and_keeps_original_record`）。

## 7. 回归对照

- `tests/test_relink_regression.py`：验收场景（移动后关联、show/query
  核对）、跨进程持久化、原文件删除或变为目录、等价路径写法、新旧路径
  相同、符号链接按目标解析、文件只读、各类参数错误、未登记、新路径
  缺失/非普通文件/已登记/等于数据库路径、空库创建与父目录缺失、
  数据库打开/结构问题、写入失败回滚与恢复、其他命令行为不变等全部边界。
