# 项目概览

> 最后更新: 2026-07-15 | 版本: v0.1.0 | 状态: 🟢 开发中

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
| AI 框架 | LangChain + LangGraph (ReAct) | 1.3.13 / 1.2.9 |
| LLM 模型 | DeepSeek | deepseek-v4-flash (推荐) / deepseek-v4-pro |
| 数据库 | SQLite 3 | - |
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

> 模板文件: `.env.example`  |  密钥获取: https://platform.deepseek.com

---

## 关键约束

- ⚠️ `.env` 不能提交到 Git（已在 .gitignore）
- ⚠️ 模型只能用 `deepseek-v4-flash` 或 `deepseek-v4-pro`，老模型已下线
- ⚠️ SSH 连接 GitHub 必须走 443 端口（22 端口被墙）
- ⚠️ fastapi/uvicorn 需手动安装（不在 requirements.txt 首次安装范围）

---

## 项目结构

```
agent_customer/
├── app/                        # 应用核心代码
│   ├── __init__.py
│   ├── main.py                 # FastAPI 入口 + 聊天网页 + /chat API
│   ├── agent.py                # LangChain ReAct Agent (ChatDeepSeek + 工具)
│   ├── tools.py                # 4 个 Agent 工具函数
│   ├── models.py               # Pydantic 请求/响应模型
│   └── database.py             # SQLite 查询封装 (5 个查询函数)
├── config/
│   ├── __init__.py
│   └── settings.py             # .env → Python 配置单例
├── data/
│   ├── .gitkeep
│   └── customer.db             # SQLite 数据库 (不入 Git)
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
├── requirements.txt            # pip 依赖清单
├── db_init.py                  # 数据库初始化脚本
├── README.md                   # 项目说明
├── PROJECT_SUMMARY.md          # 本文件 — 项目档案
└── CLAUDE_PROGRESS.md          # 开发进度追踪
```

---

## 数据库

| 表名 | 字段 | 测试数据 |
|------|------|----------|
| `users` | id, name, phone, email, created_at | 5 条 |
| `orders` | id, user_id(FK), order_no, product_name, amount, status, created_at | 8 条 |
| `logistics` | id, order_id(FK), tracking_no, carrier, status, updates, created_at | 5 条 |

状态枚举: 待付款 / 已发货 / 已完成 / 已退款

---

## 核心功能

1. 中文聊天网页 → http://localhost:8000
2. Agent 工具调用: 查用户(按手机号)、查订单(按用户/订单号)、查物流(按订单ID)
3. 自然语言理解 → 自动选工具 → 格式化返回结果

---

## 启动命令

```bash
conda activate agent_customer
uvicorn app.main:app --reload
# 浏览器打开 http://localhost:8000
```

---

## Skills 清单

| Skill | 用途 | 调用方式 |
|-------|------|----------|
| `git-push` | 一键 add → commit → push | `/git-push` |
| `project-init` | 新会话启动，读取记忆恢复上下文 | `/project-init` |
| `project-update` | 完成修改后更新记忆文档 | `/project-update` |

## Agents 清单

| Agent | 用途 | 调用方式 |
|-------|------|----------|
| `record-keeper` | 记录备份工程师 — 一键完成项目记忆更新 + Git 提交推送 | 通过 Agent 工具调用 |
