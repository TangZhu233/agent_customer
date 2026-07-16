# 👗 服装电商智能客服 Agent

基于 **LangChain + LangGraph + DeepSeek + ChromaDB** 的企业级 RAG 智能客服系统，支持服装知识问答、尺码推荐、订单物流查询、多用户会话管理。

## 技术栈

| 组件 | 技术 |
|------|------|
| LLM 模型 | DeepSeek (deepseek-v4-flash / deepseek-v4-pro) |
| Agent 框架 | LangChain + LangGraph (ReAct) |
| RAG 管道 | ChromaDB + text2vec-base-chinese |
| Web 框架 | FastAPI |
| 数据库 | SQLite (7 张表) |
| 认证 | JWT + bcrypt |
| 语言 | Python 3.10 |

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
生成 `data/customer.db`，包含 7 张表和测试数据（5 用户 + 8 订单 + 5 物流 + 2 认证账号）。

### 5. 启动服务
```bash
uvicorn app.main:app --reload
```
打开 **http://localhost:8000** 进入聊天页面。

### 6. 初始化知识库
1. 用管理员账号登录（`admin` / `123456`）
2. 点击 ⚙️ **知识库管理** → 🔄 **初始化默认知识库**
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
| 💬 多轮对话 | 独立会话 + 历史消息持久化 |
| 🔐 用户系统 | 注册/登录/修改密码/JWT 认证 |
| 📚 知识库管理 | 管理员 CRUD + 分类标签过滤 |

## API 接口

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| GET | `/` | 聊天页面（多页面 SPA） | - |
| GET | `/health` | 健康检查 | - |
| POST | `/chat` | 对话接口（RAG + 引用） | 可选 |
| POST | `/auth/register` | 用户注册 | - |
| POST | `/auth/login` | 用户登录 → JWT | - |
| POST | `/auth/change-password` | 修改密码 | 必需 |
| GET | `/auth/me` | 当前用户信息 | 必需 |
| GET | `/sessions` | 会话列表 | 必需 |
| POST | `/sessions` | 新建会话 | 必需 |
| GET | `/sessions/{id}/messages` | 会话历史 | 必需 |
| DELETE | `/sessions/{id}` | 删除会话（级联删除消息） | 必需 |
| GET | `/admin/kb/documents` | 知识库文档列表 | 管理员 |
| POST | `/admin/kb/documents` | 添加文档 | 管理员 |
| PUT | `/admin/kb/documents/{id}` | 更新文档 | 管理员 |
| DELETE | `/admin/kb/documents/{id}` | 删除文档 | 管理员 |
| POST | `/admin/kb/init` | 初始化知识库 | 管理员 |

## 项目结构
```
agent_customer/
├── app/
│   ├── main.py            # FastAPI 入口 + 15 个 API 端点 + 多页面 HTML
│   ├── agent.py           # RAG 增强 Agent（系统提示词 + LLM 重试）
│   ├── tools.py            # 6 个 Agent 工具（含 search_knowledge_base）
│   ├── models.py           # 20+ Pydantic 模型
│   ├── database.py         # SQLite 查询封装（7 张表完整 CRUD）
│   ├── auth.py             # JWT 签发/验证 + bcrypt 密码哈希
│   ├── rag.py              # ChromaDB 向量存储 + 嵌入 + 检索 + KB 同步
│   ├── logger.py           # 结构化日志（api/rag/llm/auth）
│   ├── middleware.py        # 全局异常捕获 + 请求计时 + 限流
│   └── kb_seed_data.py     # 19 条服装知识库种子数据
├── config/
│   └── settings.py          # 配置单例（环境变量 → Python）
├── data/
│   ├── customer.db          # SQLite 数据库
│   ├── chroma_db/           # ChromaDB 向量存储
│   └── models/              # 嵌入模型本地缓存
├── logs/
│   └── app.log              # 应用日志（10MB × 5 文件轮转）
├── .claude/
│   ├── agents/
│   │   └── record-keeper.md  # 记录备份工程师 Agent
│   └── skills/
│       ├── git-push/          # Git 一键推送 + 仓库描述同步
│       ├── project-init/      # 上下文恢复
│       └── project-update/    # 记忆文档更新
├── .env.example             # 环境变量模板
├── .gitignore
├── db_init.py               # 数据库 + 种子数据初始化
├── requirements.txt          # pip 依赖清单
├── PROJECT_SUMMARY.md       # 项目档案
├── CLAUDE_PROGRESS.md       # 开发进度追踪
├── INTERVIEW_PREP.md        # 面试准备文档
└── ISSUES_LOG.md            # 问题记录与修复日志
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

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 |
| `DEEPSEEK_MODEL` | 模型名称（默认 `deepseek-v4-flash`） |
| `JWT_SECRET_KEY` | JWT 签名密钥 |
| `EMBEDDING_MODEL` | 嵌入模型路径 |
| `VECTOR_SEARCH_K` | 向量检索返回数量（默认 5） |
| `GITHUB_TOKEN` | GitHub API Token（可选） |
