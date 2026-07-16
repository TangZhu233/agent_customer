# 问题记录与修复日志

> 记录每次用户提出的问题及修复方案，方便追溯和回顾。

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

## 历史批次

（待后续补充）
