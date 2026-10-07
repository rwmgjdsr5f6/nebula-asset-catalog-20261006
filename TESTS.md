# 回归测试运行说明

本仓库的回归测试仅依赖 Python 3 标准库，无需安装任何第三方包，也无需联网。
现有以下可独立执行的测试：

- `tests/test_query_regression.py`：`query`（按标签精确查询）回归测试；
- `tests/test_add_dedup_regression.py`：`add` 同一规范路径拒绝重复登记的回归测试；
- `tests/test_add_reject_db_regression.py`：`add` 拒绝损坏或不兼容数据库的回归测试；
- `tests/test_add_db_as_asset_regression.py`：`add` 拒绝把目录数据库自身登记为素材的回归测试；
- `tests/test_query_multi_tag_regression.py`：`query` 多标签（重复 `--tag`）交集查询的回归测试。
- `tests/test_query_tag_mode_any_regression.py`：`query --tag-mode any` 任选标签查询（含默认/`all` 交集不变、与 `--type`、`--check-files` 组合及参数错误）的回归测试。
- `tests/test_export_regression.py`：`export` 完整目录导出（顺序、字段、文件状态无关、空库与各类错误）的回归测试。
- `tests/test_retag_regression.py`：`retag` 标签替换（规范化、持久化、源文件状态无关、各类错误）的回归测试。
- `tests/test_retag_atomicity.py`：`retag` 写入中途失败的原子性专项回归测试（旧标签进入替换流程后失败仍保留既有记录、不留部分新标签）。
- `tests/test_add_write_failure_regression.py`：`add` 写入被数据库约束或触发器拒绝时的失败原因归类回归测试（未登记路径报数据库写入失败而非已登记、整体回滚、撤去条件后恢复成功、真实重复仍拒绝）。

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
   （`--tag`、位置参数、`--check-files`）、数据库路径指向目录：
   退出码 2、标准输出为空、标准错误说明原因且不含调用栈。
5. 非 SQLite 文件、含其他业务表的 SQLite 文件（固定记录保留）、
   `asset` 缺少 `type` 列的不兼容结构：均被拒绝，文件字节前后完全一致，
   不被覆盖、补表或重建。
6. 正常数据库截断为损坏镜像时导出：退出码 2、标准输出为空，
   不输出任何部分记录。

### 成功结果

成功时输出 `OK`，例如：

```
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
Ran 11 tests in ...s

OK
```

重复执行结论一致；任一预期不符时 unittest 以非零退出码退出，
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
4. 真实重复：恢复成功后以绝对路径、含 `.` / `..` 的等价写法再次登记 B，
   仍按既有重复规则拒绝（退出码 2、标准输出为空、标准错误说明重复登记
   并含冲突的规范绝对路径，无调用栈），原记录不被替换。

### 成功结果

成功时输出 `OK`，例如：

```
test_add_succeeds_after_condition_removed ... ok
test_trigger_rejection_reported_as_write_failure_and_rolls_back ... ok
test_true_duplicate_still_rejected_and_record_not_replaced ... ok

----------------------------------------------------------------------
Ran 3 tests in ...s

OK
```

重复执行得到一致结果；任一上述行为不符时 unittest 以非零退出码退出，
并指出具体差异（退出码、输出、错误信息、持久化记录或素材文件内容）。
