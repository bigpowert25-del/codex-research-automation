# Codex 收藏研究自动化

一个完全本地的原型：把人工提供的收藏记录放进收件箱，先做规范化、去重、分类和优先级建议；经过人工批准、修改或拒绝后，再输出证据化研究记录和项目 brief。

它对应收藏 [Codex 自动化工作流](https://www.douyin.com/video/7653472411480864767)，但不会抓取该页面，也不会接触抖音登录态。

## 已跑通的流程

```text
JSON/JSONL 收件箱
  → 链接规范化 + URL/内容去重
  → project/tutorial/reference/tool 分类建议
  → review-queue.json（强制人工确认点）
  → decisions（approve/edit/reject）
  → 分类 JSON + 研究记录 + project 类 brief + 总索引
  → 自动完整性验证
```

运行只依赖 Python 3.11+ 标准库，不需要安装第三方包。

## 立即运行内置样例

在本目录执行：

```bash
python3 automation.py run \
  --input samples/inbox.jsonl \
  --decisions samples/review-decisions.json \
  --output example-output \
  --run-id sample-manual
```

已提交的同一份验证产物位于 `example-output/runs/sample-manual/`。再次执行时请换一个 `--run-id`，因为 run 是只追加的审计记录，不会覆盖旧结果。

单独复验已有产物：

```bash
python3 automation.py verify --run-dir example-output/runs/sample-manual
python3 -m unittest discover -s tests -v
```

## 真实数据的两阶段手动触发

### 1. 准备待确认队列

将人工导出的收藏逐行写入 `inbox/inbox.jsonl`，然后运行：

```bash
python3 automation.py prepare --run-id 20260715-manual-01
```

结果默认写入 `output/runs/<run-id>/`。此时状态是 `awaiting_human_review`，不会生成研究记录或 brief。

### 2. 人工确认后发布

查看该 run 下的 `review/review-queue.json`，参照 `samples/review-decisions.json` 制作 decisions 文件。每一项决策必须有：

- `item_id`
- `action`：`approve`、`edit` 或 `reject`
- `reviewer`
- `decided_at`
- 可选 `overrides`：允许修改 `title`、`category`、`priority`

然后手动运行：

```bash
python3 automation.py publish \
  --run-dir output/runs/20260715-manual-01 \
  --decisions /path/to/human-decisions.json
```

没有出现在 decisions 中的条目继续保持 pending，不会被偷偷批准。已经发布的 run 也不能原地覆盖；修改决策需新建 run，以保留审计链。

## 收件箱数据格式

`inbox.jsonl` 每行一个 JSON 对象：

```json
{
  "title": "收藏标题",
  "source_url": "https://example.com/original",
  "captured_at": "2026-07-15T10:00:00+08:00",
  "notes": "为什么收藏、准备怎么用",
  "tags": ["项目", "自动化"],
  "category_hint": "project",
  "evidence": [
    {
      "type": "source_observation",
      "claim": "这条证据能支持的最小判断",
      "locator": "人工截图/第2段/00:18",
      "strength": "high",
      "quote": "可选的短摘录"
    }
  ]
}
```

必填字段是 `title`、`source_url`。`source_url` 必须是 HTTP(S)，跟踪参数会在去重时移除，但原始链接仍保留在产物中。`category_hint` 可选，值只能是 `project`、`tutorial`、`reference`、`tool`；它只是高置信建议，不绕过人工确认。

每条 evidence 至少包含 `type`、`claim`、`locator`；`strength` 是 `low` / `medium` / `high`。允许暂时没有证据，但产物会明确标为证据缺口，不能把推测写成事实。

## 输出目录

每次 run 都有独立目录：

```text
output/
├── state/dedup-index.json              # 跨 run 去重索引
└── runs/<run-id>/
    ├── inbox/normalized-items.json
    ├── duplicates/duplicate-items.json
    ├── review/review-queue.json
    ├── review/applied-decisions.json
    ├── categorized/
    │   ├── project/*.json
    │   ├── tutorial/*.json
    │   ├── reference/*.json
    │   └── tool/*.json
    ├── research/*.md
    ├── briefs/*.md                     # 仅 project 类
    ├── rejected/rejected-items.json
    ├── index.md
    ├── run-manifest.json
    └── verification-report.json
```

分类记录始终保留原始来源链接、证据、优先级、最终输出目录、确认人和确认时间。

## 调度接入（当前未启用）

`config/automation.json` 和 `prompts/schedule-ready.md` 已经可以交给 cron、launchd 或 Codex Automation 的上层调度器，但本项目**没有创建真实定时任务**。

建议调度只执行：

```bash
python3 automation.py prepare
```

并把运行结果通知给人。配置强制：

- `schedule.enabled=false`
- `schedule.mode=prepare_only`
- `pipeline.require_human_confirmation=true`
- `research.remote_fetch=false`

运行 `python3 automation.py check-config` 可检查这些边界。要真正接入调度时，先由用户在新任务中明确批准具体频率、输入来源、通知方式和失败策略；即使批准调度，也建议继续停在 review queue，不自动 publish。

## 人工确认点

1. 输入前：由人决定哪些收藏进入本地 JSONL，并填写可定位证据。
2. 分类后：人对每项执行 approve / edit / reject。
3. 研究后：人打开原始链接复核；程序只整理本地证据，不声称验证了网页。
4. 开工前：人确认 project brief 的用户、范围、非目标和验收标准。

## 安全边界

- 不远端抓取，不自动打开来源链接，不搜索补全内容。
- 不登录、不读取 Cookie、不改动抖音或其他账号。
- 不自动创建真实定时任务。
- 不执行发布、发信、购买、评论等外部写入。
- 不存储凭据；输入中也不应放 Cookie、Token 或密码。
- 人工 decisions 是发布研究/brief 的唯一入口；样例中的 `sample-human` 仅用于演示完整流程。
- 去重索引和所有业务产物都只写入本项目选择的输出目录。

## 设计取舍

原型使用文件而不是数据库，方便审计、版本控制和手工修正。跨 run 去重采用“规范化来源链接 + 标题/备注内容指纹”；它能拦住常见重复分享，但不会做模糊语义合并，以免误删不同收藏。真正需要网页事实时必须由人在任务内另行批准访问，并把新的证据连同定位信息写回收件箱。

## 测试与许可证

```bash
python3 -m unittest discover -s tests -v
python3 automation.py verify --run-dir example-output/runs/sample-manual
```

项目原创代码采用 [MIT License](./LICENSE)。GitHub Actions 会在 Python 3.11 环境中重复运行测试和样例验证。
