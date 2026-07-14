# 项目概览

## 基本信息
- **项目名称**: 智能客服 Agent (agent_customer)
- **创建日期**: 2026-07-14
- **Python 版本**: 3.10
- **虚拟环境**: Conda (agent_customer)
- **数据库**: SQLite (data/customer.db)

## 技术栈
| 层级 | 技术 |
|------|------|
| Web 框架 | FastAPI + Uvicorn |
| AI 框架 | LangChain + LangGraph (ReAct Agent) |
| LLM 模型 | DeepSeek (deepseek-v4-flash / deepseek-v4-pro) |
| 数据库 | SQLite |
| 配置管理 | python-dotenv (.env) |

## 项目结构
```
agent_customer/
├── app/
│   ├── main.py          # FastAPI 入口, / 聊天页面, /chat API
│   ├── agent.py         # LangChain Agent (ChatDeepSeek + 工具)
│   ├── tools.py         # 4个工具: 查用户/查订单/查物流
│   ├── models.py        # Pydantic 请求/响应模型
│   └── database.py      # SQLite 查询封装
├── config/
│   └── settings.py      # .env 配置读取
├── data/
│   └── customer.db      # SQLite 数据库
├── .env                 # API Key(不入Git)
├── .env.example         # 配置模板
├── requirements.txt     # pip依赖
├── db_init.py           # 数据库初始化
└── README.md            # 项目说明
```

## 数据库表
| 表名 | 说明 | 数据量 |
|------|------|--------|
| users | 用户信息 | 5条 |
| orders | 订单信息 | 8条 |
| logistics | 物流信息 | 5条 |

## 核心功能
1. 中文聊天网页 (http://localhost:8000)
2. 自然语言查询用户/订单/物流
3. Agent 自动选择工具并返回格式化结果

## 启动方式
```bash
conda activate agent_customer
uvicorn app.main:app --reload
```
