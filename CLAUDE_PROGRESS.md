# 开发进度追踪

> 每次完成核心修改后，由 Claude 按模板生成记录，用户确认后保存。
> 新会话启动时通过 `/project-init` 自动加载。

---

## 当前状态

- **版本**: v0.4.1
- **最后更新**: 2026-07-23
- **进行中**: 无
- **待办**: 见下方「待办事项」

---

## 进度时间线

### [2026-07-23] 修复 + 文档: Redis 缓存跨用户泄露修复 + 架构决策文档化

- **类型**: Bug 修复 + 文档
- **问题**:
  1. v0.4.0 的 Redis 缓存在 Agent 层缓存 LLM 最终回复，用户 A 的多轮对话上下文（含 "SKU-2026-001 未找到"）被缓存后，用户 B 单独问同一问题时收到了 A 的上下文回复——跨用户信息泄露
  2. RAG 缓存"没反应"——Agent 层加 `has_history` 守卫后，会话场景下缓存永远跳过（第一条消息后 history 永不为空）
- **概述**:
  - **缓存搬家（Agent 层 → RAG 检索层）**: 删除 `app/agent.py` 中 `chat()` 和 `chat_stream()` 的全部缓存逻辑（~30行），改为在 `app/rag.py` 的 `search_similar()` 中做缓存——RAG 检索是确定性纯函数，同样参数永远返回同样文档列表，与用户身份和对话历史无关
  - **缓存键增强**: `_normalize_query()` 去空白+中文标点+小写化，消除 LLM 措辞变体；`_cache_key()` 包含 retrieval_mode，区分 dense/hybrid；tools.py 中 gender/category 的 "通用" 加入 strip list
  - **三个关键认知**:
    1. 缓存 RAG 结果而非 LLM 回复——RAG 是纯函数，LLM 回复依赖上下文
    2. 缓存在此的首要价值是"一致性"（所有人基于同一知识源）和"去重"（同一查询只检索一次），速度只是副作用
    3. 缓存永远是旁路——所有 Redis 操作 try/except 包裹，挂了不影响服务
  - **架构决策文档化**: README.md 新增「架构决策」章节（多路召回 / 语义缓存 / 重排序取舍 / 熔断器设计），PROJECT_SUMMARY.md 新增 ADR 章节，INTERVIEW_PREP.md 更新至 v0.4.1
- **修改文件**:
  - `app/cache.py` — 完全重写：LLM 回复缓存 → RAG 检索缓存，含 query 归一化
  - `app/rag.py` — `search_similar()` 加 3 步缓存层（查缓存→检索→写缓存 fire-and-forget）
  - `app/agent.py` — 删除 `chat()` 和 `chat_stream()` 中全部缓存逻辑
  - `app/tools.py` — "通用" 加入 gender/category strip list，防止 LLM 参数不一致导致不同缓存键
  - `README.md` — 全面重写：新增「架构决策」章节（5 个 ADR）
  - `PROJECT_SUMMARY.md` — 新增 ADR 章节 + 技术栈补充 + 约束补充
  - `INTERVIEW_PREP.md` — 更新至 v0.4.1
- **方案**: 缓存下沉到确定性层（RAG 检索），Agent 层只负责调用工具和推理。缓存键标准化（query 归一化 + 参数归一化）
- **验证**:
  - 同一 query 两次检索，第二次日志显示 `RAG 缓存命中: rag:cache:...`
  - 跨用户安全：A 和 B 问相同问题，RAG 缓存共享正确，回复各自独立
  - 对话历史不影响：多轮中每轮 RAG 检索独立缓存
  - KB 更新后缓存自动失效（`_invalidate_caches_and_reindex` pattern 已覆盖 `rag:cache:*`）


### [2026-07-22] 功能开发: RAG 检索升级三件套——多路召回 + 熔断器 + Redis 缓存

- **类型**: 功能开发 + 基础设施
- **概述**:
  - **多路召回 + 重排序**: 稠密向量(ChromaDB) + 稀疏关键词(BM25/jieba) → RRF 融合 → 可选 LLM 重排序，`RETRIEVAL_MODE=hybrid` 启用
  - **熔断器**: 三态状态机(CLOSED→OPEN→HALF_OPEN)，包裹 tenacity 重试链，连续失败 5 次后快速失败 30s
  - **Redis 语义缓存**: 基于问题文本 MD5 的精确匹配缓存，命中 <10ms，跨用户跨会话全局共享，含流式支持
- **新增文件**:
  - `app/retrieval.py` — HybridRetriever (342行): BM25Retriever + RRF + LLM Rerank
  - `app/circuit_breaker.py` — CircuitBreaker (193行): 三态状态机 + asyncio.wait_for 超时
  - `app/cache.py` — RedisCache (191行): 连接池 + get/set/invalidate + 优雅降级 (Redis 5.x 需 protocol=2)
- **修改文件**:
  - `app/agent.py` — 熔断器 + Redis 缓存注入 chat() 和 chat_stream() + asyncio.wait_for 超时
  - `app/tools.py` — search_knowledge_base/recommend_size 改为 async
  - `app/rag.py` — search_similar → _dense_search 重命名 + hybrid 路由 + rebuild_bm25_index()
  - `app/main.py` — startup/shutdown 钩子(Redis + BM25索引) + KB CRUD 后缓存失效
  - `config/settings.py` — 新增 15+ 配置项 (CB/REDIS/RETRIEVAL 三组)
  - `.env.example` + `.env` — 补全所有配置段
  - `requirements.txt` — 新增 jieba, rank-bm25, redis
- **配置开关**:
  - `RETRIEVAL_MODE=dense|hybrid` (默认 dense 向后兼容)
  - `CB_ENABLED=true` (默认开)
  - `REDIS_ENABLED=true|false` (默认 false，需 Redis 服务)
  - `RERANK_ENABLED=false` (可选 LLM 重排序)
- **验证**:
  - 非流式 + RAG 对话正常（5条引用）
  - 流式对话正常（100 tokens, 1.9s）
  - Redis 缓存命中 <10ms（加速 99.99%），跨用户跨会话共享
  - 熔断器状态转换日志确认
  - 多路召回 hybrid 模式 BM25 索引构建成功
- **踩坑**:
  - ① agent_customer conda 环境需手动装新依赖（pip install 默认装到了 base 环境）
  - ② Redis 5.0.14.1 Windows 版不支持 RESP3 协议 → 连接池和客户端均需 `protocol=2`
  - ③ 流式 chat_stream() 最初没加缓存，前端默认流式导致缓存形同虚设 → 后补上
  - ④ cb.record_success() 漏写 await → RuntimeWarning → 补上
  - ⑤ 知识库中 SKU 格式为 `T-SU-WH-L` 等，非 `SKU-2026-001`

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
- **验证**: `ssh -T git@github.com` 返回 `Hi TangZhu233!`

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

### [2026-07-16] 修复: 知识库参考引用不显示（gender 过滤空结果降级 + 向量索引自动修复）
- **类型**: Bug 修复
- **问题**: v0.2.1 新增性别过滤后，ChromaDB 旧向量缺失 gender 元数据字段，Agent 按 SYSTEM_PROMPT 传 gender="通用" 时过滤返回空结果，导致知识库参考引用不显示
- **概述**:
  - `search_similar()` 新增空结果降级：gender 过滤返回 0 条时自动去除 gender 重试
  - `reindex_knowledge_base()` 新函数：从 SQLite 完整重建 ChromaDB 索引
  - `startup()` 启动时自动检测并修复缺失 gender 的向量
  - 新增 `POST /admin/kb/reindex` 管理接口，支持手动触发索引重建
- **影响文件**:
  - 修改: `app/rag.py`, `app/main.py`
- **方案**: 三重保障——检索时空结果自动降级（用户无感知）+ 启动时自动修复（一劳永逸）+ 管理后台手动重建（兜底手段），确保 gender 过滤升级后旧向量平滑过渡
- **验证**: gender 过滤空结果时自动降级返回正确文档；启动后自动检测缺失字段并重建索引；管理后台可手动触发重建

---

### [2026-07-16] 重构: 种子数据服装电商化 + users/auth_users 表合并 + 管理后台扩展
- **类型**: 重构 + 功能开发
- **问题**: (1) users（业务客户）和 auth_users（登录认证）两张独立表造成数据冗余与维护困难，订单查询需跨表关联 (2) 种子订单为 Apple 电子产品，与服装电商定位不符 (3) 业务用户表含 email 字段但实际业务仅用手机号 (4) 知识库缺少具体服装 SKU 参数文档 (5) 管理后台缺少用户管理和订单管理功能
- **概述**:
  - `users` 和 `auth_users` 合并为统一 `users` 表：id, username, password_hash, phone, is_admin, created_at；6 个用户种子数据 (admin/testuser/lihua/wangfang/zhangwei/chenjing)
  - 所有 FK (orders.user_id, chat_sessions.user_id, chat_messages.user_id) 指向新统一 users 表
  - 订单种子数据从 Apple 电子产品替换为 8 条服装商品（羊毛大衣/纯棉T恤/真丝连衣裙/莫代尔打底衫/西裤/牛仔小脚裤/防晒外套/天丝亚麻阔腿裤）
  - 去 email 字段，工具层 + API 层统一返回 username + phone
  - ORDER JOIN 查询自动返回 username + phone（规范化设计，订单表不冗余存用户信息）
  - 知识库「产品信息」分类新增 8 篇具体服装 SKU 文档（SKU 格式/面料成分/尺码范围/价格区间/颜色选项/洗涤保养）
  - 管理后台从单标签扩展为三标签：用户管理 / 订单管理 / 文档管理（动态 tab 切换 + 顶部标题自适应）
  - 新增 3 个 API：`GET /admin/users`、`GET /admin/orders`、`GET /admin/orders/{order_id}`
  - `migrate_database()` 改为检测旧 schema (auth_users 表或 users.name 列) → 自动删库 + 删 ChromaDB + 重建
- **影响文件**:
  - 修改: `db_init.py`, `app/database.py`, `app/models.py`, `app/tools.py`, `app/main.py`, `app/kb_seed_data.py`, `app/agent.py`
- **方案**: 统一用户表消除冗余，规范化设计（订单不冗余用户信息，通过 JOIN 获取），旧 schema 自动迁移采用激进策略（删库重建，因为此时无生产数据），管理后台遵循 FastAPI 路由顺序规则（`/admin/orders` 在 `/{order_id}` 之前声明）
- **验证**: db_init.py 成功创建 6 表 + 6 用户 + 8 订单 + 5 物流；`/admin/users` 返回 6 用户（有 phone 无 email）；`/admin/orders` 返回 8 服装订单（JOIN 含 username+phone）；客服对话查用户/订单均正确展示服装业务数据；前端三标签切换 + JS 函数全部就位

---

### [2026-07-17] 体验优化: 登录页为默认入口 + 管理入口重命名
- **类型**: 体验优化
- **问题**: (1) 启动后若 localStorage 有旧 token 会直接跳转聊天/后台，用户期望先看到登录页 (2) 聊天页顶部管理员入口仍叫"知识库管理"，但后台已扩展为用户/订单/文档三标签
- **概述**:
  - `window.onload` 改为始终先显示登录页，有历史 token 则预填用户名方便快速登录
  - 聊天页顶部链接 `⚙️ 知识库管理` → `⚙️ 后台管理`
  - README.md 第 5 步启动命令补充 `conda activate agent_customer`
- **影响文件**: `app/main.py`, `README.md`
- **验证**: 清除 token 后刷新 → 登录页；保留 token 刷新 → 登录页（用户名预填）

---

### [2026-07-17] 安全: 订单/物流查询隐私保护——手机号与订单号交叉校验
- **类型**: 功能开发 / 安全加固
- **问题**: 登录用户用他人手机号可直接列出该手机号下所有订单，并能通过任意订单号查物流——手机号和订单号没有交叉校验，攻击者可用 Bob 的手机号 + Charlie 的订单号绕过验证
- **概述**:
  - `tools.py` 新增三个 ContextVar：`_current_user_id`（当前用户）、`_target_user_id`（手机号锁定目标）、`_verified_order_ids`（已验证订单白名单）
  - `lookup_user_by_phone`：查到他人时锁定 `_target_user_id`
  - `lookup_orders_by_user_id`：查他人 → 拦截要求提供订单编号
  - `lookup_order_by_no`：若 `_target_user_id` 已锁定，订单必须归属该用户（交叉校验），否则拒绝；无锁定时订单编号即为凭证
  - `lookup_logistics`：三重校验——优先 `_target_user_id` 锁定 → 其次已验证白名单 → 最后本人订单检查
  - `agent.py`：`chat()` 每次请求初始化上下文；SYSTEM_PROMPT 新增隐私保护规则
  - 匿名用户不受影响（`_current_user_id` 为 None 时全部跳过）
- **影响文件**: `app/tools.py`, `app/agent.py`, `ISSUES_LOG.md`
- **方案**: 手机号→锁定目标→订单号必须匹配目标，形成完整校验链。手机号+订单号缺一不可，且必须归属于同一人
- **验证**: 四种场景全部通过——查自己放行、查他人+匹配订单放行、查他人+不匹配订单拒绝、直接给订单号放行

---

### [2026-07-17] 功能开发: 流式对话响应 + 压测结果分析 + 性能优化方案

- **类型**: 功能开发 + 性能评估
- **问题**: 用户每次提问需等待 9~17 秒才能看到完整回复（LLM Agent 2~3 次推理串行执行），体验差；压测仪表盘已完成但缺乏系统化的结果分析和优化路线
- **概述**:
  - **压测分析**: 10人 QPS=0.69 P50=8.7s, 100人 QPS=4.03 P50=17.3s, 成功率100%。瓶颈在 DeepSeek API 延迟（每次 LLM 推理 3~7s，每个对话 2~3 次），非应用层代码
  - **流式对话**: `app/agent.py` 新增 `chat_stream()` 异步生成器，用 `agent.astream_events(version="v2")` 实现逐 token 推送。核心技术点：按 `run_id` 分组追踪 LLM 调用，只推送最后一次调用的 token（最终回答），跳过工具选择阶段的内部推理；新 LLM 轮次开始时发送 `clear` 事件让前端丢弃中间内容
  - **前端流式渲染**: `send()` 改用 `fetch + ReadableStream + NDJSON`，`createStreamingMsg()` 创建空消息气泡，`finalizeStreamingMsg()` 在流结束后渲染引用。首字节延迟从 9,330ms 降至 2,051ms（感知提升 4.5x）
  - **消息持久化**: 流式端点内收集完整回复，流结束后自动保存到 SQLite
  - **优化路线图**: 流式→Prompt Caching→Semaphore控流→语义缓存→并行工具调用（五阶段，ROI 递减）
  - `ChatRequest` 新增 `stream: bool = False`，向后兼容
  - `requirements.txt` 新增 `langgraph>=1.0.0`

- **影响文件**:
  - 修改: `app/agent.py`, `app/models.py`, `app/main.py`, `requirements.txt`
- **方案**: NDJSON（Newline Delimited JSON）一行一个事件，与压测端点格式统一。ReAct Agent 至少 2 次 LLM 调用，按 run_id 智能过滤只推送最终回答的 token
- **验证**: 流式首字节 2,051ms（↓78%）, 非流式向后兼容 9,330ms, 前端 JS 括号平衡, 登录/注册/会话/管理后台全链路正常工作

---

### [2026-07-17] 修复: 流式对话实现过程中的 3 个 JS 语法错误

- **类型**: Bug 修复
- **问题**: 流式对话代码通过 Python 脚本注入 `main.py` HTML 模板时，Python 字符串转义导致 JS 产生三个语法错误：(1) `\n` 正则被解析为真实换行，`replace(/\n/g` 断裂为两行 (2) 旧 `send()` 函数头 `async function send() {` 未完全删除，残留无闭合的 `{` (3) `finalizeStreamingMsg` 函数结尾 `}` 丢失
- **概述**: 三个错误叠加导致 JS `{` 比 `}` 多 3 个，浏览器解析失败，整个 `<script>` 块全部瘫痪，页面所有交互（登录/注册/聊天/管理后台）不可用
- **影响文件**: `app/main.py`
- **方案**: 用 Python 脚本逐一定位修复（主文件为 Python raw string `r"""..."""`，不能用 Edit 工具跨行匹配），最终 JS 括号计数 `{143}` vs `{143}` 完全平衡
- **验证**: 登录 API 200, 页面所有 JS 函数完整, 流式首 token 971ms, 全链路测试通过


### [2026-07-20] 基础设施: 对话历史可配置化 + Token 安全裁剪

- **类型**: 基础设施
- **问题**: 历史消息数硬编码 `history[-20:]`，不可配置；无 token 预算安全网，单条超长回复可撑爆上下文
- **概述**:
  - `config/settings.py` 新增 `MAX_HISTORY_MESSAGES`（默认 20）+ `MAX_HISTORY_CHAR_LIMIT`（默认 80000）两个配置项
  - `app/database.py` 的 `get_session_messages()` 新增 `limit` 参数，SQL 层 `ORDER BY created_at DESC LIMIT ?`，避免拉全量再 Python 切片
  - `app/agent.py:_build_messages()` 两阶段截断：阶段 A 按消息数（DB LIMIT）→ 阶段 B 按字符数安全网（`len()` 估算 token，超阈值从最早消息丢弃，system prompt + 当前用户消息永不丢）
  - `.env.example` 新增对话历史配置段
  - 方案：轮数截断（非语义压缩），客服场景对话浅而窄(3~5轮)，语义压缩额外 LLM 调用反而增加延迟
- **影响文件**: `config/settings.py`, `app/database.py`, `app/agent.py`, `.env.example`
- **方案**: 两阶段截断——消息数截断是常态路径（默认 20 条不变），字符数安全网只兜底长回复边缘 case。用 `len()` 而非 tokenizer 库（零依赖，保守估计偏安全方向）
- **验证**: 默认行为不变（MAX_HISTORY_MESSAGES=20 等价于原硬编码）；降低 MAX_HISTORY_CHAR_LIMIT=500 触发裁剪日志；匿名用户无历史正常对话


### [2026-07-21] 修复: API 限流空壳补全 + Agent 死循环防护

- **类型**: Bug 修复
- **问题**: (1) settings.py 和 .env.example 中 RATE_LIMIT 配置存在，但代码中从未注册 slowapi 限流器，/chat 接口零限流保护 (2) Agent 无 recursion_limit，理论上可无限循环调用工具烧 token
- **概述**:
  - `app/middleware.py` — 创建 `Limiter(key_func=get_remote_address)` + 注册 `SlowAPIMiddleware`
  - `app/main.py` — `app.state.limiter = limiter` + `/chat` 加 `@limiter.limit("60/minute")` + 429 友好提示 + `request: Request` 参数
  - `app/agent.py` — `ainvoke` 和 `astream_events` 均传入 `config={"recursion_limit": 10}` + catch `GraphRecursionError` 返回友好提示
  - `ISSUES_LOG.md` — 新增批次 #5 记录
  - `PROJECT_SUMMARY.md` — 版本同步至 v0.3.3
- **影响文件**: `app/middleware.py`, `app/main.py`, `app/agent.py`, `ISSUES_LOG.md`, `PROJECT_SUMMARY.md`
- **方案**: 限流用 slowapi（已在 requirements.txt 中），IP 级 60 次/分钟；递归限制 10 步（客服正常 2~4 步），超限 LangGraph 抛 GraphRecursionError 被 catch
- **验证**: /health 200, /chat 200, 限流器就位不再 500, recursion_limit 导入正常
- **踩坑**: ① recursion_limit 是 ainvoke config 参数不是 create_react_agent 参数 ② slowapi 需要 app.state.limiter 否则全部 500 ③ @limiter.limit 要求路由函数有 request 参数


## 待办事项

| 优先级 | 内容 | 状态 |
|--------|------|------|
| P1 | ~~流式对话响应~~ | 已完成（v0.3.1） |
| P1 | ~~压测仪表盘 + 结果分析~~ | 已完成 |
| P2 | Prompt Caching（DeepSeek API 侧缓存系统提示词） | 待开发 |
| P2 | 增加"退换货处理"工具 | 待开发 |
| P2 | ~~用户认证（登录后只能查自己订单）~~ | 已完成（JWT + bcrypt） |
| P3 | ~~语义缓存~~ | 已完成（RAG 检索层缓存，v0.4.1） |
| P3 | ~~多路召回 (BM25 混合检索)~~ | 已完成（v0.4.0） |
| P3 | ~~LLM 熔断机制~~ | 已完成（v0.4.0） |
| P3 | 部署到服务器/云平台 | 待规划 |
| P3 | 对接企业微信/飞书等 IM | 待规划 |

---

## 防御性检查清单

> 以下约束由历次 Bug 修复提炼而来，每次修改相关代码后必须执行。

| # | 触发条件 | 检查项 | 来源 |
|---|---------|--------|------|
| 1 | 用户提出技术方案/选型建议时 | **先质疑再验证**：不要默认用户是对的。结合项目实际场景（客服 Agent，非通用助手）判断方案是否过重、是否与企业实际落地做法一致。用户说"企业大部分是轮数截断"后又改口"语义压缩"——前后矛盾，应该指出而非两头附和。复盘：客服场景对话浅而窄(3~5轮)，语义压缩需额外LLM调用反而增加延迟和成本，轮数截断才是正解 | 2026-07-20 用户反馈：Claude 无判断力，两头附和 |
| 2 | 修改 `CHAT_PAGE` 或 `BENCHMARK_PAGE` raw string 内的 JavaScript | `curl -s http://localhost:8000/ -o /tmp/page.html && python -c "..."` 提取 `<script>` 内容 → `node --check` 验证语法无误 | 批次 #3 + #4：Python raw string 中的字面换行导致 JS 语法错误，整个页面 JS 瘫痪 |

执行方法（Windows 环境）：
```bash
# 修改 main.py 后，验证聊天页 JS 语法
curl -s http://localhost:8000/ -o ~/page.html
python -c "
content = open('C:/Users/Administrator/page.html','r',encoding='utf-8').read()
s = content.find('<script>'); e = content.find('</script>')
open('C:/Users/Administrator/_check.js','w',encoding='utf-8').write(content[s+8:e])
"
node --check C:/Users/Administrator/_check.js && echo "OK" || echo "FAIL"
```

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
