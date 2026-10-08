# MiniShop

基于 FastAPI + SQLAlchemy（异步）+ asyncmy 构建的迷你商城后端服务。

## 环境要求

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/)（依赖管理）
- Docker & Docker Compose（运行 MySQL）

## 本地调试

### 1. 克隆代码并进入目录

```bash
git clone <your-repo-url> minishop
cd minishop
```

### 2. 安装依赖

使用 `uv` 同步依赖（会自动创建并管理 `.venv` 虚拟环境）：

```bash
uv sync
```

### 3. 启动 MySQL

通过 `docker-compose` 在本地启动 MySQL 8.0：

```bash
docker compose up -d mysql
```

可使用以下命令查看 MySQL 健康状态：

```bash
docker compose ps
```

### 4. 配置环境变量

在项目根目录创建 `.env` 文件，参考以下内容（与 `docker-compose.yml` 中 MySQL 默认账号一致）：

```env
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=minishop
MYSQL_PASSWORD=minishop123456
MYSQL_DATABASE=minishop
```

> 如果你修改了 `docker-compose.yml` 中的密码，请同步修改此处。

### 5. 启动开发服务

进入 `app/` 目录并以热重载模式启动 FastAPI：

```bash
cd app
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

或者在项目根目录启动：

```bash
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

启动成功后可访问：

- API 根路径：<http://127.0.0.1:8000/>
- Swagger 文档：<http://127.0.0.1:8000/docs>
- ReDoc 文档：<http://127.0.0.1:8000/redoc>

### 6. 测试数据库连接（可选）

```bash
uv run python -m app.scripts.test_db
```

执行成功后输出 `1`，表示数据库连接正常。

## 运行测试

集成测试跑在**真实 MySQL** 上（`pytest + httpx ASGITransport + asyncmy`），不依赖任何 mock。

### 1. 准备测试库

测试库与开发库分离，需要先创建（首次执行一次即可）：

```bash
docker compose exec mysql mysql -uroot -proot123456 -e "CREATE DATABASE IF NOT EXISTS minishop_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
```

### 2. 配置测试库连接串

项目根目录的 `.env.test`（与 `.env` 相互独立）：

```env
TEST_DATABASE_URL=mysql+asyncmy://minishop:minishop123456@127.0.0.1:3306/minishop_test
```

> 库名必须以 `_test` 结尾：测试会话开始会 `drop_all` + `create_all` 重建表，结尾再次 `drop_all`，
> 这是防止误删开发库的硬性保护，不满足时直接报错退出。

### 3. 执行

```bash
uv run pytest              # 全部用例
uv run pytest -v           # 显示每个用例名
uv run pytest tests/test_products.py::TestCreateProduct   # 只跑某个类
```

隔离机制：每个用例在一条独立的外层事务中执行，业务层的 `commit()` 只释放 `SAVEPOINT`，
用例结束回滚整条事务，因此用例之间互不污染、也不会有数据落库。

## 常用命令速查

| 操作 | 命令 |
| --- | --- |
| 安装 / 同步依赖 | `uv sync` |
| 启动 MySQL | `docker compose up -d mysql` |
| 停止 MySQL | `docker compose down` |
| 启动 API（开发模式） | `uv run uvicorn app.main:app --reload` |
| 运行测试 | `uv run pytest` |
| 运行脚本 | `uv run python -m app.scripts.<script_name>` |

## 目录结构

```
minishop/
├── app/
│   ├── api/         # 路由层
│   ├── core/        # 配置（settings 等）
│   ├── db/          # 数据库连接与会话
│   ├── models/      # ORM 模型
│   ├── scripts/     # 运维/调试脚本
│   └── main.py      # FastAPI 入口
├── tests/           # pytest 集成测试（真实 MySQL）
├── docker-compose.yml
├── pyproject.toml
├── uv.lock
└── README.md
```