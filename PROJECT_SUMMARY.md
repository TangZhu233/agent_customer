# 项目概览

> 最后更新: 2026-07-23 | 版本: v0.4.1 | 状态: 🟢 开发中

---

## 基本信息

| 字段 | 值 |
|------|-----|
| 项目名称 | 智能客服 Agent (agent_customer) |
| 远程仓库 | git@github.com:TangZhu233/agent_customer.git |
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
| 多路召回 | BM25 (rank-bm25 + jieba) + RRF 融合 | — |
| 语义缓存 | Redis (aioredis) | 5.0+ (Windows) |
| 熔断器 | 三态状态机 (CircuitBreaker) | — |
| 用户认证 | passlib[bcrypt] + python-jose (JWT) | 1.7+ / 3.3+ |
| API 限流 | slowapi | 0.1+ |
| 压测引擎 | httpx | 0.27+ |
| 配置管理 | python-dotenv | 1.2+ |
| 数据校验 | Pydantic | 2.13+ |

---

## 架构决策记录 (ADR)

> 这些是从"能跑"到"能落地"的关键设计决策，面试时重点讲。

### ADR-1: 多路召回 (hybrid = dense + sparse)

- **问题**: 纯向量检索对短货号（`T-SU-WH-L`）几乎无效，向量模型未见过这类字符串
- **方案**: ChromaDB 稠密检索 + BM25/jieba 稀疏检索 → RRF 融合
- **为什么不加权融合**: 两路分数尺度不同，RRF 只看排名天然免疫
- **代价**: 每次启动构建 BM25 索引（~10ms），每次检索 +20~50ms
- **开关**: `RETRIEVAL_MODE=hybrid|dense`

### ADR-2: 语义缓存放 RAG 层而非 Agent 层

- **事故**: v0.4.0 初版缓存 LLM 最终回复 → 跨用户上下文泄露（A 的对话内容出现在 B 的回复里）
- **根因**: ReAct Agent 回复依赖对话历史、用户身份，不是纯函数
- **方案**: 缓存下沉到 RAG 检索层——检索结果确定性强、用户无关
- **键设计**: `MD5(normalized_query|category|gender|k|mode)`，含 query 归一化（去空格+中文标点）
- **原则**: 缓存永远是旁路（try/except 包裹），Redis 挂了不影响服务

### ADR-3: 重排序默认关闭

- **理由**: 28 篇知识库，RRF 融合后候选 5~8 篇，LLM 重排序收益极微
- **代价**: 每次 +1 次 LLM 调用（+500ms~1s + token 费用）
- **何时开**: 知识库上千篇、同类文档混杂时
- **工程原则**: 知道一个技术"能做什么"和知道它"现在该不该用"是两回事

### ADR-4: 熔断器包裹 tenacity 重试而非替代

- tenacity (3次, 2s→4s→8s) 处理偶发网络抖动
- 熔断器 (连续 5 次 RetryError → OPEN 30s) 处理持续性故障
- 关键: 3 次全失败才算熔断器 1 次失败

---

## 环境变量 (.env)

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | — |
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
| `RETRIEVAL_MODE` | 检索模式: dense/hybrid | `hybrid` |
| `RERANK_ENABLED` | 是否启用 LLM 重排序 | `false` |
| `MULTI_RECALL_K` | 多路召回每路拉取数 | `10` |
| `FUSION_K` | RRF 平滑常数 | `60` |
| `RERANK_TOP_K` | 重排序截断数 | `5` |
| `REDIS_ENABLED` | 是否启用 Redis 语义缓存 | `true` |
| `REDIS_URL` | Redis 连接地址 | `redis://localhost:6379/0` |
| `REDIS_CACHE_TTL` | 缓存过期时间（秒） | `3600` |
| `CB_ENABLED` | 是否启用熔断器 | `true` |
| `CB_FAILURE_THRESHOLD` | 熔断触发连续失败次数 | `5` |
| `CB_RECOVERY_TIMEOUT` | 熔断恢复等待（秒） | `30` |
| `CB_HALF_OPEN_MAX` | 半开状态最大请求数 | `1` |
| `LLM_REQUEST_TIMEOUT` | LLM 调用超时（秒） | `30.0` |
| `MAX_HISTORY_MESSAGES` | 对话历史消息数上限 | `20` |
| `MAX_HISTORY_CHAR_LIMIT` | 对话历史字符数安全上限 | `80000` |
| `GITHUB_TOKEN` | GitHub API Token（更新仓库描述等） | — |

> 模板文件: `.env.example`  |  密钥获取: https://platform.deepseek.com

---

## 关键约束

- ⚠️ `.env` 不能提交到 Git（已在 .gitignore）
- ⚠️ 模型只能用 `deepseek-v4-flash` 或 `deepseek-v4-pro`
- ⚠️ SSH 连接 GitHub 必须走 443 端口（22 端口被墙）
- ⚠️ fastapi/uvicorn 需手动安装（不在 requirements.txt 首次安装范围）
- ⚠️ HuggingFace 模型下载使用 hf-mirror.com 国内镜像（见 config/settings.py）
- ⚠️ ChromaDB 写入操作受 threading.Lock 保护（SQLite 底层非线程安全）
- ⚠️ 嵌入模型 (text2vec-base-chinese) 需预下载至 `data/models/` 目录
- ⚠️ 修改 `app/main.py` 内嵌 JS 后必须 `node --check` 验证语法
- ⚠️ 流式对话依赖 `langgraph>=1.0.0`，国内安装需清华镜像源
- ⚠️ Redis Windows 版 (5.0.14.1) 不支持 RESP3，连接池和客户端均需 `protocol=2`
- ⚠️ 缓存键包含 `category` 和 `gender` 参数，LLM 传参不一致会导致缓存未命中（已通过 query 归一化 + 参数归一化解决）

---

## 项目结构

```
agent_customer/
├── app/                        # 应用核心代码
│   ├── __init__.py
│   ├── main.py                 # FastAPI 入口 + 多页面 HTML + 22 个 API（含流式对话 + 压测）
│   ├── agent.py                # LangChain ReAct Agent (RAG 增强 + 重试 + 流式 + 熔断器)
│   ├── tools.py                # 6 个 Agent 工具函数 (含 RAG + 隐私保护交叉校验)
│   ├── models.py               # Pydantic 请求/响应模型 (22 个类)
│   ├── database.py             # SQLite 查询封装 (6 张表 CRUD + JOIN 查询)
│   ├── auth.py                 # JWT + bcrypt 认证 + 依赖注入
│   ├── logger.py               # 结构化日志 (api/rag/llm/auth/cache)
│   ├── middleware.py           # 全局异常 + 请求计时 + slowapi 限流
│   ├── rag.py                  # ChromaDB 向量存储 + 嵌入 + 检索 + 索引重建 + RAG 缓存层
│   ├── retrieval.py            # 多路召回引擎: BM25/jieba + RRF 融合 + LLM 重排序(可选)
│   ├── cache.py                # Redis 语义缓存: RAG 检索结果缓存(非 LLM 回复)
│   ├── circuit_breaker.py      # 三态熔断器: CLOSED→OPEN→HALF_OPEN + 半开探测
│   ├── benchmark.py            # 服务端压测引擎（异步并发 + 延迟分位数统计）
│   └── kb_seed_data.py         # 28 条服装知识库种子数据 (含 8 篇 SKU)
├── config/
│   ├── __init__.py
│   └── settings.py             # .env → Python 配置单例 (含 JWT/RAG/CB/Redis/Retrieval)
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
│       │   └── SKILL.md          # Git 一键推送 + 仓库描述同步
│       ├── project-init/
│       │   └── SKILL.md          # 上下文恢复
│       └── project-update/
│           └── SKILL.md          # 记忆文档更新
├── .env                        # 真实密钥 (不入 Git)
├── .env.example                # 配置模板
├── .gitignore
├── requirements.txt            # pip 依赖清单 (含 langgraph + jieba + rank-bm25 + redis)
├── db_init.py                  # 数据库初始化脚本 (6 张表 + 服装种子数据)
├── README.md                   # 项目说明（含架构决策章节）
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
| `documents` | id, title, content, category, gender, created_at, updated_at | 28 条 (种子数据) |
| `chat_sessions` | id, user_id(FK→users), title, created_at | 按需创建 |
| `chat_messages` | id, session_id(FK), role, content, citations(JSON), created_at | 按需创建 |

状态枚举: 待付款 / 已发货 / 已完成 / 已退款
用户角色: is_admin=1 (管理员) / is_admin=0 (普通用户)
知识库分类: 产品信息(含8篇SKU) / 尺码指南 / 售后政策 / 面料知识 / 品牌故事（支持自定义）
知识库性别: 男 / 女 / 通用 / 儿童

---

## 核心功能

1. **多页面 Web 前端**: 登录页 / 智能客服聊天 / 管理后台（三标签：用户管理+订单管理+文档管理）→ http://localhost:8000
2. **流式对话响应**: SSE/NDJSON 流式推送，逐 token 渲染，首字节延迟降低 78%（从 9.3s 降至 2.1s）
3. **Agent 工具调用**: 查用户、查订单、查物流、向量检索知识库、尺码推荐（共 6 个工具）
4. **RAG 知识问答**: ChromaDB 向量检索 + 28 条服装知识库（含 8 篇具体 SKU 参数文档）+ 引用来源展示
5. **多路召回**: Dense(语义) + Sparse(关键词 BM25/jieba) → RRF 融合，解决纯向量对货号/短代码匹配差的问题
6. **语义缓存**: Redis 缓存 RAG 检索结果（非 LLM 回复），跨用户安全共享，一致性保障
7. **熔断保护**: 三态状态机 + 半开探测，连续 5 次失败后快速失败 30s，防止雪崩
8. **用户认证**: 注册/登录/JWT 令牌/权限校验（admin/user 角色，统一 users 表管理）
9. **隐私保护**: 手机号×订单号交叉校验链（ContextVar），查自己放行、查别人要凭证
10. **会话管理**: 多轮对话历史存储与恢复 + 删除会话（级联删除消息）
11. **管理后台**: 三标签界面 — 用户管理（列表查看）/ 订单管理（列表+详情，JOIN 返回用户信息）/ 文档管理（CRUD + 分类管理 + 性别过滤 + 10 条/页分页 + 索引重建）
12. **压测仪表盘**: 服务端并发压测引擎（httpx），支持逐 token 流式展示，P50/P75/P90/P95/P99 延迟分析
13. **企业级基础设施**: 结构化日志 + 全局异常 + API 限流 + 请求计时 + LLM 重试(指数退避) + 对话历史安全裁剪

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
