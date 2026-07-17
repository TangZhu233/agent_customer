# 问题记录与修复日志

> 记录每次用户提出的问题及修复方案，方便追溯和回顾。

---

## 批次 #4 — 2026-07-17（`split('\n')` 遗留换行 → JS 语法错误，登录/注册再次失效）

### 现象
用户报告注册按钮无法点击，输入用户名密码登录无响应。

### 排查
1. 后端 API 正常（`/auth/login` → 200, `/auth/register` → 200）
2. 浏览器端 JS 语法错误会导致**整个 `<script>` 块全部瘫痪**
3. 用 `node --check` 检查提取的 JS → 报 `Invalid or unexpected token` 定位到 `send()` 函数内 `buffer.split('` 行

### 根因
`main.py` 的 `CHAT_PAGE` raw string（`r"""..."""`）中，`send()` 函数第 1138-1139 行的 NDJSON 解析代码存在**字面换行符**：

```javascript
// 错误（Python raw string 中 \n 被写成字面换行）
const lines = buffer.split('
');
// → JS 解析器在 ' 后遇到裸换行 → SyntaxError
```

这和批次 #3 是同一类问题（Python raw string 中的字面换行混入 JS 字符串），但批次 #3 只修复了 `replace(/\n/g` 和括号问题，**遗漏了这个 `split` 处的字面换行**。

`BENCHMARK_PAGE` 对应位置（第 1937 行）使用了正确的 `'\n'` 转义，不受影响。

### 修复
`main.py` 第 1138-1139 行：`buffer.split('` + `字面换行` + `');` → `buffer.split('\n');`

Python raw string 中写 `\n` → 输出为两个字面字符 `\` `n` → JS 正确解析为换行转义序列。

### 验证
- `node --check` 语法检查通过
- JS 括号 143/143 平衡
- `/auth/login` API 正常返回 JWT
- `/auth/register` API 正常注册新用户


## 批次 #3 — 2026-07-17（流式对话 JS 注入导致登录失效）

### 现象
用户报告登录页面无法登录——输入用户名密码点击登录完全没反应。

### 排查
1. 后端 `/auth/login` API 正常返回 200 + JWT token
2. 前端页面 `<script>` 块中发现 JS 语法错误：
   - `}` 比 `{` 少 3 个（143 vs 146）
   - 浏览器解析到语法错误后，**整个 `<script>` 块全部瘫痪**——不仅是 `handleLogin`，页面所有函数都不可用

### 根因
流式对话的 JS 辅助函数通过 Python 脚本字符串注入 `main.py` 的 HTML 模板（Python raw string `r"""..."""`）时，产生了 3 个语法错误：

| # | 位置 | 错误 | 原因 |
|---|------|------|------|
| 1 | `finalizeStreamingMsg` 内 `replace(/\n/g` | `\n` 被 Python 解析为真实换行，正则裂成两行 | Python 脚本用普通字符串（非 raw），`\n`→换行 |
| 2 | `hideLoading` 与 streaming helpers 之间 | 旧 `async function send() {` 头残留，`{` 无闭合 | 字符串替换边界裁剪偏差 |
| 3 | `finalizeStreamingMsg` 结尾 | 函数结束 `}` 丢失 | 替换时末尾一行被截断 |

### 修复
1. `replace(/\n/g` → `replace(/\\n/g`（Python 层 `\\n`→JS 层 `\n`）
2. 删除残留的旧 `send()` 头
3. 补回丢失的 `}` + `scrollTop` 行
4. 修复 `document.getElementByasync function send()` 拼接错误

### 教训
- 对 Python raw string 做自动化替换时，**必须 dump 到文件检查**，不能只信任脚本输出
- JS 语法错误会让整个 `<script>` 块静默失效——排查时第一步就应检查浏览器控制台
- Python shell 转义 (`python -c`) + raw string 内容 = 灾难，应写独立 `.py` 脚本操作文件


## 批次 #2 — 2026-07-17（langgraph 升级导致依赖冲突）

### 现象
安装 `langgraph` 后，`langchain-core` 从 0.3.x 被升级到 1.4.x，`langchain`、`langchain-deepseek`、`langchain-community`、`langchain-chroma` 全部被卸载。

### 修复
1. 换清华镜像源 (`pypi.tuna.tsinghua.edu.cn`) 重装所有依赖
2. `requirements.txt` 版本号更新为 `>=1.0.0`，新增 `langgraph>=1.0.0`


---

## 批次 #1 — 2026-07-16

### 问题清单

| # | 问题描述 | 优先级 | 状态 |
|---|---------|--------|------|
| 1 | 推荐女装时会同时查找男装，缺少性别过滤 | P1 | ✅ 已修复 |
| 2 | 知识库文档不支持自定义添加新分类 | P2 | ✅ 已修复 |
| 3 | 知识库分页未按要求10条/页（原20条/页） | P2 | ✅ 已修复 |
| 4 | 没有删除对话功能 | P1 | ✅ 已修复 |
| 5 | 登录/注册切换和注册完成后不清空输入框 | P3 | ✅ 已修复 |
| 6 | 切换会话不清空输入框 | P3 | ✅ 已修复 |
| 7 | 不知道性别时应分别列出男/女/通用推荐 | P1 | ✅ 已修复 |
| 8 | 新增知识库文档检索不稳定 | P1 | ✅ 已修复 |

### 修复详情

#### 问题1: 性别过滤
- **根因**: `search_similar()` 只支持 category 过滤，缺少 gender 维度的元数据过滤
- **修复**:
  - `documents` 表新增 `gender` 列（男/女/通用/儿童）
  - ChromaDB 元数据新增 `gender` 字段
  - `search_similar()` 新增 `gender` 参数用于过滤
  - `recommend_size()` 和 `search_knowledge_base()` 传递性别参数
  - 种子数据全部标注性别
  - 管理后台新增性别选择字段
- **影响文件**: `db_init.py`, `app/models.py`, `app/database.py`, `app/kb_seed_data.py`, `app/rag.py`, `app/tools.py`, `app/agent.py`, `app/main.py`

#### 问题2: 自定义分类
- **根因**: 分类下拉框只从已有分类加载，无法输入新分类
- **修复**: 将分类 `<select>` 改为 `<input>` + `<datalist>` 组合框，支持选择已有或输入新分类
- **影响文件**: `app/main.py`（HTML 前端）

#### 问题3: 分页改为10条/页
- **修复**: 前端 `loadDocs()` 中 `page_size` 从 20 改为 10
- **影响文件**: `app/main.py`（HTML 前端）

#### 问题4: 删除对话
- **修复**:
  - `database.py` 新增 `delete_session()` 函数（级联删除消息）
  - `main.py` 新增 `DELETE /sessions/{session_id}` API
  - 前端会话列表新增删除按钮（✕）
- **影响文件**: `app/database.py`, `app/main.py`

#### 问题5: 清空认证表单
- **修复**: `switchAuthTab()` 切换时清空所有输入框；`handleRegister()` 成功后清空注册表单
- **影响文件**: `app/main.py`（HTML 前端）

#### 问题6: 切换会话清空输入框
- **修复**: `switchSession()` 中追加 `document.getElementById('inp').value = ''`
- **影响文件**: `app/main.py`（HTML 前端）

#### 问题7: 性别未知推荐
- **修复**: `recommend_size()` 当 gender 为空/未知时，分别检索男装、女装、通用尺码并合并结果
- **影响文件**: `app/tools.py`

#### 问题8: 检索优化
- **根因**: ① 检索 k 值偏小（3）容易遗漏新文档；② 写入后 ChromaDB 客户端缓存可能导致新文档不可见
- **修复**:
  - `VECTOR_SEARCH_K` 从 3 提升到 5
  - 写入操作后重置向量存储单例，确保下次检索从磁盘重新加载
  - `search_knowledge_base` 中当用户无分类偏好时不做过滤，扩大检索面
- **影响文件**: `.env.example`, `config/settings.py`, `app/rag.py`, `app/tools.py`

---

## 批次 #2 — 2026-07-16

### 问题清单

| # | 问题描述 | 优先级 | 状态 |
|---|---------|--------|------|
| 9 | 种子订单数据为电子产品，与服装电商定位不符 | P2 | ✅ 已修复 |
| 10 | users 与 auth_users 表分裂，订单无法关联用户名/手机号 | P1 | ✅ 已修复 |
| 11 | 管理后台仅有文档管理，缺少用户/订单管理页面 | P2 | ✅ 已修复 |
| 12 | 知识库「产品信息」缺少具体服装 SKU 数据 | P2 | ✅ 已修复 |
| 13 | 用户查询返回 email 字段，不需要 | P3 | ✅ 已修复 |
| 14 | 客服查订单时不显示用户名和手机号 | P1 | ✅ 已修复 |

### 修复详情

#### 问题9: 种子订单数据域不匹配
- **根因**: `db_init.py` 中 8 条订单全部为 Apple 电子产品（iPhone/MacBook/iPad 等），与「服装电商智能客服」定位完全不符
- **修复**: 将 8 条订单全部替换为服装商品：羊毛混纺大衣 ¥1599、纯棉T恤 ¥129、真丝连衣裙 ¥899、莫代尔打底衫 ¥99、商务西裤 ¥399、弹力牛仔小脚裤 ¥259、冰丝防晒外套 ¥299、天丝亚麻阔腿裤 ¥329
- **影响文件**: `db_init.py`

#### 问题10: users / auth_users 表合并
- **根因**: 业务客户表 `users`（name/phone/email）和登录认证表 `auth_users`（username/password_hash/is_admin）是两张独立的表，没有关联。`orders.user_id` → `users.id`，`chat_sessions.user_id` → `auth_users.id`，导致查订单时无法获取注册用户名
- **修复**:
  - 合并为统一 `users` 表（id/username/password_hash/phone/is_admin/created_at）
  - 去掉 `name` 和 `email` 字段
  - 表数量从 7 减为 6
  - `migrate_database()` 自动检测旧 schema (auth_users 表或 users.name 列) 并重建
  - 数据库函数重命名: `create_auth_user` → `create_user`, `get_auth_user_by_username` → `get_user_by_username`, `get_auth_user_by_id` → `get_user_by_id`
  - 种子用户 6 人: admin(管理员)/testuser/lihua/wangfang/zhangwei/chenjing
- **影响文件**: `db_init.py`, `app/database.py`, `app/models.py`, `app/main.py`

#### 问题11: 管理后台扩展
- **根因**: 管理后台 (`#page-admin`) 只有一个文档管理标签，`adminSwitchTab` 是空函数 stub，注释写着「当前只有文档管理，预留扩展」
- **修复**:
  - 新增 `GET /admin/users`、`GET /admin/orders`、`GET /admin/orders/{order_id}` 三个 API
  - 前端侧边栏增加三个标签：👥用户管理 / 📦订单管理 / 📄文档管理
  - `adminSwitchTab` 实现动态面板切换、侧边栏高亮、顶部标题自适应
  - 新增 `loadUsers()` 和 `loadOrders()` 函数渲染数据表格
  - 订单表显示用户名 + 手机号（通过 JOIN 查询）
- **影响文件**: `app/main.py`, `app/models.py`, `app/database.py`

#### 问题12: 知识库产品 SKU 数据补充
- **根因**: 知识库「产品信息」分类仅有面料特性、新品系列、羽绒服选购 3 篇通用内容，缺少具体商品 SKU 参数（面料成分、尺码范围、价格、颜色等），Agent 无法回答具体产品咨询
- **修复**: 新增 8 篇产品 SKU 文档（知识库从 19 篇增至 27 篇）：
  - 夏季纯棉T恤系列、商务休闲西裤、真丝连衣裙春夏系列、防晒外套/皮肤衣选购参数
  - 羊毛混纺大衣秋冬系列、弹力牛仔下装系列、天丝亚麻混纺透气系列、莫代尔居家内衣系列
  - 每篇含 SKU 格式、面料成分、尺码范围、价格区间、颜色选项、洗涤要求
- **影响文件**: `app/kb_seed_data.py`, `app/agent.py`

#### 问题13: 移除 email 字段
- **修复**:
  - `users` 表去掉 `email` 列
  - `tools.py` 中 `lookup_user_by_phone` 返回值移除 email，`name` → `username`
  - 前端用户管理表格不显示 email 列
- **影响文件**: `db_init.py`, `app/tools.py`, `app/models.py`

#### 问题14: 订单查询显示用户名和手机号
- **根因**: `query_orders_by_user_id` 和 `query_order_by_no` 只查 `orders` 表，不关联用户信息
- **修复**:
  - 两个查询函数均 JOIN `users` 表，返回 username + phone
  - `tools.py` 工具返回字符串中订单头部和详情均包含用户名+手机号
  - 管理后台新增 `list_all_orders()` 和 `get_order_by_id()` 同样 JOIN
  - **设计决策**: 不在 orders 表冗余存储 username/phone，仅保留 user_id FK，通过 JOIN 获取（避免数据不一致）
- **影响文件**: `app/database.py`, `app/tools.py`, `app/models.py`, `app/main.py`

---

## 批次 #3 — 2026-07-17

### 问题清单

| # | 问题描述 | 优先级 | 状态 |
|---|---------|--------|------|
| 15 | 用户可用他人手机号直接查订单/物流，缺少隐私保护 | P1 | ✅ 已修复 |

### 修复详情

#### 问题15: 手机号查他人订单缺少验证

- **场景**: 登录用户提供非本人手机号 → Agent 可直接调用 `lookup_orders_by_user_id` 列出该手机号对应的所有订单（含金额/商品/状态），并能顺藤摸瓜查物流。现实中帮朋友查快递需要订单编号作为授权凭证，只给手机号不应放行。
- **根因**: 三个工具函数（`lookup_orders_by_user_id` / `lookup_order_by_no` / `lookup_logistics`）均为纯业务查询，不感知"当前登录用户是谁"，无法区分"查自己的"和"查别人的"。
- **修复（v2 — 交叉校验）**:
  - 新增三个 ContextVar：`_current_user_id`（当前登录用户）、`_target_user_id`（手机号锁定的目标用户）、`_verified_order_ids`（已验证订单白名单）
  - `lookup_user_by_phone`：查到他人时自动设置 `_target_user_id`，锁定查询目标
  - `lookup_orders_by_user_id`：他人 → 拦截，要求提供订单编号
  - `lookup_order_by_no`：**双重校验**——若 `_target_user_id` 已锁定，订单必须归属该用户（手机号与订单号交叉校验），否则就算订单存在也拒绝；无锁定时订单编号自身即为核心凭证
  - `lookup_logistics`：**双重校验**——优先检查 `_target_user_id` 锁定 → 其次检查 `_verified_order_ids` 白名单 → 最后检查是否本人订单
  - `agent.py` 的 `chat()` 每次请求开始时初始化上下文；SYSTEM_PROMPT 新增隐私保护规则
  - 匿名用户不受影响（所有校验在 `_current_user_id` 为 None 时跳过）
- **设计原则**: 
  - 手机号 → 查自己全放行，查他人锁目标
  - 订单编号 → 必须跟手机号锁定的人对上，对不上就拒绝（防止用 Bob 的手机号 + Charlie 的订单号绕过）
  - 直接给订单编号（无手机号）→ 订单编号即凭证
- **影响文件**: `app/tools.py`, `app/agent.py`

---

## 历史批次

（待后续补充）
