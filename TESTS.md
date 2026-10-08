# 回归测试运行说明

本仓库的回归测试仅依赖 Python 3 标准库，无需安装任何第三方包，也无需联网。
现有以下可独立执行的测试：

- `tests/test_query_regression.py`：`query`（按标签精确查询）回归测试；
- `tests/test_add_dedup_regression.py`：`add` 同一规范路径拒绝重复登记的回归测试；
- `tests/test_add_reject_db_regression.py`：`add` 拒绝损坏或不兼容数据库的回归测试；
- `tests/test_add_db_as_asset_regression.py`：`add` 拒绝把目录数据库自身登记为素材的回归测试；
- `tests/test_query_multi_tag_regression.py`：`query` 多标签（重复 `--tag`）交集查询的回归测试。
- `tests/test_query_tag_mode_any_regression.py`：`query --tag-mode any` 任选标签查询（含默认/`all` 交集不变、与 `--type`、`--check-files` 组合及参数错误）的回归测试。
- `tests/test_query_exclude_tag_regression.py`：`query --exclude-tag` 按标签排除素材（含 all/any 与 `--type`、命中与排除同标签、与 `--check-files`/`--file-status` 组合、参数错误与其他子命令拒绝）的回归测试。
- `tests/test_export_regression.py`：`export` 完整目录导出（顺序、字段、文件状态无关、空库与各类错误）及 `export --check-files` 全目录文件状态查看的回归测试。
- `tests/test_show_regression.py`：`show` 按路径查看单条记录（验收命令、等价路径与符号链接、源文件删除或变目录、未登记、参数与数据库错误、导出顺序不变）的回归测试。
- `tests/test_show_file_status_regression.py`：`show --check-files` 单条记录文件状态查看（missing/not_file/present 验收序列、断链与中间组件、未登记与权限错误、只读与其他素材状态无关）的回归测试。
- `tests/test_retag_regression.py`：`retag` 标签替换（规范化、持久化、源文件状态无关、各类错误）的回归测试。
- `tests/test_retag_append_regression.py`：`retag --append` 标签追加（保留旧标签、末尾追加、去重与大小写、幂等、原子性与各类错误）的回归测试。
- `tests/test_retag_remove_regression.py`：`retag --remove` 标签移除（保留顺序、区分大小写、幂等、至少保留一个标签、与 `--append` 互斥、原子性与各类错误）的回归测试。
- `tests/test_retag_atomicity.py`：`retag` 写入中途失败的原子性专项回归测试（旧标签进入替换流程后失败仍保留既有记录、不留部分新标签）。
- `tests/test_add_write_failure_regression.py`：`add` 写入被数据库约束或触发器拒绝时的失败原因归类回归测试（未登记路径报数据库写入失败而非已登记、整体回滚、撤去条件后恢复成功、真实重复仍拒绝）。
- `tests/test_view_database_rejection_regression.py`：只含用户视图（无用户表）的 SQLite 文件必须被四个目录操作拒绝、空库仍正常初始化的回归测试。
- `tests/test_query_nested_path.py`：`query` 在登记路径的中间组件被替换为普通文件（不再是目录）时，文件状态检查与 `--file-status` 筛选仍把该路径归为 `missing`、目录记录可追溯的回归测试。
- `tests/test_retype_write_failure_regression.py`：`retype` 类型写入被数据库触发器拒绝时旧记录完整保留、撤去拒绝条件后同一输入成功修改的回归测试。
- `tests/test_export_symlink_status_regression.py`：`export --check-files` 在登记路径后来变为符号链接时按链接目标判状态、输出仍保留原登记路径的专项回归测试（有效链接 present、断链 missing、重建后恢复 present、指向目录 not_file）。
- `tests/test_relink_regression.py`：`relink` 重新关联本地路径（固定样例验收、等价路径与符号链接、同路径返回原记录、目标冲突与目录数据库拒绝、参数与数据库错误、写入失败回滚恢复）的回归测试。

三组测试均从 README 公开的 `python -m asset_catalog` 入口以**独立子进程**方式调用，
每个用例使用 `tempfile` 创建**独立的临时样例文件与 SQLite 数据库**，
结束后自动清理，不接触、不修改已有目录或素材；
通过 `json.loads` 比较解析后的内容，不依赖 JSON 空白或对象键顺序。

## add 路径去重回归测试（新增）

### 运行入口

在项目根目录（`asset_catalog/` 所在目录）执行：

```sh
python -m unittest tests.test_add_dedup_regression -v
```

也可以直接运行测试文件：

```sh
python tests/test_add_dedup_regression.py
```

### 覆盖内容

1. 以**相对路径**首次登记样例 A（类型 `image`，标签依次为 `demo`、`ui`）：
   退出码 0、标准错误为空，标准输出为单个 JSON 对象，
   含规范绝对路径、原类型与完整标签顺序 `["demo", "ui"]`。
2. 分别用以下等价写法再次登记 A（每次均传不同类型 `audio` 与标签 `replacement`）：
   首次登记的相对路径写法、同一文件的绝对路径、含 `.` 的等价路径、
   含 `..` 的等价路径、同时含 `.` 与 `..` 的等价路径。
   每次均为：退出码 2、标准输出为空、标准错误说明重复登记（含冲突的规范路径）
   且不含调用栈。
3. 每次重复尝试失败后重新查询：`demo` 只返回 A 的原始记录（类型与标签未被
   覆盖、合并或更新），`replacement` 返回 `[]`；查询退出码均为 0、标准错误为空。
4. 对照样例 B 与 A **内容相同但规范路径不同**：以 `image` 和相同的两个标签登记成功，
   查询 `demo` 按首次登记顺序各返回 A、B 一次——去重依据是路径而非文件内容。
5. 登记与查询分别由独立进程完成；全部登记结束后由**新启动的查询进程**读取
   同一数据库，结果不变。
6. 全部操作结束后，A、B 两个样例文件的内容与登记前一致。

### 成功结果

成功时输出 `OK`，例如：

```
test_add_success_reports_canonical_record ... ok
test_duplicate_spellings_rejected_and_original_record_kept ... ok
test_records_persist_across_processes ... ok
test_same_content_different_path_registered_separately ... ok
test_sample_files_unchanged_after_all_operations ... ok

----------------------------------------------------------------------
Ran 5 tests in ...s

OK
```

重复执行结论一致；任一断言失败时 unittest 以非零退出码退出，
并打印预期值与实际值的差异（含失败的路径写法说明）。

## query 标签查询回归测试（原有）

### 运行入口

在项目根目录（`asset_catalog/` 所在目录）执行：

```sh
python -m unittest tests.test_query_regression -v
```

也可以直接运行测试文件：

```sh
python tests/test_query_regression.py
```

### 测试方式

- 从 README 公开的 `python -m asset_catalog` 入口以**独立子进程**方式调用
  （`add` 仅用于准备样例数据，测试对象为 `query`）。
- 每个用例使用 `tempfile` 创建**独立的临时样例文件与 SQLite 数据库**，
  结束后自动清理，不接触、不修改已有目录或素材。
- 通过 `json.loads` 比较解析后的内容，不依赖 JSON 空白或对象键顺序。

覆盖内容：

1. 素材 A（image，标签 ` demo `、`ui`、`demo`、`Demo`）与 B（audio，标签 `ui`、`demo`）
   依次登记后：
   - 查询 `" demo "` 按登记顺序返回 A、B，各一次；路径为规范绝对路径，类型保持登记值；
   - A 的完整标签为 `["demo","ui","Demo"]`，B 为 `["ui","demo"]`（校验完整标签及顺序）；
   - 查询 `"Demo"` 只返回 A（大小写区分）；查询 `"de"` 返回 `[]`（完整匹配、无子串匹配）；
   - 以上查询退出码均为 0，标准输出仅为 JSON 数组，标准错误为空。
2. 登记结束后由**新启动的查询进程**读取同一数据库，得到同样的记录。
3. 删除测试自建的 A 文件后，查询 `demo` 仍返回 A、B 的原有元数据。
4. 缺少 `--tag`、标签仅含空白时：退出码 2、标准输出为空、
   标准错误说明原因且不含调用栈；错误后再次查询 `demo`，原有结果不变。

### 成功结果

成功时输出 `OK`，例如：

```
test_missing_or_blank_tag_errors_leave_records_intact ... ok
test_query_exact_tag_matching_and_full_tags ... ok
test_records_read_by_fresh_process_after_registration ... ok
test_records_remain_after_source_file_deleted ... ok

----------------------------------------------------------------------
Ran 4 tests in ...s

OK
```

重复执行结论一致；任一断言失败时 unittest 以非零退出码退出并打印差异。

## add 拒绝损坏或不兼容数据库回归测试（新增）

### 运行入口

在项目根目录（`asset_catalog/` 所在目录）执行：

```sh
python -m unittest tests.test_add_reject_db_regression -v
```

也可以直接运行测试文件：

```sh
python tests/test_add_reject_db_regression.py
```

### 覆盖内容

每次登记都提供存在的普通文件、非空类型与有效标签，
避免把素材参数错误混入数据库保护检查。拒绝场景：

1. 数据库文件内容为固定的非 SQLite 文本：执行一次 `add` 后退出码 2、
   标准输出为空、标准错误说明数据库原因且不含调用栈
   （不要求底层 SQLite 错误逐字一致）。
2. 数据库文件是含其他业务表及一条固定记录的 SQLite 文件：同样被拒绝；
   失败后该业务表与固定记录仍在，既不被覆盖也不被补表或重建。
3. 数据库文件具有 `asset` 与 `asset_tag` 表但 `asset` 缺少 `type` 列：
   同样被拒绝。
4. 以上每种场景失败前后，数据库文件的字节内容完全一致，
   待登记的样例素材内容也不变。
5. 对照样例：正常数据库先登记类型 `image` 的素材 A，再向同一数据库登记
   路径不同、类型 `audio` 的素材 B（两者标签均为 `demo`）。第二次登记
   退出码 0、标准错误为空，标准输出解析后为 B 的规范路径、类型与完整标签；
   随后由**新启动的 query 进程**查询 `demo`，按登记顺序返回 A、B，
   原记录不变。

全部样例与数据库由测试在临时目录中独立创建并清理，不访问已有素材，
不依赖网络、第三方包或平台权限设置。

### 成功结果

成功时输出 `OK`，例如：

```
test_control_valid_database_registers_two_assets ... ok
test_rejects_non_sqlite_database_file ... ok
test_rejects_schema_missing_type_column ... ok
test_rejects_sqlite_with_foreign_business_table ... ok

----------------------------------------------------------------------
Ran 4 tests in ...s

OK
```

重复执行结论一致；任一预期不符时 unittest 以非零退出码退出，
并指出失败场景。

## add 拒绝把目录数据库自身登记为素材回归测试（新增）

### 运行入口

在项目根目录（`asset_catalog/` 所在目录）执行：

```sh
python -m unittest tests.test_add_db_as_asset_regression -v
```

也可以直接运行测试文件：

```sh
python tests/test_add_db_as_asset_regression.py
```

### 覆盖内容

1. 正常登记一个 image 素材（标签依次为 `demo`、`ui`）后，以目录数据库
   作为素材路径、以 `database` 类型和 `self` 标签发起 `add`：退出码 2、
   标准输出为空、标准错误说明目录数据库不能作为本次素材登记并包含冲突的
   规范绝对路径，不含调用栈。
2. 等价写法均适用：素材侧与数据库侧各自使用相对路径、绝对路径、
   含 `.` / `..` 的写法，以及（环境支持时）指向数据库的符号链接，
   结论不变。
3. 每次拒绝后：数据库文件字节与拒绝前完全一致；`query --tag demo`
   仍只返回原素材及完整标签，`query --tag self` 返回 `[]`。
4. 已有空文件与数据库路径相同时同样被拒绝，且不会因此被初始化为
   目录数据库（文件仍为空）。
5. 已有非 SQLite 文件或不兼容 SQLite 文件在两条路径相同且其他登记参数
   有效时，同样报告路径冲突并保持原内容。
6. 对照样例：路径不同的另一份 SQLite 文件仍能以 `database` 类型和
   `self` 标签登记到该目录数据库——不按扩展名或内容一概拒绝素材。
7. 登记路径不存在、不是普通文件、类型或标签去空白后为空、缺少必填
   参数时，仍沿用原有错误规则（退出码 2、标准输出为空、说明对应原因），
   不创建数据库文件。

### 成功结果

成功时输出 `OK`，例如：

```
test_control_other_sqlite_file_still_registered ... ok
test_db_as_asset_rejected_and_state_untouched ... ok
test_empty_file_not_initialized ... ok
test_equivalent_spellings_on_both_sides_rejected ... ok
test_incompatible_sqlite_file_same_path_reports_conflict ... ok
test_non_sqlite_file_same_path_reports_conflict ... ok
test_original_error_rules_unchanged ... ok
test_symlink_spellings_rejected ... ok

----------------------------------------------------------------------
Ran 8 tests in ...s

OK
```

重复执行结论一致；任一预期不符时 unittest 以非零退出码退出，
并指出失败场景。

## query 多标签交集查询回归测试（新增）

### 运行入口

在项目根目录（`asset_catalog/` 所在目录）执行：

```sh
python -m unittest discover -s tests -p test_query_multi_tag_regression.py -v
```

也可以直接运行测试文件：

```sh
python tests/test_query_multi_tag_regression.py
```

### 测试方式

- 从 README 公开的 `python -m asset_catalog` 入口以**独立子进程**方式调用
  （`add` 仅用于准备样例数据，测试对象为 `query` 的多标签交集语义）。
- 每个用例使用 `tempfile` 创建**独立的临时样例文件与 SQLite 数据库**，
  结束后自动清理，不接触、不修改已有目录或素材，不依赖网络或外部素材。
- 通过 `json.loads` 比较解析后的内容，不依赖 JSON 空白或对象键顺序。

### 覆盖内容

固定样例按 A、B、C 顺序登记：A 类型 `image`，标签依次为 `demo`、`ui`、`Demo`；
B 类型 `audio`，只有 `demo`；C 类型 `audio`，标签依次为 `ui`、`demo`。

1. 查询 `--tag demo --tag ui` 只返回同时具备两标签的 A、C，各出现一次且保持
   登记顺序，只带 `demo` 的 B 不入选；每条结果保留登记时的规范绝对路径、
   类型与完整标签（A 的 `Demo` 标签保留，C 的标签顺序 `["ui", "demo"]`
   不被查询条件改变）。
2. 交换两个条件的顺序、重复传入 `demo`、给任一条件增加首尾空白，
   结果都与原查询相同。
3. 查询 `Demo` 与 `ui` 只返回 A（大小写区分）；查询 `de` 与 `ui`、
   `demo` 与未登记标签 `absent` 均返回 `[]`（完整匹配、交集语义）。
4. 以上成功查询退出码均为 0，标准错误为空，标准输出仅为一个 JSON 数组。
5. 在有效 `demo` 条件之后追加空字符串标签或仅含空白的标签：整次查询
   退出码 2、标准输出为空、标准错误指出 `--tag` 的问题且不含调用栈，
   不返回前面有效条件的结果；每次失败后重新查询 `demo` 与 `ui`
   仍得到原有 A、C，样例文件内容保持不变。

### 成功结果

成功时输出 `OK`，例如：

```
test_blank_tag_conditions_rejected_and_state_intact ... ok
test_case_sensitive_exact_match_and_absent_tag ... ok
test_condition_order_duplicates_and_whitespace_equivalent ... ok
test_multi_tag_intersection_returns_a_and_c ... ok

----------------------------------------------------------------------
Ran 4 tests in ...s

OK
```

重复执行结论一致；任一预期不符时 unittest 以非零退出码退出，
并打印对应场景的预期值与实际值差异。

## query 任选标签查询（--tag-mode any）回归测试（新增）

### 运行入口

在项目根目录（`asset_catalog/` 所在目录）执行：

```sh
python -m unittest discover -s tests -p test_query_tag_mode_any_regression.py -v
```

也可以直接运行测试文件：

```sh
python tests/test_query_tag_mode_any_regression.py
```

### 测试方式

- 从 README 公开的 `python -m asset_catalog` 入口以**独立子进程**方式调用
  （`add` 仅用于准备样例数据，测试对象为 `query` 的 `--tag-mode` 语义）。
- 每个用例使用 `tempfile` 创建**独立的临时样例文件与 SQLite 数据库**，
  结束后自动清理，不接触、不修改已有目录或素材，不依赖网络或外部素材。
- 通过 `json.loads` 比较解析后的内容，不依赖 JSON 空白或对象键顺序。

### 覆盖内容

固定验收样例按 A、B、C 顺序登记：A 类型 `image`，标签依次为 `demo`、`ui`；
B 类型 `audio`，只有 `demo`；C 类型 `image`，只有 `ui`。

1. 不传 `--tag-mode` 与显式 `all` 查询 `demo`、`ui` 仍只返回 A（交集语义不变）。
2. `--tag-mode any` 查询 `demo`、`ui` 按登记顺序返回 A、B、C，命中两个条件的
   A 只出现一次；每条记录仍只含 `path`、`type`、`tags`，保留规范绝对路径、
   登记类型与完整标签顺序，无匹配方式或内部编号字段。
3. `any` 加 `--type image` 返回 A、C（既要命中任一标签也要匹配类型）；
   类型不匹配时输出 `[]`。
4. `any` 下交换条件顺序、重复传入标签、给条件增加首尾空白，结果都相同。
5. `any` 仍区分大小写并完整匹配：`Demo`、`de` 与未登记标签均不产生误命中。
6. 删除源文件 C 后 `any` 仍按登记标签入选；不传 `--check-files` 时不检查
   文件状态，传入时只为入选记录追加 `present` / `missing` / `not_file`。
7. 空目录或无匹配返回 `[]`。
8. `--tag-mode` 缺少值、为空、含首尾空白、大小写不符或不是 `all`、`any`：
   退出码 2、标准输出为空、标准错误指出该选项且不含调用栈，参数错误不创建
   数据库、不改变已有记录。
9. `any` 下任一 `--tag` 为空或只有空白（即使其他标签有效）：整次查询退出码 2、
   标准输出为空、标准错误指向 `--tag` 且不含调用栈；失败后重新查询结果不变。

### 成功结果

成功时输出 `OK`，例如：

```
test_any_combined_with_type_filter ... ok
test_any_deleted_source_file_still_selected_and_check_files ... ok
test_any_duplicates_order_and_whitespace_equivalent ... ok
test_any_empty_directory_or_no_match_returns_empty_array ... ok
test_any_returns_assets_having_either_tag_once_each ... ok
test_any_still_case_sensitive_and_exact ... ok
test_blank_tag_rejected_even_under_any ... ok
test_default_and_explicit_all_keep_intersection ... ok
test_invalid_tag_mode_leaves_existing_records_intact ... ok
test_invalid_tag_mode_rejected_without_creating_database ... ok
test_tag_mode_rejected_for_other_subcommands ... ok

----------------------------------------------------------------------
Ran 11 tests in ...s

OK
```

重复执行结论一致；任一预期不符时 unittest 以非零退出码退出，
并指出失败场景。

## query 按标签排除素材（--exclude-tag）回归测试（新增）

### 运行入口

在项目根目录（`asset_catalog/` 所在目录）执行：

```sh
python -m unittest discover -s tests -p test_query_exclude_tag_regression.py -v
```

也可以直接运行测试文件：

```sh
python tests/test_query_exclude_tag_regression.py
```

### 测试方式

- 从 README 公开的 `python -m asset_catalog` 入口以**独立子进程**方式调用
  （`add` 仅用于准备样例数据，测试对象为 `query` 的 `--exclude-tag` 语义）。
- 每个用例使用 `tempfile` 创建**独立的临时样例文件与 SQLite 数据库**，
  结束后自动清理，不接触、不修改已有目录或素材，不依赖网络或外部素材。
- 通过 `json.loads` 比较解析后的内容，不依赖 JSON 空白或对象键顺序。

### 覆盖内容

固定验收样例按 A、B、C 顺序登记为 `image`：A 标签 `demo`、`ui`；
B 标签 `demo`、`archived`；C 标签 `ui`。

1. 验收：`query --tag-mode any --tag demo --tag ui --exclude-tag archived`
   先按 any 与可选类型确定范围（A、B、C），再排除具备 `archived` 的 B，
   只按 A、C 的首次登记顺序输出，每条只出现一次，记录仅含 `path`、
   `type`、`tags`，完整标签保持原顺序。
2. 同一数据库 `query --tag demo --tag ui --exclude-tag ui`（all 语义）
   命中的 A 同时具备排除标签 `ui`，仍执行排除、不报冲突错误，输出 `[]`；
   两次成功均退出码 0、标准错误为空、标准输出仅为 JSON 数组。
3. 排除标签逐个去除首尾空白，重复条件只生效一次，条件顺序不影响结果，
   按完整文本区分大小写匹配；不存在的排除标签不影响结果。
4. 与 `--type` 同用时先按标签与类型确定范围再排除；不传 `--exclude-tag`
   时保持已有查询行为。
5. 排除只作用于本次查询：`export` 仍按 A、B、C 返回完整记录，
   不修改记录或源文件，旧数据库无需迁移。
6. 与 `--check-files` 或 `--file-status` 同用时，被排除素材不检查文件
   状态（样例删除被排除的 B 后查询仍成功），剩余范围沿用既有状态规则。
7. `--exclude-tag` 缺值、为空或仅有空白时整次退出码 2、标准输出为空、
   标准错误指出该选项且不含调用栈，即使其他条件有效也不返回部分结果，
   且不创建数据库；失败后重新查询结果不变。
8. `--exclude-tag` 只属于 `query`：`add`、`export`、`retag`、`retype`
   收到它按参数错误拒绝（退出码 2、标准输出为空、标准错误指出该选项），
   既有记录保持不变。

### 成功结果

成功时输出 `OK`，例如：

```
test_acceptance_any_exclude_archived_returns_a_c ... ok
test_acceptance_same_tag_hit_and_excluded_returns_empty ... ok
test_blank_or_missing_exclude_tag_rejected_without_creating_db ... ok
test_exclude_is_query_only_and_does_not_modify_records ... ok
test_exclude_normalization_duplicates_order_whitespace_case ... ok
test_exclude_tag_rejected_by_other_subcommands ... ok
test_exclude_with_type_and_without_option ... ok
test_excluded_asset_file_status_not_checked ... ok

----------------------------------------------------------------------
Ran 8 tests in ...s

OK
```

重复执行结论一致；任一预期不符时 unittest 以非零退出码退出，
并指出失败场景。

## export 完整目录导出回归测试（新增）

### 运行入口

在项目根目录（`asset_catalog/` 所在目录）执行：

```sh
python -m unittest tests.test_export_regression -v
```

也可以直接运行测试文件：

```sh
python tests/test_export_regression.py
```

### 测试方式

- 从 README 公开的 `python -m asset_catalog` 入口以**独立子进程**方式调用
  （`add` 仅用于准备样例数据，测试对象为 `export`）。
- 每个用例使用 `tempfile` 创建**独立的临时样例文件与 SQLite 数据库**，
  结束后自动清理，不接触、不修改已有目录或素材，不依赖网络或外部素材。
- 通过 `json.loads` 比较解析后的内容，不依赖 JSON 空白或对象键顺序。

### 覆盖内容

固定验收样例按 A、B 顺序登记：A 类型 `image`，标签依次为 `demo`、`ui`；
B 类型 `audio`，标签只有 `music`。

1. `export` 无任何筛选参数，按 A、B 的首次登记顺序输出两条记录，
   每条素材出现一次；每条记录仅含 `path`、`type`、`tags`，
   保留规范绝对路径、类型文本与完整标签顺序，没有 `file_status` 或内部编号。
2. 删除演示文件 B 后导出：B 仍在结果中（导出不读取素材内容、不检查文件状态）；
   导出后由新进程执行 `query --tag demo` 仍只返回 A，数据库登记内容未改变，
   未删除素材的文件字节不变。
3. 空目录输出 `[]`：数据库文件不存在但父目录存在时创建空目录数据库并输出
   `[]`，新进程再次读取结论一致；父目录缺失时报错且不补建目录。
4. 缺少 `--db`、数据库路径为空、向 `export` 传入不支持的参数
   （`--tag`、`--type`、`--file-status`、位置参数）、数据库路径指向目录：
   退出码 2、标准输出为空、标准错误说明原因且不含调用栈。
5. 非 SQLite 文件、含其他业务表的 SQLite 文件（固定记录保留）、
   `asset` 缺少 `type` 列的不兼容结构：均被拒绝，文件字节前后完全一致，
   不被覆盖、补表或重建。
6. 正常数据库截断为损坏镜像时导出：退出码 2、标准输出为空，
   不输出任何部分记录。
7. `export --check-files`：删除演示文件 B 后带选项导出仍按 A、B 顺序
   返回两条完整记录，`file_status` 分别为 `present` 与 `missing`，
   退出码 0、标准错误为空、标准输出仅一行 JSON 数组；新进程默认导出
   仍返回两条记录且不含 `file_status`。
8. `export --check-files` 对目录等非普通文件报告 `not_file`，
   输出 `path` 保留登记值；空目录带选项输出 `[]`。
9. `export --check-files` 中任一记录因权限错误无法判断状态时：
   整次导出退出码 2、标准输出为空，标准错误说明无法确定文件状态并
   包含相关路径，不含调用栈（root 下跳过该用例）。

### 成功结果

成功时输出 `OK`，例如：

```
test_check_files_appends_status_and_keeps_all_records ... ok
test_check_files_empty_database_outputs_empty_array ... ok
test_check_files_reports_directory_as_not_file ... ok
test_check_files_status_error_fails_whole_export ... ok
test_corrupt_and_incompatible_databases_rejected_and_untouched ... ok
test_database_path_is_directory_rejected ... ok
test_deleted_source_file_still_exported_without_file_status ... ok
test_empty_database_exports_empty_array ... ok
test_empty_database_path_rejected ... ok
test_export_does_not_change_registrations_or_files ... ok
test_export_returns_all_records_in_registration_order ... ok
test_malformed_database_produces_no_partial_output ... ok
test_missing_db_option_rejected ... ok
test_missing_parent_directory_is_not_created ... ok
test_unsupported_export_arguments_rejected ... ok

----------------------------------------------------------------------
Ran 15 tests in ...s

OK
```

重复执行结论一致；任一预期不符时 unittest 以非零退出码退出，
并指出对应场景。

## show 按路径查看单条记录回归测试（新增）

### 运行入口

在项目根目录（`asset_catalog/` 所在目录）执行：

```sh
python -m unittest tests.test_show_regression -v
```

也可以直接运行测试文件：

```sh
python tests/test_show_regression.py
```

### 测试方式

- 从 README 公开的 `python -m asset_catalog` 入口以**独立子进程**方式调用
  （`add` 仅用于准备样例数据，`retag`/`retype` 用于核对当前值，`export`
  用于验证登记内容不变，测试对象为 `show`）。
- 每个用例使用 `tempfile` 创建**独立的临时样例文件与 SQLite 数据库**，
  结束后自动清理，不接触、不修改已有目录或素材，不依赖网络或外部素材；
  验收命令在临时演示目录中以相对路径 `./A.bin`、`./C.bin` 执行。
- 通过 `json.loads` 比较解析后的内容，不依赖 JSON 空白或对象键顺序。

### 覆盖内容

固定验收样例按 A.bin、B.bin 顺序登记：A 类型 `image`，标签依次为 `demo`、
`ui`；B 类型 `audio`，标签只有 `music`。

1. 验收：在同一演示目录执行
   `python -m asset_catalog --db catalog.sqlite show ./A.bin`：
   退出码 0、标准错误为空，标准输出仅一行 JSON 对象，只含 `path`、`type`、
   `tags`，分别为 A 的规范绝对路径、`image` 与 `["demo", "ui"]`。
2. 在同一演示目录执行 `show ./C.bin` 查询未登记的 C.bin：退出码 2、
   标准输出为空，标准错误说明素材未登记并包含 C.bin 的规范绝对路径。
3. 路径等价写法（相对路径、绝对路径、含 `.`、含 `..`、同时含两者）以及
   解析后指向同一登记路径的符号链接均定位同一记录（环境不支持符号链接时
   跳过对应用例），输出的 `path` 始终是登记时的规范绝对路径。
4. `show` 不读取素材内容、不检查当前文件状态：源文件删除后、原登记路径
   变成目录后，通过原路径仍返回登记记录；`show` 前后数据库字节与未删除
   素材的字节完全一致。
5. `retag`、`retype` 之后 `show` 反映当前保存的类型与完整标签，标签顺序
   不变；其他素材不受影响。
6. 缺少素材路径或 `--db`、素材或数据库路径为空字符串、传入不支持的参数
   （多余位置参数、`--tag`、`--type`、`--file-status`、`--append`、未知
   选项）：退出码 2、标准输出为空、标准错误指出对应参数及原因且不含调用栈；
   参数错误不创建数据库。`--check-files` 现为 show 的合法可选开关，源文件
   存在时命中记录并追加 `present`，其文件状态专项行为见
   `test_show_file_status_regression.py`。
7. 数据库文件不存在但父目录存在时沿用空库创建规则后报告未登记，新进程
   `export` 该库得到 `[]`；父目录缺失时报错且不补建目录。
8. 数据库路径指向目录、非 SQLite 文件、含其他业务表的 SQLite 文件、
   缺列的不兼容结构、截断的损坏镜像：均退出码 2、标准输出为空、不输出
   部分记录，非兼容文件字节前后完全一致。
9. 成功与失败后再次 `export`：已有记录的路径、类型、标签及 A、B 的登记
   顺序均不变；`show` 兼容现有数据库，`add`、`query` 等其他命令行为不变。

### 成功结果

成功时输出 `OK`，例如：

```
test_acceptance_relative_path_in_demo_directory ... ok
test_argument_errors_do_not_create_database ... ok
test_corrupt_and_incompatible_databases_rejected_and_untouched ... ok
test_database_path_is_directory_rejected ... ok
test_deleted_source_still_shown_through_original_path ... ok
test_directory_at_registered_path_still_shown ... ok
test_empty_database_path_rejected ... ok
test_equivalent_path_spellings_resolve_same_record ... ok
test_export_unchanged_after_success_and_failure ... ok
test_fresh_database_created_then_unregistered_reported ... ok
test_missing_db_option_rejected ... ok
test_missing_parent_directory_is_not_created ... ok
test_missing_path_or_unsupported_arguments_rejected ... ok
test_show_is_readonly_against_database_and_source ... ok
test_show_reflects_current_type_and_full_tag_order ... ok
test_show_works_with_existing_database_and_other_commands_intact ... ok
test_symlink_resolving_to_registered_path_shows_same_record ... ok
test_unregistered_c_rejected_by_relative_path_in_demo_directory ... ok

----------------------------------------------------------------------
Ran 18 tests in ...s

OK
```

重复执行结论一致；任一预期不符时 unittest 以非零退出码退出，
并指出对应场景。

## show --check-files 单条记录文件状态回归测试（新增）

### 运行入口

在项目根目录（`asset_catalog/` 所在目录）执行：

```sh
python -m unittest tests.test_show_file_status_regression -v
```

也可以直接运行测试文件：

```sh
python tests/test_show_file_status_regression.py
```

### 测试方式

- 从 README 公开的 `python -m asset_catalog` 入口以**独立子进程**方式调用
  （`add` 仅用于准备样例数据，`export` 用于核对登记内容不变，测试对象为
  `show --check-files`）。
- 每个用例使用 `tempfile` 创建**独立的临时样例文件与 SQLite 数据库**，
  结束后自动清理，不接触、不修改已有目录或素材，不依赖网络或外部素材；
  验收命令在临时演示目录中以相对路径 `A.bin` 执行。
- 权限错误通过子进程内对指定路径的 `os.stat` 确定性注入
  `PermissionError` 复现，不依赖系统权限配置；符号链接用例在环境不支持时
  自行跳过。
- 通过 `json.loads` 比较解析后的内容，不依赖 JSON 空白或对象键顺序。

### 覆盖内容

固定验收样例按 A.bin、B.bin 顺序登记：A 类型 `image`，标签依次为 `demo`、
`ui`；B 类型 `audio`，标签只有 `music`。

1. 验收序列：登记后删除 A.bin，在演示目录执行
   `show A.bin --check-files` 返回原规范路径、`image`、`["demo", "ui"]` 与
   `file_status="missing"`，退出码 0、标准错误为空、标准输出仅一行四键 JSON；
   随后 `show A.bin` 返回相同元数据且没有状态字段；原位置换成目录后带选项
   查看返回 `not_file`；恢复普通文件后返回 `present`，无需重新登记；全程
   `export` 的路径、类型、标签与 A、B 登记顺序不变。
2. 普通文件存在时为 `present`；`--check-files` 放在路径前后语义一致；
   连续查看即时反映当前状态，不保存历史状态。
3. 登记路径的中间组件被替换为普通文件（不再是目录）时为 `missing`，记录仍
   按其登记的规范路径命中；不带选项时照常返回记录。
4. 断开的符号链接按链接目标解析：目标规范路径未登记时按未登记失败
   （标准错误含目标规范路径）；登记路径处替换为指向另一条已登记普通文件的
   符号链接时，定位到目标记录并按目标报告 `present`，输出目标保存的规范
   路径。
5. 未登记路径无论文件是否存在都退出码 2、标准输出为空，标准错误说明素材
   未登记并包含规范路径，不返回其文件状态。
6. 对命中记录保存路径注入 `PermissionError`：退出码 2、标准输出为空，
   标准错误包含“无法确定文件状态”与相关路径、不含调用栈、不输出部分 JSON；
   失败后不带选项的 show 元数据不变。
7. 其他素材的文件状态（删除、变目录）不影响本次查看；查看不读取素材内容、
   不改写源文件或数据库字节，不改变标签顺序与登记顺序。
8. 参数合法、父目录存在时先创建空库再报告未登记；父目录缺失时报错且不补建
   目录——口径与不带选项一致。

### 成功结果

成功时输出 `OK`，13 个用例全部通过：

```
test_acceptance_missing_then_plain_then_not_file_then_present ... ok
test_broken_symlink_at_registered_path_locates_nothing ... ok
test_check_files_is_readonly_against_database_and_source ... ok
test_flag_before_path_works_like_flag_after ... ok
test_fresh_database_created_then_unregistered_reported ... ok
test_intermediate_component_replaced_with_file_reports_missing ... ok
test_missing_parent_directory_is_not_created ... ok
test_other_assets_state_does_not_affect_this_show ... ok
test_permission_error_fails_without_partial_output ... ok
test_present_for_existing_regular_file ... ok
test_repeated_check_files_reflects_current_state ... ok
test_symlink_to_another_registered_file_locates_target_record ... ok
test_unregistered_path_rejected_whether_file_exists_or_not ... ok

----------------------------------------------------------------------
Ran 13 tests in ...s

OK
```

重复执行结论一致；任一断言失败时 unittest 以非零退出码退出，
并指出对应场景。

## retag 标签替换回归测试（新增）

### 运行入口

在项目根目录（`asset_catalog/` 所在目录）执行：

```sh
python -m unittest tests.test_retag_regression -v
```

也可以直接运行测试文件：

```sh
python tests/test_retag_regression.py
```

### 测试方式

- 从 README 公开的 `python -m asset_catalog` 入口以**独立子进程**方式调用
  （`add` 仅用于准备样例数据，`query`/`export` 用于验证持久化结果，测试对象为 `retag`）。
- 每个用例使用 `tempfile` 创建**独立的临时样例文件与 SQLite 数据库**，
  结束后自动清理，不接触、不修改已有目录或素材，不依赖网络或外部素材。
- 通过 `json.loads` 比较解析后的内容，不依赖 JSON 空白或对象键顺序。

### 覆盖内容

固定验收样例按 A、B 顺序登记：A 类型 `image`，标签依次为 `demo`、`ui`；
B 类型 `audio`，标签只有 `demo`。

1. 验收场景：`retag A --tag " project " --tag ui --tag project` 成功，
   退出码 0、标准错误为空，标准输出仅为含 `path`、`type`、`tags` 的 JSON 对象，
   A 的 `tags` 为 `["project", "ui"]`（去除首尾空白、按首次出现顺序去重、
   保留大小写）；随后 `export` 仍按 A、B 顺序返回且 B 不变。
2. 持久化：新进程 `query` 中 `project` 命中 A、被移除的 `demo` 不再使 A 入选；
   `all`/`any`、类型筛选与 `--check-files` 规则不变。
3. 路径等价写法（相对路径、含 `.` 的写法）定位同一记录；重复提交相同的
   规范化标签与顺序仍成功。
4. 源文件已删除或原路径变成目录仍可改标签；素材文件内容保持只读。
5. 缺少路径或 `--tag`、路径为空、任一标签为空或只有空白、传入不支持的参数：
   退出码 2、标准输出为空、标准错误说明原因且不含调用栈；参数错误不创建数据库。
6. 目标未登记：退出码 2，标准错误含规范路径；数据库文件不存在但父目录存在时
   创建空库后报告未登记，父目录缺失时报错且不补建目录。
7. 数据库路径指向目录、非 SQLite 文件、含其他业务表的 SQLite 文件：
   均被拒绝，文件字节前后完全一致；失败不新增素材、不改变已有记录、
   不留部分新标签。
8. `retag` 之后 `add` 仍拒绝同一规范路径的重复登记。

### 成功结果

成功时输出 `OK`，例如：

```
test_add_still_rejects_duplicate_path_after_retag ... ok
test_argument_errors_do_not_create_database ... ok
test_database_open_and_schema_errors_rejected ... ok
test_deleted_or_directory_source_still_retaggable ... ok
test_equivalent_path_spellings_resolve_same_record ... ok
test_fresh_database_created_then_unregistered_reported ... ok
test_missing_parent_directory_is_not_created ... ok
test_missing_path_or_tag_rejected ... ok
test_retag_does_not_touch_source_file_content ... ok
test_retag_persists_for_query_in_new_process ... ok
test_retag_replaces_tags_and_export_reflects_change ... ok
test_same_normalized_tags_repeated_succeeds ... ok
test_unregistered_target_rejected_with_canonical_path ... ok

----------------------------------------------------------------------
Ran 13 tests in ...s

OK
```

重复执行结论一致；任一预期不符时 unittest 以非零退出码退出，
并指出对应场景。

## retag 标签追加（--append）回归测试（新增）

### 运行入口

在项目根目录（`asset_catalog/` 所在目录）执行：

```sh
python -m unittest tests.test_retag_append_regression -v
```

也可以直接运行测试文件：

```sh
python tests/test_retag_append_regression.py
```

### 测试方式

- 从 README 公开的 `python -m asset_catalog` 入口以**独立子进程**方式调用
  （`add` 仅用于准备样例数据，`query`/`export` 用于验证持久化结果，测试对象为 `retag --append`）。
- 每个用例使用 `tempfile` 创建**独立的临时样例文件与 SQLite 数据库**，
  结束后自动清理，不接触、不修改已有目录或素材，不依赖网络或外部素材。
- 通过 `json.loads` 比较解析后的内容，不依赖 JSON 空白或对象键顺序。

### 覆盖内容

固定验收样例按 A、B 顺序登记：A 类型 `image`，标签依次为 `demo`、`ui`；
B 类型 `audio`，标签只有 `demo`。

1. 验收命令 `retag A.bin --append --tag " ui " --tag project --tag demo`：
   退出码 0、标准错误为空，标准输出仅为含 `path`、`type`、`tags` 的 JSON 对象，
   A 的标签为 `["demo", "ui", "project"]`，类型与规范路径不变，B 不变；
   新进程 `export` 仍按 A、B 顺序返回，`query --tag project` 只返回 A，
   `query --tag demo` 仍返回 A、B 两条。
2. 追加规则：新标签按输入顺序去除首尾空白并去重，原标签及顺序保留，
   已存在的标签不移动，新标签接在末尾；比较区分大小写（`ui` 与 `UI` 是两个标签）；
   重复追加同一批标签仍成功，标签不增加也不重排。
3. 等价路径写法（相对路径、含 `.` 的写法）定位同一记录；
   源文件已删除或原路径变成目录仍可追加，操作不读取或改写素材内容。
4. 不传 `--append` 时仍执行既有完整替换规则。
5. 缺少素材路径、路径为空、缺少 `--tag`、任一标签为空或只有空白、传入不支持
   的参数、目标未登记（错误含规范路径）、数据库无法打开或读写、损坏或结构
   不兼容时：退出码 2、标准输出为空、标准错误说明原因且不含调用栈；
   参数错误不创建数据库；数据库文件不存在但父目录存在时创建空库后报告未登记，
   父目录缺失时不补建目录。
6. 追加中途失败（固定触发器只拒绝 `blocked` 标签写入）时整体回滚：
   原记录与标签顺序完整保留，不留部分新增标签；撤去拒绝条件后同一命令成功。
7. `--append` 只属于 `retag`：`add`、`query`、`export` 收到它按参数错误拒绝，
   既有记录不变。

### 成功结果

成功时输出 `OK`，例如：

```
test_append_acceptance_scenario ... ok
test_append_argument_errors_do_not_create_database ... ok
test_append_argument_errors_rejected ... ok
test_append_database_open_and_schema_errors_rejected ... ok
test_append_deleted_or_directory_source_still_works ... ok
test_append_does_not_touch_source_file_content ... ok
test_append_equivalent_path_spellings_resolve_same_record ... ok
test_append_fresh_database_created_then_unregistered_reported ... ok
test_append_mid_write_failure_keeps_old_tags_then_recovers ... ok
test_append_missing_parent_directory_is_not_created ... ok
test_append_option_rejected_by_other_commands ... ok
test_append_preserves_order_and_is_case_sensitive ... ok
test_append_unregistered_target_rejected_with_canonical_path ... ok
test_repeated_append_is_idempotent ... ok
test_retag_without_append_still_replaces_all_tags ... ok

----------------------------------------------------------------------
Ran 15 tests in ...s

OK
```

重复执行结论一致；任一预期不符时 unittest 以非零退出码退出，
并指出对应场景。

## retag 标签移除（--remove）回归测试（新增）

### 运行入口

在项目根目录（`asset_catalog/` 所在目录）执行：

```sh
python -m unittest tests.test_retag_remove_regression -v
```

也可以直接运行测试文件：

```sh
python tests/test_retag_remove_regression.py
```

### 测试方式

- 从 README 公开的 `python -m asset_catalog` 入口以**独立子进程**方式调用
  （`add` 仅用于准备样例数据，`query`/`export` 用于验证持久化结果，测试对象为 `retag --remove`）。
- 每个用例使用 `tempfile` 创建**独立的临时样例文件与 SQLite 数据库**，
  结束后自动清理，不接触、不修改已有目录或素材，不依赖网络或外部素材。
- 通过 `json.loads` 比较解析后的内容，不依赖 JSON 空白或对象键顺序。

### 覆盖内容

固定验收样例按 A、B 顺序登记：A 类型 `image`，标签依次为 `demo`、`ui`、`UI`；
B 类型 `audio`，标签只有 `demo`。

1. 验收命令 `retag A.bin --remove --tag " ui " --tag absent`：
   退出码 0、标准错误为空，标准输出仅为含 `path`、`type`、`tags` 的 JSON 对象，
   A 的标签为 `["demo", "UI"]`，类型与规范路径不变，B 不变；
   新进程 `export` 仍按 A、B 顺序返回，`query --tag ui` 不再返回 A，
   `query --tag UI` 仍返回 A；随后移除 A 的 `demo` 与 `UI` 整次拒绝，
   两个标签保留。
2. 移除规则：待移除标签去除首尾空白、按完整文本区分大小写匹配
   （`ui` 与 `UI` 是两个标签），重复条件只生效一次；未指定的标签保持
   原顺序，不存在的标签忽略，重复移除同一批标签仍成功。
3. 素材至少保留一个标签：一次指定全部标签（含重复与空白写法）或对单标签
   素材移除唯一标签时整次拒绝（退出码 2，标准错误说明不能移除全部标签），
   原记录与标签顺序完整保留。
4. `--remove` 与 `--append` 互斥：同时指定按参数错误拒绝且不创建数据库；
   不传 `--remove` 时保留原替换和追加行为。
5. 等价路径写法（相对路径、含 `.` 的写法）定位同一记录；
   源文件已删除或原路径变成目录仍可移除，操作不读取或改写素材内容。
6. 缺少素材路径、路径为空、缺少 `--tag`、任一标签为空或只有空白、传入不支持
   的参数、目标未登记（错误含规范路径）、数据库无法打开或读写、损坏或结构
   不兼容时：退出码 2、标准输出为空、标准错误说明原因且不含调用栈；
   参数错误不创建数据库；数据库文件不存在但父目录存在时创建空库后报告未登记，
   父目录缺失时不补建目录。
7. 移除中途失败（固定触发器只拒绝 `blocked` 标签写入）时整体回滚：
   原记录与标签顺序完整保留，不留部分标签修改；撤去拒绝条件后同一命令成功。
8. `--remove` 只属于 `retag`：`add`、`query`、`export` 收到它按参数错误拒绝，
   既有记录不变。

### 成功结果

成功时输出 `OK`，例如：

```
test_remove_acceptance_scenario ... ok
test_remove_and_append_are_mutually_exclusive ... ok
test_remove_argument_errors_do_not_create_database ... ok
test_remove_argument_errors_rejected ... ok
test_remove_all_tags_rejected_and_record_kept ... ok
test_remove_database_open_and_schema_errors_rejected ... ok
test_remove_deleted_or_directory_source_still_works ... ok
test_remove_does_not_touch_source_file_content ... ok
test_remove_equivalent_path_spellings_resolve_same_record ... ok
test_remove_fresh_database_created_then_unregistered_reported ... ok
test_remove_is_case_sensitive_and_dedups_conditions ... ok
test_remove_mid_write_failure_keeps_old_tags_then_recovers ... ok
test_remove_missing_parent_directory_is_not_created ... ok
test_remove_option_rejected_by_other_commands ... ok
test_remove_preserves_order_of_remaining_tags ... ok
test_remove_unregistered_target_rejected_with_canonical_path ... ok
test_mutually_exclusive_modes_do_not_create_database ... ok
test_repeated_remove_is_idempotent ... ok
test_retag_without_remove_keeps_replace_and_append_behavior ... ok

----------------------------------------------------------------------
Ran 19 tests in ...s

OK
```

重复执行结论一致；任一预期不符时 unittest 以非零退出码退出，
并指出对应场景。

## retag 写入中途失败原子性专项回归测试（新增）

### 运行入口

在项目根目录（`asset_catalog/` 所在目录）执行（验收入口）：

```sh
python -m unittest discover -s tests -p test_retag_atomicity.py -v
```

也可以直接运行测试文件：

```sh
python tests/test_retag_atomicity.py
```

### 测试方式

- 从 README 公开的 `python -m asset_catalog` 入口以**独立子进程**方式调用：
  `add` 仅用于准备样例，`query`/`export` 用于核对替换结果，测试对象为 `retag`
  写入中途失败的原子性约定。
- 每个用例使用 `tempfile` 创建**独立的临时样例文件与 SQLite 数据库**，
  结束后自动清理，不接触、不修改已有目录或素材，不依赖外部素材、权限差异、
  网络或第三方库（仅用 Python 3 标准库）。
- 通过 `json.loads` 比较解析后的内容，不依赖 JSON 空白或对象键顺序。

### 固定场景与预期

样例只含两个临时演示文件，按 A.bin、B.bin 顺序登记：A 类型 `image`、
标签依次为 `demo`、`ui`；B 类型 `audio`、标签只有 `demo`。

1. 样例库保留现有兼容表结构（`asset`、`asset_tag` 及索引）和正常读取功能；
   在其上设置一个**仅拒绝 `blocked` 标签写入**的固定条件（BEFORE INSERT
   触发器），允许此前的 `project` 标签写入。设置后 `export` 仍原样返回 A、B。
2. 执行 `retag A.bin --tag project --tag blocked`：删除旧标签后，第一个新标签
   `project` 写入成功，第二个新标签 `blocked` 写入被拒，失败来自第二个新标签
   的写入；参数及数据库读取均有效。预期**退出码 2、标准输出为空**，标准错误
   说明数据库读写失败、点名 `blocked`，且**不含调用栈**。
3. 失败后重新通过公开入口读取同一目录：
   - A 的完整旧标签及顺序仍为 `["demo", "ui"]`；
   - B 的记录与两条素材的登记顺序（A、B）均不变；
   - 查询 `project` 与 `blocked` 均返回 `[]`（不留部分新标签）；
   - 查询 `demo` 仍按原顺序返回 A、B；
   - 两个素材文件的内容保持不变。
4. 在同一测试场景中撤去拒绝条件（DROP TRIGGER），再次提交相同的替换命令：
   退出码 0、标准错误为空，标准输出为既有格式（仅含 `path`、`type`、`tags`）
   的 A 记录，标签为 `["project", "blocked"]`。
5. 由**新进程**导出仍得到 A、B（登记顺序不变），只有 A 的标签改变，B 不变；
   `project`、`blocked` 查询命中 A，`demo` 查询只剩 B；素材文件内容始终不变。

### 成功结果

成功时输出 `OK`，例如：

```
test_mid_write_failure_keeps_old_tags_then_succeeds_when_condition_removed ... ok

----------------------------------------------------------------------
Ran 1 tests in ...s

OK
```

重复执行得到一致结果；任一上述行为不符时 unittest 以非零退出码退出，
并指出具体差异（退出码、输出、错误信息、持久化记录或素材文件内容）。

## add 写入失败原因归类回归测试（新增）

### 运行入口

在项目根目录（`asset_catalog/` 所在目录）执行：

```sh
python -m unittest tests.test_add_write_failure_regression -v
```

也可以直接运行测试文件：

```sh
python tests/test_add_write_failure_regression.py
```

### 测试方式

- 从 README 公开的 `python -m asset_catalog` 入口以**独立子进程**方式调用，
  测试对象为 `add` 在数据库约束或触发器拒绝写入时的失败原因归类；
  `query`/`export` 用于核对持久化结果。
- 每个用例使用 `tempfile` 创建**独立的临时样例文件与 SQLite 数据库**，
  结束后自动清理，不接触、不修改已有目录或素材，不依赖外部素材、
  网络或第三方库（仅用 Python 3 标准库）。
- 通过 `json.loads` 比较解析后的内容，不依赖 JSON 空白或对象键顺序。

### 固定场景与预期

样例只含两个内容固定、路径不同的临时演示文件 A.bin、B.bin。
先正常登记 A：类型 `image`，标签依次为 `demo`、`ui`。样例库保持原有
兼容表结构（`asset`、`asset_tag` 及索引），另加一个 BEFORE INSERT
触发器作为固定拒绝条件：只拒绝 `blocked` 标签写入，拒绝文本为
`rejected by fixed condition: blocked`，其他标签允许写入。

1. 失败归类：登记尚未入库的 B（类型 `audio`，标签依次为 `project`、
   `blocked`），第二个标签被触发器拒绝时：退出码 2、标准输出为空，
   标准错误说明**数据库写入失败**并保留数据库给出的拒绝文本
   `rejected by fixed condition: blocked`，不声称素材已登记，
   也不含调用栈。
2. 失败原子性：失败后由**新进程** `export` 仍只得到 A 的原始完整记录；
   查询 `project` 或 `blocked` 都得到 `[]`；直接读库确认不残留 B 的
   素材记录或已经写入的 `project` 标签；两个源文件内容不变。
3. 恢复成功：在同一样例库撤去拒绝条件（DROP TRIGGER）后原样提交 B 的
   登记：退出码 0、标准错误为空，标准输出是仅含 `path`、`type`、`tags`
   的单条 JSON 记录，标签为 `["project", "blocked"]`；新进程导出按首次
   登记顺序返回 A、B，各一次。
重复执行得到一致结果；任一上述行为不符时 unittest 以非零退出码退出，
并指出具体差异（退出码、输出、错误信息、持久化记录或素材文件内容）。

## 视图库拒绝与空库初始化回归测试（新增）

### 运行入口

在项目根目录（`asset_catalog/` 所在目录）执行（验收入口）：

```sh
python -m unittest tests.test_view_database_rejection_regression -v
```

也可以直接运行测试文件：

```sh
python tests/test_view_database_rejection_regression.py
```

### 测试方式

- 从 README 公开的 `python -m asset_catalog` 入口以**独立子进程**方式调用。
- 每个用例使用 `tempfile` 在项目根目录下创建**独立的临时样例文件与
  SQLite 数据库**，结束后自动清理，不接触、不修改已有目录或素材。
- 固定样例 `foreign.sqlite` 的唯一业务对象由
  `CREATE VIEW source_note AS SELECT 'keep' AS note` 定义，
  样例准备完成后立即关闭连接。
- 只使用 Python 3 标准库（unittest / subprocess / tempfile / sqlite3），
  不依赖网络或第三方包。

### 覆盖内容

1. 固定验收：`python -m asset_catalog --db foreign.sqlite export`
   退出码 2、标准输出为空，标准错误说明该数据库结构不属于本产品并包含
   指定数据库路径、不含调用栈；失败前后文件字节完全一致，
   `SELECT note FROM source_note` 仍返回 `keep`，不留下素材表、标签表、
   部分新记录或 `-journal`/`-wal`/`-shm` 侧车文件；再次调用得到相同错误。
2. `query --tag demo`、源文件与参数均合法的 `add`、参数合法的 `retag`
   在同一视图库上同样确定失败，结论与 `export` 一致（视图数量和名称
   不改变结果）。
3. 含多个视图、且视图名为 `asset` 或 `asset_tag` 的 SQLite 文件也按同一
   规则拒绝；全部原视图定义与查询结果保留。
4. 参数不合法（`query` 缺 `--tag`、`export` 传不支持的位置参数）或
   `add` 的源路径不存在时继续遵循既有参数校验规则，且不初始化、不修改
   视图库。
5. 对照：在已存在的父目录中对尚不存在的 `fresh.sqlite` 使用同一 `export`
   入口，仍创建空目录库，退出码 0、标准错误为空、输出 `[]`；新进程再次
   读取结论一致，之后能正常登记素材并由新进程 `export`/`query` 读回。
6. 已有零字节文件、没有用户业务对象的空 SQLite 库（`sqlite_master`
   完全为空，或仅含内部表 `sqlite_sequence`）保持原来的初始化行为。
7. 父目录不存在仍报错且不补建目录。
8. 含兼容素材表的正常目录即使附带用户视图与触发器，也继续可用：
   登记、查询、导出、`retag` 的完整标签、登记顺序与源文件只读规则均不变。

### 成功结果

成功时输出 `OK`，例如：

```
test_add_rejects_view_only_database ... ok
test_export_rejects_view_only_database ... ok
test_fresh_sqlite_export_initializes_then_register_and_read ... ok
test_empty_sqlite_without_user_objects_is_initialized ... ok
test_invalid_arguments_still_follow_existing_validation ... ok
test_missing_parent_directory_is_not_created ... ok
test_normal_catalog_with_view_and_trigger_remains_usable ... ok
test_query_rejects_view_only_database ... ok
test_repeated_calls_fail_identically_and_keep_bytes ... ok
test_retag_rejects_view_only_database ... ok
test_view_count_and_names_do_not_change_result ... ok
test_zero_byte_file_is_initialized ... ok

----------------------------------------------------------------------
Ran 12 tests in ...s

OK
```

重复执行得到一致结果；任一上述行为不符时 unittest 以非零退出码退出，
并指出具体差异（退出码、输出、错误信息、文件字节或视图查询结果）。

## query 中间组件被替换为普通文件时的文件状态回归测试（新增）

### 运行入口

在项目根目录（`asset_catalog/` 所在目录）执行：

```sh
python -m unittest tests.test_query_nested_path -v
```

也可以直接运行测试文件：

```sh
python tests/test_query_nested_path.py
```

### 测试方式

- 从 README 公开的 `python -m asset_catalog` 入口以**独立子进程**方式调用
  （`add` 仅用于准备样例数据，`export` 用于核对登记元数据，测试对象为
  `query` 的文件状态检查与 `--file-status` 筛选）。
- 每个用例使用 `tempfile` 创建**独立的临时样例文件与 SQLite 数据库**，
  结束后自动清理，不接触、不修改已有目录或素材，不依赖网络或外部素材，
  重复执行不依赖上次残留文件。
- 通过 `json.loads` 比较解析后的内容，不依赖 JSON 空白或对象键顺序。

### 固定场景与预期

固定样例只含两条素材，按 A、B 顺序登记：两者类型均为 `image`，
标签顺序均为 `demo`、`ui`。A 位于临时目录的 `nested/a.bin`，
B 位于同一临时目录的 `b.bin`。登记后移除 A 及其父目录 `nested`，
再在 `nested` 原位置创建一个普通文件（路径中间组件被替换为非目录），
B 保持原样。

1. `query --tag demo --check-files` 按登记顺序返回 A、B 两条记录：
   A 的 `file_status` 为 `missing`（中间组件不是目录按 `missing` 归类，
   不判为 `not_file`，也不构成查询错误），B 为 `present`；
   每条记录的规范绝对路径、类型与完整标签顺序与登记结果一致。
2. `query --tag demo --file-status missing` 只返回 A 的原元数据，
   不附加 `file_status` 字段；追加 `--check-files` 后仍只返回 A，
   并附加 `missing`。
3. 中间组件被替换期间，不带状态选项的 `query --tag demo`
   仍返回原有 A、B 两条记录，且不含 `file_status`。
4. 恢复 `nested` 目录及 `a.bin`（不重新登记）后，由**新进程**执行
   `--file-status missing` 筛选得到 `[]`；带 `--check-files` 的标签查询中
   两条素材均显示 `present`。
5. 以上查询退出码均为 0、标准错误为空、标准输出仅为一个 JSON 数组。
6. 各次查询不改变登记元数据（前后 `export` 结果一致、数据库文件字节不变），
   不改写 B 或替代目录的普通文件内容。

### 成功结果

成功时输出 `OK`，例如：

```
test_check_files_marks_a_missing_b_present ... ok
test_file_status_missing_filter_with_and_without_check_files ... ok
test_plain_tag_query_unchanged_while_nested_replaced ... ok
test_queries_do_not_modify_metadata_or_sample_files ... ok
test_restore_nested_dir_reports_present_in_fresh_process ... ok

----------------------------------------------------------------------
Ran 5 tests in ...s

OK
```

重复执行结论一致；任一预期不符时 unittest 以非零退出码退出，
并打印预期值与实际值的差异。

## retype 类型写入失败回归测试（新增）

### 运行入口

在项目根目录（`asset_catalog/` 所在目录）执行（验收入口）：

```sh
python -m unittest discover -s tests -p test_retype_write_failure_regression.py -v
```

也可以直接运行测试文件：

```sh
python tests/test_retype_write_failure_regression.py
```

### 测试方式

- 从 README 公开的 `python -m asset_catalog` 入口以**独立子进程**方式调用：
  `add` 仅用于准备样例，`query`/`export` 用于核对持久化结果，测试对象为
  `retype` 类型写入被数据库拒绝时的行为。
- 每个用例使用 `tempfile` 创建**独立的临时样例文件与 SQLite 数据库**，
  结束后自动清理，不接触、不修改已有目录或素材，不依赖外部素材、权限差异、
  网络或第三方库（仅用 Python 3 标准库）。
- 通过 `json.loads` 比较解析后的内容，不依赖 JSON 空白或对象键顺序。

### 固定场景与预期

样例只含两个临时演示文件，按 A.bin、B.bin 顺序登记：A 类型 `image`、
标签依次为 `demo`、`ui`；B 类型 `audio`、标签只有 `demo`。

1. 样例库保留原有兼容表结构（`asset`、`asset_tag` 及索引）和正常读取
   功能；仅附加一个 BEFORE UPDATE 触发器作为固定拒绝条件：拒绝把类型
   更新为 `texture`，拒绝文本固定为 `retype blocked`，其他类型与读取
   不受影响。
2. 用 `retype` 对 A 的登记路径提交 `--type texture`：更新被触发器拒绝。
   预期**退出码 2、标准输出为空**，标准错误为单行，说明数据库读写失败
   并包含 `retype blocked`，且**不含调用栈**。
3. 失败后由**新进程**核对：`export` 仍按 A、B 的登记顺序返回原记录，
   规范绝对路径、类型与标签顺序均不变；`query --tag demo --type image`
   只返回 A，改为 `--type texture` 时返回 `[]`；不带类型的 `demo`
   查询仍按登记顺序返回 A、B；两个源文件字节保持不变。
4. 在同一场景撤去拒绝条件（DROP TRIGGER），用**同一输入**再次提交：
   退出码 0、标准错误为空，标准输出仅为含 `path`、`type`、`tags` 的
   A 记录，类型为 `texture`，标签仍为 `["demo", "ui"]`。
5. 由**新进程**导出仍按 A、B 顺序返回，只有 A 的类型变化，B 保持原样；
   按 `demo` 和 `texture` 查询只命中 A，按 `demo` 和 `image` 查询返回
   `[]`；两次操作均不改写两个源文件的字节内容。

### 成功结果

成功时输出 `OK`，例如：

```
test_retype_blocked_keeps_old_record_then_succeeds_when_condition_removed ... ok

----------------------------------------------------------------------
Ran 1 tests in ...s

OK
```

重复执行得到一致结果；任一上述行为不符时 unittest 以非零退出码退出，
并指出具体差异（退出码、输出、错误信息、持久化记录或素材文件内容）。

## export 登记路径变为符号链接时的文件状态专项回归测试（新增）

### 运行入口

在项目根目录（`asset_catalog/` 所在目录）执行（验收入口）：

```sh
python -m unittest tests.test_export_symlink_status_regression -v
```

也可以被现有发现方式统一收集：

```sh
python -m unittest discover -s tests -v
```

或直接运行测试文件：

```sh
python tests/test_export_symlink_status_regression.py
```

### 测试方式

- 从 README 公开的 `python -m asset_catalog` 入口以**独立子进程**方式调用
  （`add` 仅用于准备样例数据，测试对象为 `export --check-files` 对登记路径
  后来变为符号链接时的判定）。
- 每组用例使用 `tempfile` 创建**独立的临时样例文件与 SQLite 数据库**，
  结束后自动清理，不接触、不修改已有目录或真实素材，不访问网络，
  不依赖第三方库（仅用 Python 3 标准库）。
- 通过 `json.loads` 比较解析后的内容，不依赖 JSON 空白或对象键顺序。
- 环境确实不支持创建符号链接时，仅 `skipTest` 跳过相关用例并在跳过原因中
  说明；其他错误（如链接创建遇到非“不支持”类 OSError、任一导出预期不符）
  均使测试失败，不把跳过记为行为验证通过。

### 固定样例与覆盖内容

两组独立样例均按 A、B 顺序登记：A 类型 `image`，标签依次为 `demo`、`ui`；
B 类型 `audio`，标签只有 `music`。

第一组把 A 的原登记路径删除后替换为一个符号链接，链接指向**未登记的普通
文件 T**（T 在创建链接之后才写入）：

1. 链接有效时执行 `export --check-files`：按 A、B 的首次登记顺序各输出一次，
   两者状态均为 `present`；A 的 `path`、`type`、`tags` 保持登记值，
   输出路径仍是原登记路径而非 T 的路径，T 不成为目录记录。
2. 删除 T 形成**断链**后再次导出：A 报告 `missing`，B 仍为 `present`，
   两条记录均保留（断链被成功报告为 missing，不报错、不丢记录）。
3. 在 T 的原目标位置按原内容重建 T（不重新登记）后，由**新启动的导出进程**
   重新报告 A 为 `present`，B 仍为 `present`，元数据与顺序不变。
4. 上述每一种状态（有效链接、断链、重建后）下，不带 `--check-files` 的
   `export` 都仍返回 A、B 两条原元数据记录，且不含 `file_status` 字段。

第二组用独立样例把 A 的原登记路径替换为指向一个**临时目录**的符号链接：

5. `export --check-files` 按链接目标（目录）把 A 判为 `not_file`，
   B 仍为 `present`；结果顺序与每条记录的 path/type/tags 不变；
   不带选项的导出同样保持两条原元数据记录。

通用约定与只读核对：

6. 上述带选项导出均退出码 0、标准错误为空，标准输出只有**一行 JSON 数组**。
7. 每次导出前后核对样例数据库字节、B 与仍存在的 T 的内容以及链接指向
   （`readlink` 字符串）均不变；样例准备中主动删除或重建 T、删除 A 并
   创建链接、创建目录等显式变更除外。导出不会重建被删除的 T，也不会改动
   链接指向的目录内容。

### 成功结果

成功时输出 `OK`，4 个用例全部通过，例如：

```
test_broken_link_reports_missing_then_rebuilt_reports_present ... ok
test_exports_readonly_database_b_and_target_bytes ... ok
test_link_to_directory_reports_not_file ... ok
test_link_to_unregistered_file_present_and_no_t_record ... ok

----------------------------------------------------------------------
Ran 4 tests in ...s

OK
```

若环境不支持创建符号链接，相关用例显示 `skipped` 并给出原因（其余用例仍
正常执行）；重复执行结论一致；任一上述行为不符时 unittest 以非零退出码
退出，并指出具体场景与差异（退出码、标准错误、输出行数、状态、顺序、
元数据、数据库字节、B/T 内容或链接指向）。

## relink 重新关联本地路径回归测试（新增）

### 运行入口

在项目根目录（`asset_catalog/` 所在目录）执行：

```sh
python -m unittest tests.test_relink_regression -v
```

也可以直接运行测试文件：

```sh
python tests/test_relink_regression.py
```

### 测试方式

- 从 README 公开的 `python -m asset_catalog` 入口以**独立子进程**方式调用
  （`add` 仅用于准备样例数据，`show`/`query`/`export` 用于核对持久化结果，
  测试对象为 `relink`）。
- 每个用例使用 `tempfile` 创建**独立的临时样例文件与 SQLite 数据库**，
  结束后自动清理，不接触、不修改已有目录或素材，不依赖网络或外部素材。
- 通过 `json.loads` 比较解析后的内容，不依赖 JSON 空白或对象键顺序。

### 覆盖内容

固定验收样例按 A.old、B.bin 顺序登记：A 类型 `image`，标签依次为 `demo`、
`ui`；B 类型 `audio`，标签只有 `demo`。准备样例时把 A.old 移到 A.new。

1. 验收场景：`relink A.old A.new` 退出码 0、标准错误为空，标准输出仅一行
   JSON 对象，含 A.new 的规范绝对路径、原类型 `image` 与完整标签
   `["demo", "ui"]`；随后 `show A.new` 返回 A 的完整记录，
   `query --tag demo` 仍依次返回 A、B，`export` 顺序不变，只有 A 的路径
   变化，`show A.old` 报告未登记（错误含规范路径）。
2. 原路径按 show 的规则解析（相对路径、含 `.` 的等价写法定位同一记录）；
   原文件已删除或原位置变成目录也允许关联；新路径为符号链接时保存目标的
   规范绝对路径。
3. 操作只更新目录中的路径：不移动、复制、删除或改写文件，样例文件内容
   保持不变；类型、标签及顺序、首次登记顺序与 B 的记录不变，新进程读取
   结果一致。
4. 新旧规范路径相同且目标仍为普通文件时不写库，成功返回原记录。
5. 原路径未登记、新文件不存在或不是普通文件（目录）、目标已登记
   （不合并或覆盖）、目标为目录数据库（含已有空文件不因此被初始化）：
   退出码 2、标准输出为空、标准错误说明原因并含相关规范路径、不含调用栈。
6. 路径缺失或为空、缺少 `--db`、数据库路径为空、不支持的参数：退出码 2，
   不创建数据库；数据库不存在且父目录存在时初始化空库后报告原路径未登记，
   父目录缺失时不补建目录。
7. 数据库路径指向目录、非 SQLite 文件、含其他业务表的 SQLite 文件：
   均被拒绝，文件字节前后完全一致；写入被触发器拒绝时整体回滚，
   原记录完整保留，撤去拒绝条件后同一输入成功。

### 成功结果

成功时输出 `OK`，例如：

```
test_acceptance_move_then_relink ... ok
test_argument_errors_do_not_create_database ... ok
test_database_open_and_schema_errors_rejected ... ok
test_deleted_or_directory_old_location_still_relinkable ... ok
test_equivalent_old_path_spellings_resolve_same_record ... ok
test_fresh_database_created_then_unregistered_reported ... ok
test_missing_or_empty_path_arguments_rejected ... ok
test_missing_parent_directory_is_not_created ... ok
test_new_file_missing_or_not_regular_rejected ... ok
test_new_path_equal_to_database_rejected ... ok
test_new_path_equal_to_empty_db_file_not_initialized ... ok
test_new_path_registered_to_another_record_rejected ... ok
test_new_path_symlink_stores_target_canonical_path ... ok
test_relink_does_not_touch_files ... ok
test_relink_persists_across_processes_and_other_commands ... ok
test_same_canonical_path_returns_original_record ... ok
test_unregistered_old_path_rejected_with_canonical_path ... ok
test_write_failure_rolls_back_then_recovers ... ok

----------------------------------------------------------------------
Ran 18 tests in ...s

OK
```

重复执行结论一致；任一预期不符时 unittest 以非零退出码退出，
并指出对应场景。
