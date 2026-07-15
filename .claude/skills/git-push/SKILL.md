---
name: git-push
description: 一键Git提交推送：自动add、commit、push。当你需要提交并推送代码时调用。
allowed-tools: Bash(git add:*), Bash(git status:*), Bash(git commit:*), Bash(git push:*)
---

执行以下步骤：

1. `git status` — 展示当前改动
2. 如果用户没有附带提交信息，询问 commit message
3. 确认 `.env` 和 `*.db` 不在暂存区
4. 执行：
   ```bash
   git add .
   git commit -m "<提交信息>"
   git push
   ```
5. 报告结果（成功/失败）

## 约束
- 禁止 `git push -f`
- 推送前确保 .env 不在暂存区
- 如果 push 被拒绝，提示用户先 `git pull`
