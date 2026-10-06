# 回归测试运行说明

本仓库的回归测试仅依赖 Python 3 标准库，无需安装任何第三方包，也无需联网。
现有三组可独立执行的测试：

- `tests/test_query_regression.py`：`query`（按标签精确查询）回归测试；
- `tests/test_add_dedup_regression.py`：`add` 同一规范路径拒绝重复登记的回归测试；
- `tests/test_add_reject_db_regression.py`：`add` 拒绝损坏或不兼容数据库的回归测试。

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
