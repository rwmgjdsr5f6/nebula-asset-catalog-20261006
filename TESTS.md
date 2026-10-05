# 回归测试运行说明

本仓库的回归测试仅依赖 Python 3 标准库，无需安装任何第三方包，也无需联网：

- `tests/test_query_regression.py`：`query`（按标签精确查询）回归测试；
- `tests/test_add_dedup_regression.py`：`add` 规范路径去重规则回归测试。

# add 路径去重回归测试

本组测试针对 `add` 的“同一规范路径拒绝重复登记”规则，只验证已有行为，
不改动命令行参数、退出码、JSON 输出结构与 SQLite 数据格式。

## 运行入口

在项目根目录（`asset_catalog/` 所在目录）执行：

```sh
python -m unittest tests.test_add_dedup_regression -v
```

也可以直接运行测试文件：

```sh
python tests/test_add_dedup_regression.py
```

## 测试方式

- 从 README 公开的 `python -m asset_catalog` 入口以**独立子进程**方式调用，
  登记与查询分别由不同进程完成，验证记录经 SQLite 持久化后可被新进程读取。
- 每个用例使用 `tempfile` 创建**独立的临时样例文件与 SQLite 数据库**，
  结束后自动清理，不接触、不修改已有目录或素材；样例为普通文本文件，
  不依赖素材解码。
- 通过 `json.loads` 比较解析后的内容，不依赖 JSON 空白或对象键顺序；
  断言失败时 unittest 打印预期与实际的差异并以非零退出码退出。

覆盖内容：

1. 以**相对路径**首次登记样例 A（类型 `image`，标签依次为 `demo`、`ui`）：
   退出码 0、标准错误为空，标准输出是包含规范绝对路径、原类型和
   完整标签顺序的单个 JSON 对象；新进程查询 `demo`/`ui` 均只返回该记录。
2. 分别用**首次登记的路径写法**、**同一文件的绝对路径**、**含 `.` 与 `..`
   的等价路径**再次登记 A，每次传入不同的类型 `audio` 与标签 `replacement`：
   退出码 2、标准输出为空、标准错误说明重复登记且不含调用栈；
   每次失败后查询 `demo` 仍只得到 A 的原始记录（未被覆盖/合并/更新），
   查询 `replacement` 得到空数组，成功查询退出码均为 0 且标准错误为空。
3. 对照样例 B 与 A **内容相同但规范路径不同**，以同样的类型与标签登记成功；
   新进程查询 `demo` 返回 A、B 各一次并保持首次登记顺序，
   证明去重依据是规范路径而非文件内容。
4. 每个用例结束时校验两个样例文件的内容与登记前一致。

## 成功结果

成功时输出 `OK`，例如：

```
test_duplicate_registrations_rejected_and_record_unchanged ... ok
test_first_registration_with_relative_path ... ok
test_same_content_different_path_registered_separately ... ok

----------------------------------------------------------------------
Ran 3 tests in ...s

OK
```

重复执行结论一致；任一断言失败时 unittest 以非零退出码退出并打印差异。

# query 标签查询回归测试

## 运行入口

在项目根目录（`asset_catalog/` 所在目录）执行：

```sh
python -m unittest tests.test_query_regression -v
```

也可以直接运行测试文件：

```sh
python tests/test_query_regression.py
```

## 测试方式

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

## 成功结果

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
