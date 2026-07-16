# 开发进度追踪

> 每次完成核心修改后，由 Claude 按模板生成记录，用户确认后保存。
> 新会话启动时通过 `/project-init` 自动加载。

---

## 当前状态

- **版本**: v0.2.1
- **最后更新**: 2026-07-16
- **进行中**: 无
- **待办**: 见下方「待办事项」

---

## 进度时间线

### [2026-07-14 10:22] 项目初始化
- **类型**: 功能开发
- **概述**: 从零搭建智能客服 Agent 项目完整骨架
- **新增文件**:
  - `app/` 全部模块 (main.py / agent.py / tools.py / models.py / database.py)
  - `config/settings.py` / `.env.example` / `.gitignore`
  - `requirements.txt` / `db_init.py` / `README.md`
- **数据库**: users(5) + orders(8) + logistics(5) 测试数据
- **验证**: db_init.py 运行成功，生成 data/customer.db (32KB)

---

### [2026-07-14 10:35] 修复: DeepSeek 模型名 + API 参数
- **类型**: Bug 修复
- **问题**: 老版 `deepseek-chat` 即将下线，新版 langchain-deepseek 参数名从 `model=` 变为 `model_name=`
- **影响文件**: `.env.example` / `config/settings.py` / `app/agent.py` / `README.md`
- **方案**: 默认模型改为 `deepseek-v4-flash`，`agent.py` 参数改为 `model_name=`
- **验证**: 导入测试通过

---

### [2026-07-14 10:45] 修复: 安装缺失依赖 + 端到端验证
- **类型**: 环境修复
- **问题**: fastapi 和 uvicorn 未安装在 agent_customer 环境，服务无法启动
- **影响文件**: agent_customer conda 环境
- **方案**: `pip install fastapi uvicorn[standard]`
- **验证**: 启动服务 → /health 200 → /chat 200 → Agent 正常调用工具

---

### [2026-07-14 10:55] 优化: 添加中文聊天网页
- **类型**: 体验优化
- **问题**: Swagger /docs 英文多、技术门槛高，用户找不到输入位置
- **影响文件**: `app/main.py`
- **方案**: GET / 返回内嵌中文聊天 HTML 页面，原健康检查移至 /health
- **验证**: 浏览器打开 http://localhost:8000 显示中文聊天界面

---

### [2026-07-14 11:10] 基础设施: SSH 免密 + Git 推送 + Skill
- **类型**: 基础设施
- **问题**: (1) 无 SSH 密钥每次推送需输密码 (2) 国内 GitHub 22 端口被墙
- **影响文件**: `~/.ssh/id_ed25519` / `~/.ssh/config` / `.claude/skills/git-push.md`
- **方案**: 生成 ED25519 密钥 → 配置走 443 端口 → 创建 `/git-push` 一键推送 Skill
- **验证**: `ssh -T git@github.com` 返回 `Hi 91TangZhu!`

---

### [2026-07-14 11:25] 优化: 记忆文档规范化 + project-init / project-update Skill
- **类型**: 基础设施
- **问题**: 原有记忆文档格式松散、缺关键字段（仓库地址/环境变量/约束/待办），新会话难以快速恢复上下文
- **概述**:
  - PROJECT_SUMMARY.md 重写为企业级格式: 增加远程仓库、版本号、环境变量清单、关键约束、Skills 清单
  - CLAUDE_PROGRESS.md 统一 5 条历史记录为规范模板，增加「当前状态」「待办事项」「记录模板」
  - 创建 `/project-init` Skill（新会话恢复上下文）
  - 创建 `/project-update` Skill（完成修改后更新记忆文档）
- **影响文件**:
  - `PROJECT_SUMMARY.md` — 全面重写
  - `CLAUDE_PROGRESS.md` — 格式规范化
  - `.claude/skills/project-init.md` — 新建
  - `.claude/skills/project-update.md` — 新建
- **验证**: 两份文档结构完整，两个 Skill 指令可执行

---

### [2026-07-15 09:33] 基础设施: 插件命令迁移至 Skills/Agents 标准格式
- **类型**: 基础设施
- **问题**: 旧插件命令系统（`.claude/plugins/agent-customer-tools/`）格式不规范，缺少 Agent 定义能力，无法支持子代理独立执行任务
- **概述**:
  - 删除旧插件目录 `agent-customer-tools/`（4 个文件，81 行）
  - 新建 `.claude/skills/` 目录，将 3 个命令（project-init / project-update / git-push）迁移为标准 Skill 格式（独立目录 + `SKILL.md`）
  - 新建 `.claude/agents/record-keeper.md` Agent 定义，支持子代理独立执行备份归档任务
- **影响文件**:
  - 删除: `.claude/plugins/agent-customer-tools/` 全部 4 个文件
  - 新增: `.claude/skills/project-init/SKILL.md`
  - 新增: `.claude/skills/project-update/SKILL.md`
  - 新增: `.claude/skills/git-push/SKILL.md`
  - 新增: `.claude/agents/record-keeper.md`
- **方案**: Skills 采用目录 + `SKILL.md` 标准格式，Agents 独立目录存放代理定义，与 Claude Code 官方规范对齐
- **验证**: 3 个 Skill 可在系统内被 `/` 命令识别和调用，`record-keeper` Agent 可独立执行备份归档流程

---

### [2026-07-15] 功能开发: 企业级 RAG 知识问答系统
- **类型**: 功能开发 + 基础设施
- **问题**: v0.1.0 仅支持查订单/物流等事务操作，缺乏知识问答能力；缺少用户认证、会话管理、知识库后台、限流/日志等企业级基础设施
- **概述**:
  - RAG 知识问答管道（ChromaDB + text2vec-base-chinese + 19 条服装知识种子）
  - 用户认证系统（JWT + bcrypt + FastAPI 依赖注入）
  - 会话管理（chat_sessions / chat_messages，多轮对话历史存储）
  - 知识库管理后台（CRUD API + HTML 管理页面）
  - 全局基础设施（结构化日志 + 异常捕获 + API 限流 + 请求计时）
  - Agent 增强（RAG 增强系统提示词 + tenacity 重试 + ContextVar 引用收集）
  - 新增 Agent 工具：search_knowledge_base（向量检索）+ recommend_size（尺码推荐）
  - 前端重构为多页面（登录页 / 聊天页 / 管理后台）
- **新增文件**:
  - `app/logger.py` — 结构化日志（api/rag/llm/auth 四通道）
  - `app/middleware.py` — 全局异常捕获 + 请求计时 + slowapi 限流
  - `app/auth.py` — JWT 令牌 + bcrypt 密码 + FastAPI 依赖注入
  - `app/rag.py` — ChromaDB 向量存储 + 嵌入 + 相似检索 + 知识库同步
  - `app/kb_seed_data.py` — 19 条服装知识库种子数据
- **修改文件**:
  - `app/main.py` — 3 → 15 个 API 端点 + 多页面 HTML 前端
  - `app/agent.py` — RAG 增强提示词 + tenacity 重试 + ContextVar 引用收集
  - `app/tools.py` — 新增 search_knowledge_base + recommend_size 工具
  - `app/models.py` — 新增 16 个 Pydantic 模型（auth/session/kb/citation）
  - `app/database.py` — 3 → 7 张表完整 CRUD（新增 auth_users/documents/chat_sessions/chat_messages）
  - `config/settings.py` — 新增 JWT/RAG/限流配置 + HuggingFace 国内镜像
  - `db_init.py` — 全量表创建 + admin/testuser 种子账号
  - `requirements.txt` — 新增 7 个依赖（chromadb/langchain-chroma/sentence-transformers/passlib[bcrypt]/python-jose[cryptography]/slowapi/langchain-huggingface）
  - `.env.example` — 新增 JWT/RAG/限流环境变量模板
  - `.gitignore` — 新增 chroma_db/ models/ logs/ 排除规则
- **数据库**: 3 → 7 张表（新增 auth_users / documents / chat_sessions / chat_messages）
- **API 端点**: 3 → 15 个（/auth/* 认证 / /sessions/* 会话 / /admin/kb/* 知识库管理 / /chat 增强）
- **验证**:
  - 数据库初始化成功（7 张表 + 种子数据）
  - RAG 管道正常运行（19 文档嵌入 + 检索约 50ms）
  - 认证流程完整（注册 → 登录 → JWT 签发 → 权限校验）
  - 端到端对话验证通过（RAG 尺码推荐 + 引用展示）
  - 服务正常启动: http://localhost:8000

---

### [2026-07-15] 基础设施: 优化 record-keeper Agent 与 git-push Skill，集成远程仓库描述同步
- **类型**: 基础设施
- **问题**: record-keeper Agent 工作流程繁琐（分步确认），缺少项目上下文，git-push Skill 功能单一
- **概述**:
  - record-keeper Agent 新增项目背景章节（版本/仓库/技术栈/数据库等），减少上下文切换
  - 合并确认步骤（第一步汇总后一次 AskUserQuestion 搞定）
  - record-keeper 和 git-push 集成 GitHub API 自动同步远程仓库描述功能
  - .env / .env.example 新增 GITHUB_TOKEN 配置
- **影响文件**:
  - `.claude/agents/record-keeper.md` — 新增项目上下文 + 合并确认步骤 + 集成远程描述同步
  - `.claude/skills/git-push/SKILL.md` — 新增仓库描述自动同步功能
  - `.env.example` — 新增 GITHUB_TOKEN 配置
  - `.env` — 本地添加 GITHUB_TOKEN（不入 Git）
- **方案**: 利用 GitHub REST API (PATCH /repos/{owner}/{repo}) 自动同步项目定位描述，减少手动维护
- **验证**: record-keeper Agent 可独立完成归档推送流程，推送后自动更新仓库描述

---

### [2026-07-15] 文档: README 全面重写至 v0.2.0，配套同步机制
- **类型**: 文档
- **问题**: README.md 停留在 v0.1.0 水平（仅基础 Agent + SQLite 描述），缺少 RAG/认证/15 个 API 端点/完整项目结构/对话示例/环境变量等关键信息，与当前 v0.2.0 企业级功能严重不匹配
- **概述**:
  - README.md 全面重写：标题改为「服装电商智能客服 Agent」，新增完整技术栈表格（含 RAG/JWT/ChromaDB）、详细启动流程（6 步，涵盖嵌入模型下载和知识库初始化）、测试账号、核心功能清单、15 个 API 端点表、完整项目结构树、对话示例、环境变量说明
  - git-push Skill 新增「同步 README」步骤：推送后自动对比 PROJECT_SUMMARY.md 校验 README 一致性并自动更新提交
  - record-keeper Agent 第二步新增 README 同步子步骤，确保文档三件套（README + PROJECT_SUMMARY + CLAUDE_PROGRESS）保持同步
- **影响文件**:
  - `README.md` — 全面重写（86 → 167 行，新增 6 个章节）
  - `.claude/skills/git-push/SKILL.md` — 新增 README 同步步骤（Step 2）
  - `.claude/agents/record-keeper.md` — 新增 README 同步子步骤（Step 2.3）
- **方案**: 以 README 为对外门面、PROJECT_SUMMARY 为内部档案、CLAUDE_PROGRESS 为变更日志，三者通过 Agent/Skill 机制自动保持一致性
- **验证**: README 内容与 PROJECT_SUMMARY.md 核心指标一致（版本/技术栈/结构/端点），git-push Skill 包含 README 校验逻辑，record-keeper Agent 流程覆盖三份文档

---

### [2026-07-15] 文档: 新增面试准备文档 + 快速启动章节修正
- **类型**: 文档
- **问题**: (1) 缺乏阶段性面试回顾材料，需要通过系统总结来梳理技术要点 (2) PROJECT_SUMMARY.md 快速启动步骤与实际运行流程不一致（db_init 已自动化、管理员密码文档与代码不符）
- **概述**:
  - 新增 INTERVIEW_PREP.md 面试准备文档（154行），涵盖项目概述、技术栈演进、架构图、核心模块、数据库设计、踩坑经验、工程化实践、面试话术等十大章节
  - PROJECT_SUMMARY.md 快速启动章节修正：移除手动 db_init 步骤（已自动化）、移除手动下载嵌入模型说明、管理员密码从 admin123 更正为 123456
- **影响文件**:
  - `INTERVIEW_PREP.md` — 新建
  - `PROJECT_SUMMARY.md` — 快速启动章节修正（-5行 +3行）
- **方案**: 面试准备文档独立存放，方便面试前快速回顾，不被其他文档干扰
- **验证**: INTERVIEW_PREP.md 覆盖十大章节，PROJECT_SUMMARY.md 启动步骤与代码实际行为一致

---

### [2026-07-16] 修复: 8 个问题集中修复 + 体验优化
- **类型**: Bug 修复 + 功能开发 + 体验优化
- **问题**: (1) 推荐女装会同时查找男装，缺少性别过滤 (2) 知识库不支持自定义新分类 (3) 分页 20 条/页改为 10 条/页 (4) 无删除对话功能 (5) 登录/注册切换和注册完成后不清空输入框 (6) 切换会话不清空输入框 (7) 不知性别时应分别列出男/女/通用推荐 (8) 新增知识库文档检索不稳定
- **概述**:
  - 知识库文档表新增 `gender` 列（男/女/通用/儿童），19 条种子数据全部标注
  - ChromaDB 元数据新增 gender 字段，`search_similar()` 支持 gender 参数 + `$and` 组合过滤 + 降级回退
  - 管理后台分类输入从 `<select>` 改为下拉框 + "＋新增"按钮组合
  - 知识库分页 `page_size` 从 20 改为 10
  - 新增 `DELETE /sessions/{session_id}` API + 数据库级联删除 + 前端删除按钮
  - 认证页面切换和注册成功后清空所有输入框和错误提示
  - 切换会话时清空聊天输入框
  - `recommend_size()` 性别为空时分别检索男装/女装/通用三类结果，`search_knowledge_base()` 无分类偏好时不过滤
  - `VECTOR_SEARCH_K` 3→5，写入操作后重置向量存储单例确保新文档可见
  - 新增 `migrate_database()` 自动迁移函数（启动时执行，幂等 ALTER TABLE）
  - 刷新页面会话逻辑优化：0 会话自动创建，有会话不自动创建不自动切换
  - 新建 `ISSUES_LOG.md` 问题记录与修复日志
- **影响文件**:
  - 修改: `.env.example`, `config/settings.py`, `db_init.py`, `app/models.py`, `app/database.py`, `app/kb_seed_data.py`, `app/rag.py`, `app/tools.py`, `app/agent.py`, `app/main.py`
  - 新增: `ISSUES_LOG.md`
- **方案**: 以 gender 字段作为性别过滤核心，贯穿数据库 → ChromaDB 元数据 → 检索函数 → Agent 提示词全链路；检索优化双管齐下（增大 k 值 + 写入后重置单例），确保新文档即时可见
- **验证**: 全链路性别过滤生效，自定义分类可正常创建和保存，删除会话级联正确，表单清空逻辑覆盖所有切换场景，性别未知时分三类展示推荐结果

---

## 待办事项

| 优先级 | 内容 | 状态 |
|--------|------|------|
| P1 | ~~多轮对话上下文记忆~~ | 已完成（chat_sessions 实现） |
| P1 | ~~聊天页面历史记录保存~~ | 已完成 |
| P2 | 增加"退换货处理"工具 | 待开发 |
| P2 | ~~用户认证（登录后只能查自己订单）~~ | 已完成（JWT + bcrypt） |
| P3 | 部署到服务器/云平台 | 待规划 |
| P3 | 对接企业微信/飞书等 IM | 待规划 |

---

## 记录模板

> 新记录使用此模板，保持格式统一：

```markdown
### [YYYY-MM-DD HH:MM] 类型: 简短标题
- **类型**: 功能开发 / Bug 修复 / 体验优化 / 基础设施 / 重构
- **问题**: (为什么要改)
- **概述**: (做了什么)
- **影响文件**: 列出所有改动文件
- **方案**: 关键设计决策
- **验证**: 如何确认修改有效
```
