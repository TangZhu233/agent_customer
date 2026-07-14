# 🤖 智能客服 Agent

基于 **LangChain + DeepSeek + SQLite** 的智能客服系统，支持自然语言查询用户、订单、物流信息。

## 技术栈

| 组件 | 技术 |
|------|------|
| LLM 模型 | DeepSeek (deepseek-v4-flash) |
| Agent 框架 | LangChain + LangGraph (ReAct) |
| Web 框架 | FastAPI |
| 数据库 | SQLite |
| 语言 | Python 3.10 |

## 快速开始

### 1. 环境准备
```bash
# 激活 conda 虚拟环境
conda activate agent_customer

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置 API Key
```bash
# 复制配置模板
cp .env.example .env

# 编辑 .env，填入你在 https://platform.deepseek.com 获取的 API Key
# DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx
```

### 3. 初始化数据库
```bash
python db_init.py
```
执行后在 `data/` 目录生成 `customer.db`，包含用户、订单、物流三张表及测试数据。

### 4. 启动服务
```bash
uvicorn app.main:app --reload
```
打开 http://localhost:8000/docs 查看 Swagger 接口文档。

### 5. 测试对话
```bash
# 用 curl 测试（或在 /docs 页面直接试用）
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "帮我查一下手机号13800138001的用户信息"}'
```

## 项目结构
```
agent_customer/
├── app/
│   ├── main.py          # FastAPI 入口 & 路由
│   ├── agent.py         # LangChain Agent（DeepSeek + 工具绑定）
│   ├── tools.py         # Agent 工具函数（查用户/订单/物流）
│   ├── models.py        # Pydantic 请求/响应模型
│   └── database.py      # SQLite 查询封装
├── config/
│   └── settings.py      # 配置读取（.env → Python）
├── data/
│   └── customer.db      # SQLite 数据库（db_init.py 生成）
├── .env.example         # 环境变量模板
├── .gitignore
├── db_init.py           # 数据库初始化脚本
└── requirements.txt     # pip 依赖清单
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 健康检查 |
| POST | `/chat` | 对话接口 |

`/chat` 请求示例：
```json
{
  "message": "13800138001这个手机号的用户有哪些订单？"
}
```
