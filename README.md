# 👗 服装电商智能客服 Agent

基于 **LangChain + LangGraph + DeepSeek + ChromaDB** 的企业级 RAG 智能客服系统，支持服装知识问答、尺码推荐、订单物流查询、多用户会话管理。

> 不仅仅是跑通 Demo——本项目在每个环节都做了面向生产环境的工程决策。下文「架构决策」章节详细记录了多路召回、语义缓存、重排序取舍等关键设计及其背后的权衡。

## 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| LLM 模型 | DeepSeek (deepseek-v4-flash / deepseek-v4-pro) | 性价比最高的中文大模型 |
| Agent 框架 | LangChain + LangGraph (ReAct) | Think→Act→Observe 循环 |
| RAG 管道 | ChromaDB + text2vec-base-chinese | 嵌入式向量数据库 + 中文语义模型 |
| 多路召回 | Dense(ChromaDB) + Sparse(BM25/jieba) → RRF 融合 | 语义 + 关键词双路互补 |
| 语义缓存 | Redis — RAG 检索结果缓存 | 缓存命中 <10ms，跨用户安全共享 |
| 熔断器 | 三态状态机 (CLOSED→OPEN→HALF_OPEN) | 连续 5 次失败后快速失败 30s |
| Web 框架 | FastAPI + Uvicorn | 异步并发 + SSE 流式 |
| 数据库 | SQLite (6 张表) | 零部署依赖 |
| 认证 | JWT + bcrypt | 无状态认证 + 角色权限 |
| 限流 | slowapi | IP 级 60次/分钟 |
| 语言 | Python 3.10 | — |

## 快速开始

### 1. 环境准备
```bash
conda activate agent_customer

# 安装依赖（国内用户建议加清华镜像）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 2. 配置 API Key
```bash
cp .env.example .env
# 编辑 .env，填入 DeepSeek API Key 和 GitHub Token（可选）
# DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx
# GITHUB_TOKEN=ghp_xxxxxxxxxxxx  （可选，用于自动更新仓库描述）
```

### 3. 下载嵌入模型（首次运行必需）
```bash
python -c "
import os; os.environ['HF_ENDPOINT']='https://hf-mirror.com'
from sentence_transformers import SentenceTransformer
model = SentenceTransformer('shibing624/text2vec-base-chinese', device='cpu')
model.save('data/models/text2vec-base-chinese')
"
```
模型约 400MB，仅需下载一次。后续启动服务直接加载本地文件。

### 4. 初始化数据库
```bash
python db_init.py
```
生成 `data/customer.db`，包含 6 张表和测试数据（6 用户 + 8 订单 + 5 物流）。

### 5. 启动服务
```bash
conda activate agent_customer
uvicorn app.main:app --reload
```
打开 **http://localhost:8000** 进入聊天页面。

### 6. 初始化知识库
1. 用管理员账号登录（`admin` / `123456`）
2. 点击 ⚙️ **后台管理** → **文档管理** → 🔄 **初始化默认知识库**
3. 完成后即可在聊天中提问服装相关问题

## 测试账号

| 用户名 | 密码 | 角色 |
|--------|------|------|
| `admin` | `123456` | 管理员（可管理知识库） |
| `testuser` | `123456` | 普通用户（仅问答） |

## 核心功能

| 功能 | 说明 |
|------|------|
| 📏 尺码推荐 | 根据身高体重推荐精准尺码（调用知识库） |
| 🎨 颜色搭配 | 肤色/季节/场合穿搭建议 |
| 🧺 洗涤保养 | 棉/羊毛/牛仔/真丝等面料护理指南 |
| ℹ️ 产品咨询 | 面料特性、新品系列、选购指南 |
| 📦 订单物流 | 查询用户订单状态和快递进度 |
| 💬 多轮对话 | 独立会话 + 历史消息持久化 + 流式逐 token 推送 |
| 🔐 用户系统 | 注册/登录/修改密码/JWT 认证 |
| 📚 知识库管理 | 管理员 CRUD + 分类标签过滤 + 性别过滤 |
| 📊 压测仪表盘 | 服务端并发压测 + 延迟百分位分析 + 流式实时展示 |

---

## 🏗️ 架构决策（面试重点）

以下记录了本项目从"能跑"到"能落地"过程中做出的关键工程决策。

### 一、多路召回：为什么 Dense + BM25 混用？

**问题：** 纯向量检索（dense）对短代码/货号查询几乎无效。

`"T-SU-WH-L"` 这种 SKU 编码，向量模型从未在训练数据中见过，编码出来的 512 维向量近乎随机方向。用户搜货号时，dense 可能把完全不相关的文档排在前面。

**方案：** Dense（语义理解）+ Sparse（关键词匹配）双路并行 → RRF 融合。

```
用户查询 "T-SU-WH-L"
    │
    ├── 路1 Dense (ChromaDB) → "我感觉这几个语义比较近" → 10 篇
    │
    └── 路2 Sparse (BM25/jieba) → "文档里有没有写这几个字" → 5 篇
            │
            └── RRF 融合去重 → 最终 Top-5
```

- **Dense 擅长：** 长句语义查询（"夏天穿什么面料凉快"）、同义词（"棉T"≈"纯棉T恤"）
- **Sparse 擅长：** 精确货号（"T-SU-WH-L"）、专有名词、数字参数
- **RRF（Reciprocal Rank Fusion）：** 只关心排名不关心分数尺度，天然免疫稠密/稀疏两路分数不可比的问题

**为什么不是加权分数融合？** 稠密的余弦距离和 BM25 的统计分数不在同一尺度，加权需要对两者分别做 min-max 归一化，而 RRF 只需排名——k=60 是学界标准参数，零调参成本。

### 二、语义缓存：为什么缓存 RAG 检索结果而非 LLM 最终回复？

**这是一次完整的架构重构。** 最初缓存的是 LLM 最终回复，上线后发现严重 bug。

**事故：** 用户 A 在对话中先问 `SKU-2026-001`（返回"未找到"），再问 `T-SU-WH-L`（返回详细信息）。用户 B 单独问 `T-SU-WH-L`，命中了 A 的缓存，**收到了包含 A 上下文（"刚才那个 SKU-2026-001 没找到"）的回复**——跨用户信息泄露。

**根因分析：** ReAct Agent 的回复不是纯函数。同样的 `"T-SU-WH-L 是什么"` 在不同对话历史、不同用户身份下，LLM 会给出不同措辞。缓存最终回复 = 缓存了上下文相关的副作用。

**正确做法——缓存下沉到 RAG 检索层：**

```
改前（❌）：User Query → Agent(思考+工具+推理) → 缓存最终回复
                              ↑ 回复依赖对话历史、用户身份，不可缓存

改后（✅）：User Query → 缓存 RAG 检索结果 → Agent 基于结果推理
                              ↑ 检索是纯函数：同样的(query, k, category, gender, mode)
                                永远返回同样的文档列表，与用户是谁无关
```

**缓存键设计：** `MD5(normalized_query|category|gender|k|retrieval_mode)`

- `normalized_query`：去空格 + 去中文标点 + 小写化 → LLM 对同一问题产生的措辞变体（"纯棉T恤 洗涤" vs "纯棉T恤洗涤"）归一化为同一缓存键
- 包含 `retrieval_mode`：dense 和 hybrid 对同一查询返回不同排序，必须区分
- 不包含 `user_id`：RAG 结果用户无关，共享才安全

**收益：**

| 维度 | 缓存 LLM 回复 | 缓存 RAG 结果 |
|------|-------------|-------------|
| 跨用户安全 | ❌ 上下文泄露 | ✅ 纯函数，天然安全 |
| 命中率 | 低（措辞难完全一致） | 高（query 归一化后复用） |
| 加速效果 | 跳过 3~9s LLM | 跳过 ~50ms 向量搜索 |
| 一致性 | 无保证 | 所有人基于同一知识源回答 |

> **关键洞察：缓存在这里的首要价值不是速度，是一致性和去重。** 100 个人同时问"退货政策"，ChromaDB 只被调用 1 次，所有人基于同一套文档回答。LLM 推理仍然每人独立跑——那是"个性化"部分，不能省也不该省。

### 三、重排序：为什么不开？

`RERANK_ENABLED=false`（默认关闭），这是刻意为之。

**重排序做了什么：** RRF 融合出 ~12 篇候选文档后，调用 DeepSeek 逐篇读内容再排一次序，取 Top-5。

**不开的理由：**

| 考量 | 分析 |
|------|------|
| 知识库规模 | 仅 28 篇文档，RRF 融合后候选通常 5~8 篇，重排意义极小 |
| 延迟代价 | 每次检索多 1 次 LLM 调用（+500ms~1s） |
| 费用 | 每次重排额外消耗 token（12 篇摘要 + prompt） |
| 收益 | 只在候选文档多（50+）且内容相近时才体现价值 |

**什么时候开：** 知识库上千篇、同类文档（如 50 篇不同面料的 T 恤）混杂时，LLM 的语义理解能力才有用武之地。现阶段 RRF 已经足够。

> **工程原则：不是所有开关都要打开。** 知道一个技术"能做什么"和知道它"现在该不该用"，是 demo 和落地之间的区别。

### 四、熔断器：为什么包裹 tenacity 重试而不是替代它？

```
LLM 调用
  ├── tenacity 重试（3次，2s→4s→8s 指数退避） ← 处理偶发网络抖动
  └── 熔断器（连续 5 次 RetryError → OPEN 30s） ← 处理持续性故障
```

重试和熔断解决不同层次的问题：
- 单次 TCP 超时 → tenacity 自动重试，用户无感知
- DeepSeek API 持续宕机 → 熔断器快速失败，不浪费时间等待
- 关键：tenacity 的 `RetryError`（3 次全失败）才算熔断器的 1 次失败，避免单次抖动触发熔断

### 五、其他生产级考量

- **对话历史安全裁剪**：两阶段截断——消息数限制（默认 20 条）+ 字符数安全网（默认 80000），system prompt 和当前消息永不丢弃
- **Agent 防死循环**：`recursion_limit=10`——客服正常 2~4 步，超限 LangGraph 抛异常被友好拦截
- **RAG 降级链**：gender 过滤空结果 → 自动去除 gender 重试 → 仍然空 → 告知用户联系人工客服
- **缓存永远是旁路**：所有 Redis 操作 try/except 包裹，Redis 挂了不影响服务——直接查 ChromaDB
- **ContextVar 并发隔离**：FastAPI 协程并发下，多个请求不串扰——类比"每人一张便签纸"，不是"公共白板"
- **嵌入模型单例 + 懒加载**：400MB 模型只加载一次，避免每次请求重新加载（2~5s → 0）

---

## API 接口

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| GET | `/` | 聊天页面（多页面 SPA） | - |
| GET | `/health` | 健康检查 | - |
| POST | `/chat` | 对话接口（支持 stream=True 流式响应） | 可选 |
| POST | `/auth/register` | 用户注册 | - |
| POST | `/auth/login` | 用户登录 → JWT | - |
| POST | `/auth/change-password` | 修改密码 | 必需 |
| GET | `/auth/me` | 当前用户信息 | 必需 |
| GET | `/sessions` | 会话列表 | 必需 |
| POST | `/sessions` | 新建会话 | 必需 |
| GET | `/sessions/{id}/messages` | 会话历史 | 必需 |
| DELETE | `/sessions/{id}` | 删除会话（级联删除消息） | 必需 |
| GET | `/benchmark` | 压测仪表盘页面 | - |
| POST | `/benchmark` | 发起压测（同步返回汇总） | 管理员 |
| POST | `/benchmark/stream` | 发起压测（流式逐结果推送） | 管理员 |
| GET | `/admin/users` | 用户列表 | 管理员 |
| GET | `/admin/orders` | 订单列表 | 管理员 |
| GET | `/admin/orders/{id}` | 订单详情 | 管理员 |
| GET | `/admin/kb/documents` | 知识库文档列表 | 管理员 |
| POST | `/admin/kb/documents` | 添加文档 | 管理员 |
| PUT | `/admin/kb/documents/{id}` | 更新文档 | 管理员 |
| DELETE | `/admin/kb/documents/{id}` | 删除文档（自动失效缓存 + 重建 BM25） | 管理员 |
| POST | `/admin/kb/init` | 初始化知识库 | 管理员 |
| POST | `/admin/kb/reindex` | 重建向量索引 + BM25 索引 | 管理员 |
| GET | `/admin/kb/categories` | 文档分类列表 | 管理员 |

## 项目结构
```
agent_customer/
├── app/
│   ├── main.py              # FastAPI 入口 + 22 个 API 端点 + 多页面 HTML
│   ├── agent.py             # RAG 增强 Agent（系统提示词 + LLM 重试 + 流式生成器）
│   ├── tools.py              # 6 个 Agent 工具（含 search_knowledge_base）
│   ├── models.py             # 25+ Pydantic 模型
│   ├── database.py           # SQLite 查询封装（6 张表完整 CRUD）
│   ├── auth.py               # JWT 签发/验证 + bcrypt 密码哈希
│   ├── rag.py                # ChromaDB 向量存储 + 嵌入 + 检索 + KB 同步 + 索引重建
│   ├── retrieval.py          # 多路召回引擎（BM25/jieba + RRF 融合 + 可选 LLM 重排序）
│   ├── cache.py              # Redis 语义缓存（RAG 检索层，非 LLM 回复层）
│   ├── circuit_breaker.py    # 三态熔断器（CLOSED→OPEN→HALF_OPEN）
│   ├── logger.py             # 结构化日志（api/rag/llm/auth/cache）
│   ├── middleware.py          # 全局异常捕获 + 请求计时 + slowapi 限流
│   ├── benchmark.py           # 服务端压测引擎（异步并发 + 延迟分位数）
│   └── kb_seed_data.py       # 28 条服装知识库种子数据（含 8 篇 SKU）
├── config/
│   └── settings.py            # 配置单例（环境变量 → Python，含 RAG/CB/Redis/检索全配置）
├── data/
│   ├── customer.db            # SQLite 数据库
│   ├── chroma_db/             # ChromaDB 向量存储
│   └── models/                # 嵌入模型本地缓存
├── logs/
│   └── app.log                # 应用日志（10MB × 5 文件轮转）
├── .claude/
│   ├── agents/
│   │   └── record-keeper.md   # 记录备份工程师 Agent
│   └── skills/
│       ├── git-push/           # Git 一键推送 + 仓库描述同步
│       ├── project-init/       # 上下文恢复
│       └── project-update/     # 记忆文档更新
├── .env.example               # 环境变量模板
├── .gitignore
├── db_init.py                 # 数据库 + 种子数据初始化
├── requirements.txt            # pip 依赖清单
├── PROJECT_SUMMARY.md         # 项目档案（技术栈/数据库/约束/Skills/Agents）
├── CLAUDE_PROGRESS.md         # 开发进度追踪（时间线 + 待办 + 踩坑记录）
├── INTERVIEW_PREP.md          # 面试准备（架构图/核心亮点/话术/关键词自查）
└── ISSUES_LOG.md              # 问题记录与修复日志
```

## 对话示例

```
用户: 我身高175体重70，男，穿什么尺码的上衣？

Agent: 根据知识库中的男装上衣尺码指南：
- M 码：168-175cm，55-68kg
- L 码：173-180cm，68-78kg ✅ ← 推荐

您的身高体重正好落在 L 码范围内，建议选择 L 码。
如果喜欢宽松风格，可选大一码 XL。

📚 参考来源 (2)
  · 上衣尺码推荐表（男装）
  · 裤装尺码对照表
```

## 环境变量

完整配置见 `.env.example`，关键变量：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | — |
| `DEEPSEEK_MODEL` | 模型名称 | `deepseek-v4-flash` |
| `JWT_SECRET_KEY` | JWT 签名密钥 | — |
| `EMBEDDING_MODEL` | 嵌入模型路径 | `data/models/text2vec-base-chinese` |
| `VECTOR_SEARCH_K` | 向量检索返回数量 | `5` |
| `RETRIEVAL_MODE` | 检索模式：`dense` 或 `hybrid` | `hybrid` |
| `RERANK_ENABLED` | 是否启用 LLM 重排序 | `false` |
| `MULTI_RECALL_K` | 多路召回每路拉取数量 | `10` |
| `FUSION_K` | RRF 平滑常数 | `60` |
| `REDIS_ENABLED` | 是否启用 Redis 语义缓存 | `true` |
| `REDIS_URL` | Redis 连接地址 | `redis://localhost:6379/0` |
| `REDIS_CACHE_TTL` | 缓存过期时间（秒） | `3600` |
| `CB_ENABLED` | 是否启用熔断器 | `true` |
| `CB_FAILURE_THRESHOLD` | 熔断触发连续失败次数 | `5` |
| `CB_RECOVERY_TIMEOUT` | 熔断恢复等待时间（秒） | `30` |
| `LLM_REQUEST_TIMEOUT` | LLM 调用超时（秒） | `30.0` |
| `MAX_HISTORY_MESSAGES` | 对话历史消息数上限 | `20` |
| `MAX_HISTORY_CHAR_LIMIT` | 对话历史字符数安全上限 | `80000` |
| `GITHUB_TOKEN` | GitHub API Token（可选） | — |

---

*更多技术细节见 [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md)（项目档案）、[INTERVIEW_PREP.md](INTERVIEW_PREP.md)（面试准备）、[CLAUDE_PROGRESS.md](CLAUDE_PROGRESS.md)（开发进度）。*
