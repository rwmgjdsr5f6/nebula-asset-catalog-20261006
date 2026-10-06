# 本地数字素材目录

管理数字素材的目录与标签信息。面向本地单机使用，采用 Python 3 标准库与 SQLite，无需安装第三方依赖。

当前实现两个操作：素材登记（`add`）与按标签精确查询（`query`）。使用过程中源文件保持只读，不扫描其他目录，也不解码素材内容。

## 用法

全局必填选项 `--db` 指定 SQLite 数据库文件；文件不存在时自动创建（不自动创建缺失的父目录）。

```sh
# 登记素材：一个文件路径、必填 --type、至少一个可重复的 --tag
python -m asset_catalog --db catalog.sqlite add demo.png --type image --tag demo

# 成功输出（退出码 0）：
# {"path": "/abs/path/demo.png", "type": "image", "tags": ["demo"]}

# 按完整标签查询（区分大小写，不做子串匹配）
python -m asset_catalog --db catalog.sqlite query --tag demo
# [{"path": "/abs/path/demo.png", "type": "image", "tags": ["demo"]}]
# 无结果时输出 []

# 查询时可选再按素材类型筛选：去除首尾空白后与登记类型完整匹配（区分大小写）
python -m asset_catalog --db catalog.sqlite query --tag demo --type image
# 不传 --type 时查询范围、输出与排序均与只按标签查询一致

# 按需检查匹配记录的文件状态：追加不接收值的 --check-files 开关
python -m asset_catalog --db catalog.sqlite query --tag demo --type image --check-files
# [{"path": "/abs/path/demo.png", "type": "image", "tags": ["demo"], "file_status": "present"}]
# 不改变筛选范围、首次登记顺序与完整标签顺序；无匹配时仍输出 []
```

## 文件状态（`--check-files`）

传入 `--check-files` 后，每条匹配结果仅追加字符串字段 `file_status`；不传时输出与原来完全一致。状态以数据库保存的路径为准，只检查满足标签及可选类型条件的记录，不搜索其他目录，也不读取素材内容：

- `present`：路径当前指向普通文件。文件事后被替换为符号链接时，按链接指向的对象判断；内容即使已变仍是 `present`。
- `missing`：路径或中间目录不存在，或符号链接已断开。输出的 `path` 仍保留登记值，缺失文件随匹配记录一并返回。
- `not_file`：路径存在但对应的是目录或其他非普通文件（如 FIFO、设备文件）。

状态只随本次查询输出返回，不写入数据库。权限不足或其他系统错误导致状态无法确定时：退出码 2、标准输出为空、标准错误说明失败路径与原因（不含调用栈），不会输出部分结果，也不会把错误伪装成 `missing`。

## 规则

- 文件路径会解析为规范绝对路径（相对路径、`.`、`..` 及符号链接均会解析），同一规范路径重复登记将被拒绝。
- 类型与标签去除首尾空白后保存；标签按首次出现顺序去重，大小写保留。
- 查询结果按首次登记顺序排列；源文件事后被删除不影响已保存的记录。
- 查询可通过 `--type` 在标签结果内再按类型完整匹配筛选；非空类型无匹配时输出 `[]`，`--type` 缺失值或去空白后为空按参数错误处理（退出码 2）。
- `--check-files` 仅在传入时生效：为匹配结果追加 `file_status`（present/missing/not_file），不改变筛选与排序、不写库；状态无法确定时整次命令按退出码 2 失败，不输出部分结果。
- 已存在但损坏或不属于本产品结构的数据库会报错，不会被覆盖或重建。
- 参数缺失、类型/标签为空、路径不存在或不是普通文件、重复登记、数据库无法打开或写入时：退出码 2，标准输出为空，标准错误给出原因，不输出调用栈。
