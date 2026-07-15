---
description: 新会话启动时读取项目档案和进度记录，快速恢复上下文
---

读取以下两份文件，了解项目全貌和历史进度：

1. PROJECT_SUMMARY.md — 项目是什么、技术栈、目录结构、环境变量、关键约束
2. CLAUDE_PROGRESS.md — 历史进度、当前状态、待办事项

然后输出固定格式的摘要：

```
## 项目状态摘要

**项目**: 智能客服 Agent v0.1.0 | 仓库: 91TangZhu/agent_customer
**环境**: Python 3.10 + Conda(agent_customer) | 数据库: 5用户 8订单 5物流
**上次进度**: [最近一条记录的标题]
**当前状态**: [进行中/空闲]
**待办优先级**: [列出 P1 项]

启动: conda activate agent_customer && uvicorn app.main:app --reload
页面: http://localhost:8000
```

完成后等待用户指示下一步任务。
