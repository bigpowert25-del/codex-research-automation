# Schedule-ready 提示词（仅准备队列）

你正在运行“Codex 收藏研究自动化”的本地准备阶段。

## 目标

读取项目目录内 `inbox/inbox.jsonl` 中由用户主动提供的新收藏，执行字段校验、链接规范化、去重、四分类建议和优先级建议，并生成待人工确认队列。

## 必须执行

1. 在项目目录运行：`python3 automation.py prepare`。
2. 返回本轮 `run_dir`、收到数量、去重数量和待确认数量。
3. 提醒用户编辑该 run 下的 `review/review-queue.json` 所对应的 decisions 文件，再手动运行 `publish`。

## 禁止事项

- 不访问、抓取或搜索任何来源链接。
- 不登录、不读取 Cookie、不操作或修改抖音及其他远端账号。
- 不替用户自动批准、拒绝或修改分类。
- 不运行 `publish`，除非当前任务中用户明确提供了人工 decisions 文件并要求发布。
- 不创建或修改真实 cron、launchd、Codex Automation 等调度任务。
- 不把没有证据的推测写成事实。

## 预期结果

调度运行只停在 `awaiting_human_review`；研究记录与项目 brief 必须等人工确认后生成。
