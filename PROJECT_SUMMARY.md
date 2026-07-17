# 项目概览

> 最后更新: 2026-07-17 | 版本: v0.3.1 | 状态: 🟢 开发中

---

## 基本信息

| 字段 | 值 |
|------|-----|
| 项目名称 | 智能客服 Agent (agent_customer) |
| 远程仓库 | git@github.com:91TangZhu/agent_customer.git |
| 创建日期 | 2026-07-14 |
| Python 版本 | 3.10 |
| 虚拟环境 | Conda (agent_customer) 路径: `C:\Users\Administrator\.conda\envs\agent_customer` |
| 数据库 | SQLite → `data/customer.db` |
| 本地路径 | `d:\vscode_demo\agent_customer` |

---

## 技术栈

| 层级 | 技术 | 版本 |
|------|------|------|
| Web 框架 | FastAPI + Uvicorn | 0.139+ |
| AI 框架 | LangChain + LangGraph (ReAct) | 1.3.13 / 1.2.9 / 1.0.0+ |
| LLM 模型 | DeepSeek | deepseek-v4-flash (推荐) / deepseek-v4-pro |
| 数据库 | SQLite 3 | - |
| 向量数据库 | ChromaDB (嵌入式) | 0.5+ |
| 嵌入模型 | sentence-transformers (text2vec-base-chinese) | 3.0+ |
| 用户认证 | passlib[bcrypt] + python-jose (JWT) | 1.7+ / 3.3+ |
| API 限流 | slowapi | 0.1+ |
| 压测引擎 | httpx | 0.27+ |
| 配置管理 | python-dotenv | 1.2+ |
| 数据校验 | Pydantic | 2.13+ |

---

## 环境变量 (.env)

| 变量名 | 说明 | 示例值 |
|--------|------|--------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | `sk-xxx...` |
| `DEEPSEEK_BASE_URL` | API 地址 | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 模型名 | `deepseek-v4-flash` |
| `DATABASE_PATH` | 数据库路径 | `data/customer.db` |
| `JWT_SECRET_KEY` | JWT 签名密钥 | (生产环境务必修改) |
| `JWT_EXPIRE_HOURS` | Token 过期小时数 | `24` |
| `EMBEDDING_MODEL` | 嵌入模型本地路径 | `data/models/text2vec-base-chinese` |
| `CHROMA_DB_PATH` | ChromaDB 持久化目录 | `data/chroma_db` |
| `VECTOR_SEARCH_K` | 向量检索返回数量 | `5` |
| `RATE_LIMIT_CHAT_PER_USER` | /chat 每用户限流 | `30/minute` |
| `RATE_LIMIT_CHAT_PER_IP` | /chat 每 IP 限流 | `60/minute` |
| `GITHUB_TOKEN` | GitHub API Token（更新仓库描述等） | `ghp_xxx...` |

> 模板文件: `.env.example`  |  密钥获取: https://platform.deepseek.com

---

## 关键约束

- ⚠️ `.env` 不能提交到 Git（已在 .gitignore）
- ⚠️ 模型只能用 `deepseek-v4-flash` 或 `deepseek-v4-pro`，老模型已下线
- ⚠️ SSH 连接 GitHub 必须走 443 端口（22 端口被墙）
- ⚠️ fastapi/uvicorn 需手动安装（不在 requirements.txt 首次安装范围）
- ⚠️ HuggingFace 模型下载使用 hf-mirror.com 国内镜像（见 config/settings.py）
- ⚠️ ChromaDB 写入操作受 threading.Lock 保护（SQLite 底层非线程安全）
- ⚠️ 嵌入模型 (text2vec-base-chinese) 需预下载至 `data/models/` 目录
- ⚠️ 修改 `app/main.py` 内嵌 JS 后必须 `node --check` 验证语法（见 CLAUDE_PROGRESS.md 防御性检查清单）
- ⚠️ 流式对话依赖 `langgraph>=1.0.0`，国内安装需清华镜像源

---

## 项目结构

```
agent_customer/
├── app/                        # 应用核心代码
│   ├── __init__.py
│   ├── main.py                 # FastAPI 入口 + 多页面 HTML + 22 个 API（含流式对话 + 压测）
│   ├── agent.py                # LangChain ReAct Agent (RAG 增强 + 重试)
│   ├── tools.py                # 6 个 Agent 工具函数 (含 RAG)
│   ├── models.py               # Pydantic 请求/响应模型 (22 个类)
│   ├── database.py             # SQLite 查询封装 (6 张表 CRUD + JOIN 查询)
│   ├── auth.py                 # JWT + bcrypt 认证 + 依赖注入
│   ├── logger.py               # 结构化日志 (api/rag/llm/auth)
│   ├── middleware.py           # 全局异常 + 请求计时 + 限流
│   ├── rag.py                  # ChromaDB 向量存储 + 嵌入 + 检索 + 索引重建
│   ├── benchmark.py              # 服务端压测引擎（异步并发 + 延迟分位数统计）
│   └── kb_seed_data.py         # 27 条服装知识库种子数据 (含 8 篇 SKU)
├── config/
│   ├── __init__.py
│   └── settings.py             # .env → Python 配置单例 (含 JWT/RAG)
├── data/
│   ├── .gitkeep
│   ├── customer.db             # SQLite 数据库 (不入 Git)
│   ├── chroma_db/              # ChromaDB 向量持久化 (不入 Git)
│   └── models/                 # 嵌入模型文件 (不入 Git)
├── logs/                       # 日志输出 (不入 Git)
├── .claude/
│   ├── agents/
│   │   └── record-keeper.md      # 记录备份工程师 Agent
│   └── skills/
│       ├── git-push/
│       │   └── SKILL.md          # Git 一键推送 Skill
│       ├── project-init/
│       │   └── SKILL.md          # 上下文恢复 Skill
│       └── project-update/
│           └── SKILL.md          # 记忆文档更新 Skill
├── .env                        # 真实密钥 (不入 Git)
├── .env.example                # 配置模板
├── .gitignore
├── requirements.txt            # pip 依赖清单 (17 个包，含 langgraph + httpx)
├── db_init.py                  # 数据库初始化脚本 (6 张表 + 服装种子数据)
├── README.md                   # 项目说明
├── PROJECT_SUMMARY.md          # 本文件 — 项目档案
├── CLAUDE_PROGRESS.md          # 开发进度追踪
├── INTERVIEW_PREP.md           # 面试准备文档
└── ISSUES_LOG.md               # 问题记录与修复日志
```

---

## 数据库

| 表名 | 字段 | 测试数据 |
|------|------|----------|
| `users` | id, username(UNIQUE), password_hash, phone, is_admin, created_at | 6 条 (admin/testuser/lihua/wangfang/zhangwei/chenjing) |
| `orders` | id, user_id(FK→users), order_no, product_name, amount, status, created_at | 8 条 (服装商品) |
| `logistics` | id, order_id(FK), tracking_no, carrier, status, updates, created_at | 5 条 |
| `documents` | id, title, content, category, gender, created_at, updated_at | 27 条 (种子数据) |
| `chat_sessions` | id, user_id(FK→users), title, created_at | 按需创建 |
| `chat_messages` | id, session_id(FK), role, content, citations(JSON), created_at | 按需创建 |

状态枚举: 待付款 / 已发货 / 已完成 / 已退款
用户角色: is_admin=1 (管理员) / is_admin=0 (普通用户)
知识库分类: 产品信息(含8篇SKU) / 尺码指南 / 售后政策 / 面料知识 / 品牌故事（支持自定义）
知识库性别: 男 / 女 / 通用 / 儿童

> v0.3.0 重构说明: users 与 auth_users 合并为统一 users 表，去 email 字段，订单查询通过 JOIN 返回 username+phone（规范化设计，订单表不冗余用户信息）

---

## 核心功能

1. **多页面 Web 前端**: 登录页 / 智能客服聊天 / 管理后台（三标签：用户管理+订单管理+文档管理）→ http://localhost:8000
2. **流式对话响应**: SSE/NDJSON 流式推送，逐 token 渲染，首字节延迟降低 78%（从 9.3s 降至 2.1s）
3. **Agent 工具调用**: 查用户、查订单、查物流、向量检索知识库、尺码推荐（共 6 个工具）
4. **RAG 知识问答**: ChromaDB 向量检索 + 27 条服装知识库（含 8 篇具体 SKU 参数文档）+ 引用来源展示
5. **用户认证**: 注册/登录/JWT 令牌/权限校验（admin/user 角色，统一 users 表管理）
6. **会话管理**: 多轮对话历史存储与恢复 + 删除会话（级联删除消息）
7. **管理后台**: 三标签界面 — 用户管理（列表查看）/ 订单管理（列表+详情，JOIN 返回用户信息）/ 文档管理（CRUD + 分类管理 + 性别过滤 + 10 条/页分页 + 索引重建）
8. **压测仪表盘**: 服务端并发压测引擎（httpx），支持逐 token 流式展示，P50/P75/P90/P95/P99 延迟分析
9. **企业级基础设施**: 结构化日志 + 全局异常 + API 限流 + 请求计时 + 启动时数据库自动迁移（检测旧 schema 自动删库重建）

---

## 启动命令

```bash
conda activate agent_customer

# 首次运行：安装依赖 + 初始化数据库 + 下载嵌入模型
pip install -r requirements.txt
# 启动服务（每次开机只需这两步）
conda activate agent_customer
uvicorn app.main:app --reload
# 浏览器打开 http://localhost:8000
# 默认管理员账号: admin / 123456
```

---

## Skills 清单

| Skill | 用途 | 调用方式 |
|-------|------|----------|
| `git-push` | 一键推送 + 同步远程仓库描述 | `/git-push` |
| `project-init` | 新会话启动，读取记忆恢复上下文 | `/project-init` |
| `project-update` | 完成修改后更新记忆文档 | `/project-update` |

## Agents 清单

| Agent | 用途 | 调用方式 |
|-------|------|----------|
| `record-keeper` | 记录备份工程师 — 一键完成项目记忆更新 + Git 提交推送 | 通过 Agent 工具调用 |
