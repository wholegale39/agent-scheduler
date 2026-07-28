# Agent Task Scheduler · Agent 任务调度器

轻量级任务调度器——比 cron 更聪明，专为 Agent 任务设计。任务用自然语言 prompt 定义，支持依赖链编排、失败重试、多平台投递。

## 为什么做这个？

我们用 Hermes Agent 跑定时任务（收盘汇总→画图→持仓汇总），原生 cron 的痛点：

- ❌ 没有依赖链 — 得手动算时间错开
- ❌ prompt 任务和 shell 脚本混着管
- ❌ 失败了不会自动重试
- ❌ 投递目标得各任务自己处理

这个调度器把这些问题一次性解决。

## 快速开始

```bash
git clone https://github.com/wholegale39/agent-scheduler.git
cd agent-scheduler

# 安装
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 配好你的 LLM API Key
export LLM_API_KEY="sk-..."

# 启动
python3 -m uvicorn src.main:app --host 0.0.0.0 --port 8767
```

## 定义任务

YAML 文件写任务定义，服务启动自动加载：

```yaml
# tasks/my-tasks.yaml
- name: market-summary
  description: "全球市场收盘汇总"
  type: prompt                # prompt | script | webhook
  schedule: "0 18 * * 1-5"    # cron 表达式或 "every 30m"
  prompt: |
    生成全球市场收盘汇总，包含 A股、港股、亚太、美股、欧洲、外汇。
  llm:
    provider: deepseek
    model: deepseek-chat
    system_prompt: "你是金融数据分析师"
  retry:
    max_attempts: 3

- name: gen-chart
  depends_on: [market-summary]  # 依赖链！
  type: script
  script: "/opt/data/scripts/gen_chart.sh"
  deliver:
    - platform: stdout
      target: console
```

## 特性

| 特性 | 说明 |
|------|------|
| **三种任务类型** | prompt（LLM 调用）、script（shell 命令）、webhook（HTTP 请求） |
| **依赖链** | 声明 `depends_on`，自动拓扑排序，下游任务接力执行 |
| **日程调度** | cron 表达式 / interval（`every 30m`）/ 手动触发 |
| **失败重试** | 指数退避 + 连续失败告警 |
| **多平台投递** | Feishu / Telegram / stdout / HTTP webhook |
| **LLM 原生集成** | 直接配 model + provider + system_prompt |
| **YAML 定义** | 任务文件放 `tasks/` 目录，启动时自动加载 |
| **REST API** | 完整的 CRUD + 手动执行接口 |

## API

| Method | Path | 说明 |
|--------|------|------|
| `GET` | `/health` | 健康检查 |
| `GET` | `/tasks` | 任务列表与状态 |
| `POST` | `/tasks` | 创建/更新任务 |
| `DELETE` | `/tasks/{name}` | 删除任务 |
| `POST` | `/tasks/{name}/run` | 手动触发执行 |

## 配置

```yaml
# config.yaml
db_path: data/scheduler.db
tasks_dir: tasks
api_port: 8767
default_llm:
  provider: deepseek
  model: deepseek-chat
  api_key_env: LLM_API_KEY
alert_on_failure_after: 3
alert_deliver:
  - platform: stdout
    target: console
```

## 架构

```
FastAPI (8767)
  └─ APScheduler — cron/interval 触发器
       └─ 依赖拓扑排序
            └─ Executor — 执行 prompt/script/webhook
                 └─ Deliver — 投递到各平台
                      └─ SQLite — 持久化状态+历史
```

## 许可证

MIT
