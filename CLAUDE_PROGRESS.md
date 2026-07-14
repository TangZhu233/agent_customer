# 开发进度追踪

> 每次完成核心修改后由 Claude 生成更新内容，用户确认后保存。

---

## [2026-07-14 10:22] 项目初始化

**完成内容：**
- 创建完整项目目录结构 (app/、config/、data/)
- 编写 requirements.txt（LangChain + FastAPI + DeepSeek 等 10 个依赖）
- 编写 .env.example 配置模板（含 DeepSeek 密钥获取教程）
- 编写 .gitignore（排除 .env、__pycache__、*.db）
- 实现 config/settings.py 配置读取模块
- 实现 app/database.py SQLite 查询封装（5 个查询函数）
- 实现 app/models.py Pydantic 数据模型
- 实现 app/tools.py 四个 Agent 工具函数
- 实现 app/agent.py LangChain ReAct Agent
- 实现 app/main.py FastAPI 服务入口
- 实现 db_init.py 数据库初始化脚本
- 创建 README.md 项目说明

**数据库：** 5 用户 + 8 订单 + 5 物流（测试数据）

---

## [2026-07-14 10:35] DeepSeek 模型名更新 + API 参数修复

**问题：**
- 老版 `deepseek-chat` 模型即将下线，DeepSeek 目前只支持 `deepseek-v4-flash` / `deepseek-v4-pro`
- 新版 langchain-deepseek 1.1.0 参数名从 `model=` 改为 `model_name=`

**修改文件：**
- .env.example — 默认模型改为 `deepseek-v4-flash`
- config/settings.py — 默认值同步
- app/agent.py — `model=` → `model_name=`
- README.md — 技术栈描述更新

---

## [2026-07-14 10:45] 安装缺失依赖 + 端到端验证

**问题：** fastapi 和 uvicorn 未安装在 agent_customer 环境中，导致服务无法启动

**操作：**
- pip install fastapi uvicorn[standard]
- 验证所有依赖可用
- 启动服务 → /health 通过 → /chat API 通过
- 端到端测试：Agent 成功调用工具查询用户和订单

---

## [2026-07-14 10:55] 添加中文聊天网页

**问题：** Swagger /docs 页面英文多、上手难，用户不知道在哪输入

**修改：**
- app/main.py — GET / 路由改为返回中文聊天 HTML 页面
- 原健康检查移至 GET /health
- 聊天页面特性：纯中文、输入框+发送按钮、回车发送、加载动画

---
