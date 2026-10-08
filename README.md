# AIDB · 清水混凝土数据库工作台

单文件 HTML 数据库客户端：连接、浏览、增删改查、SQL、导出。本地 SQLite 离线可用（内嵌 WASM 引擎），配合 `server.py` 连接**远程 MySQL / PostgreSQL / SQL Server**——这是它的主要用法。

## 在线版（GitHub Pages）

仓库可直接部署为静态站点（`.nojekyll` 已内置）：

- **本地 SQLite 全部功能可用**：打开/拖入 .db 文件、浏览、编辑、SQL、导出；「加载示例数据库」按钮一键体验
- **连远程库**：Pages 是纯静态的，桥接需在自己电脑上跑 `python server.py`；HTTPS 页面访问本机 `http://127.0.0.1:8788` 属于回环地址豁免，Chrome/Edge 均允许（数据源→服务地址填 `http://127.0.0.1:8788`）
- 也可以直接把整个目录当静态站部署到任何静态服务器

## 快速开始（远程数据库）

```bash
pip install pymysql          # 连 MySQL 用
pip install psycopg2-binary  # 连 PostgreSQL 用
pip install pymssql          # 连 SQL Server 用
python server.py             # 监听 http://127.0.0.1:8788
```

浏览器打开 <http://127.0.0.1:8788>，左侧「服务器连接」：

1. 类型选 MySQL / PostgreSQL / SQL Server，填主机、端口、用户、密码、库名 → **连接**
2. 左侧选出表：分页浏览、点列头排序、WHERE 框输条件回车过滤
3. **增**：`＋ 行` 填表单插入；**删**：点行选中后 `－ 行`；**改**：双击单元格，回车提交；**查**：网格 + SQL 控制台
4. 服务器模式的所有改动**直接写入数据库**（提交即生效）
5. 连接信息（除密码外）会记住，下次打开自动回填

也可以双击 `index.html` 离线使用本地 SQLite（打开 .db 文件编辑后 Ctrl+S 导出）。

## 表格编辑的行定位规则（远程模式）

| 表 | 定位方式 |
|---|---|
| 有主键（单列/复合） | `WHERE pk = ?` / `WHERE pk1=? AND pk2=?` |
| 无主键 | 整行 NULL 安全匹配（MySQL `<=>`、PG `IS NOT DISTINCT FROM`、SQL Server `INTERSECT`），只命中第一行（MySQL `LIMIT 1` / PG `ctid` 子查询 / SQL Server `TOP (1)`） |
| 视图 | 只读，用 SQL 控制台改 |

单元格编辑：留空 = NULL，纯数字自动按数字写入；回车提交，Esc 取消。无主键表编辑时表名旁会标注「按整行匹配」。

## 功能

| 功能 | 本地模式 | 服务器模式 |
|---|---|---|
| MySQL / PostgreSQL / SQL Server | — | ✓ |
| SQLite | 本地 .db 文件 | 服务器上的文件 |
| 分页 / 排序 / WHERE 过滤 | ✓ | ✓ |
| 插入 / 删除 / 双击改单元格 | ✓（内存） | ✓（直接写库） |
| SQL 控制台（Ctrl+Enter，历史） | ✓ | ✓ |
| 结构视图（列信息 + DDL） | ✓ | 仅列信息 |
| 导出 .db / CSV | ✓ | CSV ✓ |

## 安全提示

- SQL 控制台默认开启**危险操作确认**：`DROP DATABASE / TRUNCATE / DROP TABLE / 无 WHERE 的 DELETE·UPDATE` 等语句执行前会弹确认框，最危险的一类需手动输入 `YES` 才放行（开关在 SQL 栏，状态会记住）
- 删除单行有独立确认弹窗；服务器模式的删除/修改会直接写库，不可撤销
- `server.py` 只监听 `127.0.0.1`；如需从别的机器开页面，建议用 SSH 隧道：`ssh -L 8788:127.0.0.1:8788 user@db-host`，页面照常填 `http://127.0.0.1:8788`
- 服务器模式会对数据库执行页面生成的任意 SQL，请只连开发库或只读账号
- 密码只在表单里，不落盘、不上传
