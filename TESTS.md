# query 标签查询回归测试运行说明

本组测试为 `query`（按标签精确查询）补充可独立执行的回归测试，仅依赖 Python 3 标准库，
无需安装任何第三方包，也无需联网。

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
