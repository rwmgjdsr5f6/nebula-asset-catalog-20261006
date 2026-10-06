# 回归测试运行说明

本仓库的回归测试仅依赖 Python 3 标准库，无需安装任何第三方包，也无需联网。
现有以下可独立执行的测试：

- `tests/test_query_regression.py`：`query`（按标签精确查询）回归测试；
- `tests/test_add_dedup_regression.py`：`add` 同一规范路径拒绝重复登记的回归测试；
- `tests/test_add_reject_db_regression.py`：`add` 拒绝损坏或不兼容数据库的回归测试；
- `tests/test_add_db_as_asset_regression.py`：`add` 拒绝把目录数据库自身登记为素材的回归测试；
- `tests/test_query_multi_tag_regression.py`：`query` 多标签（重复 `--tag`）交集查询的回归测试。
- `tests/test_export_regression.py`：`export` 完整目录导出（顺序、字段、文件状态无关、空库与各类错误）的回归测试。

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
