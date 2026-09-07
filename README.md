# Chatbot

一个保持简单、可测试、可替换的异步聊天机器人。当前支持固定回复的 `MockModel`，以及通过 Chat Completions 协议调用 Ollama、Packy 等服务的 `OpenAICompatibleModel`。QQ 和微信仍未接入。

## 环境要求

- Python 3.12+
- pytest（仅测试需要）

项目只有一个运行时依赖：`httpx`，用于异步模型请求、超时和取消。

## 首次初始化（CMD）

打开 CMD，进入项目目录后执行：

```bat
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[test]"
```

## 一键运行

直接双击项目根目录的 `run.cmd`。脚本会自动进入项目目录并优先使用 `.venv` 中的 Python。

也可以在 CMD 中运行：

```bat
run.cmd
```

## 配置文件

首次配置时，在 CMD 中复制模板：

```bat
copy .env.example .env
notepad .env
```

程序会自动读取项目根目录的 `.env`。同名系统环境变量优先于 `.env`；`.env` 已被 `.gitignore` 排除，不要把真实 API Key 提交到 Git。

### MockModel（默认）

```dotenv
MODEL_PROVIDER=mock
MODEL_BASE_URL=
MODEL_API_KEY=
MODEL_NAME=
```

Mock 模式不需要 Ollama、API Key 或网络连接，运行后固定返回测试回复。

### Ollama + Qwen3.5

安装并启动 Ollama 后，由你手动下载模型：

```bat
ollama pull qwen3.5:2b
ollama list
```

如果 Ollama 没有作为桌面后台程序运行，可以在单独的 CMD 窗口启动：

```bat
ollama serve
```

在 `.env` 中设置：

```dotenv
MODEL_PROVIDER=openai_compatible
MODEL_BASE_URL=http://localhost:11434/v1
MODEL_API_KEY=ollama
MODEL_NAME=qwen3.5:2b
```

随后双击 `run.cmd`。切换为 `qwen3.5:0.8b`、`qwen3.5:4b` 或 `qwen3.5:9b` 时只需修改 `MODEL_NAME`，业务代码无需变化。

### Packy 或其他 OpenAI-compatible 服务

```dotenv
MODEL_PROVIDER=openai_compatible
MODEL_BASE_URL=<服务商提供的 OpenAI-compatible 基础地址>
MODEL_API_KEY=<用户自行填写>
MODEL_NAME=<用户自行填写>
```

不要把 `/chat/completions` 重复写入 `MODEL_BASE_URL`；程序会自动追加该路径。

## 支持的环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `MODEL_PROVIDER` | `mock` | `mock` 或 `openai_compatible` |
| `MODEL_BASE_URL` | 空 | OpenAI-compatible API 基础地址 |
| `MODEL_API_KEY` | 空 | API 认证值；日志不会输出完整内容 |
| `MODEL_NAME` | 空 | 模型名称 |
| `MODEL_TIMEOUT_SECONDS` | `60` | 单次模型请求超时秒数 |
| `MODEL_TEMPERATURE` | `0.7` | 模型采样温度；设为空或 `none` 时不发送 |
| `MODEL_MAX_TOKENS` | `256` | 请求中的最大生成 token 数 |
| `CONTEXT_MAX_MESSAGES` | `12` | 发送给模型的最近 user/assistant 消息总数 |
| `MODEL_MAX_OUTPUT_CHARS` | `1200` | 程序允许显示和保存的最大回复字符数 |
| `SYSTEM_PROMPT_PATH` | `app/prompts/chat.txt` | System Prompt 文件位置 |
| `CHATBOT_DATABASE_PATH` | `data/chatbot.db` | SQLite 文件位置 |
| `CHATBOT_LOG_PATH` | `data/chatbot.log` | 调试日志位置 |
| `MESSAGE_DEBOUNCE_SECONDS` | `1.2` | 同一会话连续消息的 debounce 等待秒数 |
| `MEMORY_AUTO_PROCESS_ENABLED` | `true` | 是否在聊天完成后按阈值后台维护记忆 |
| `MEMORY_EXTRACTION_INTERVAL_MESSAGES` | `12` | 两次候选抽取之间至少新增的消息数 |
| `SUMMARY_TRIGGER_MESSAGES` | `24` | 未摘要消息达到此数量时更新阶段摘要 |
| `MEMORY_PROCESSING_BATCH_MESSAGES` | `40` | 单次记忆维护最多读取的新增消息数 |
| `MEMORY_CONFIDENCE_THRESHOLD` | `0.85` | 候选自动接受为长期记忆的最低置信度 |
| `MEMORY_CONTEXT_LIMIT` | `6` | 一次聊天最多选取的长期记忆数 |
| `MEMORY_CONTEXT_FALLBACK` | `2` | 关键词不命中时最多补入的近期记忆数 |
| `MEMORY_DEBUG` | `false` | 是否显示本次模型实际使用的上下文 |
| `MEMORY_PROMPT_PATH` | `app/prompts/memory_maintenance.txt` | 摘要与候选提取 Prompt |

旧名称 `MODEL_HISTORY_LIMIT`、`MODEL_MAX_OUTPUT_TOKENS` 和
`CHATBOT_MERGE_WINDOW` 仍兼容，但建议新配置统一使用表中的名称。

## 命令行聊天

输入格式：

```text
[张三] 你好
[李四] 在吗
```

不同联系人使用独立会话和独立 debounce 计时器。相同联系人在等待窗口内
连续输入的消息只触发一次模型调用；每条原始消息分别保存到 SQLite，也会以
多个连续的 `user` 消息传给模型，不会被拼成改变语义的一整句话。

模型上下文只保留最近 `CONTEXT_MAX_MESSAGES` 条 user/assistant 消息，
system prompt 不计入该数量。内存会话使用同一上限，不会无限增长；SQLite
仍保留完整原始历史。程序重启后，某会话第一次收到消息时会从 SQLite 恢复
该会话最近的必要上下文。当前新消息直接包含在这份上下文中，不会再追加第二次。

人工接管命令：

```text
/takeover 张三 on
/takeover 张三 off
```

开启接管后，该联系人的新消息仍会保存，但不会调用模型。生成中的旧回复会被
取消，且不再显示或保存。消息到达时会递增该会话的
`conversation_version`；每次生成同时记录独立 `generation_id`。发布回复前
必须同时匹配最新会话版本和活动生成编号，否则回复按过期结果丢弃。版本校验是
最终防线，即使模型请求没有正确响应取消，也不能发布旧回复。

## 长期记忆

聊天模型收到的内容分为独立区块：当前账号资料、当前联系人画像、少量相关长期
记忆、当前话题摘要、最近聊天和当前消息。长期记忆先限定在当前联系人，再按
关键词、记忆类型和最近使用时间选择；`disabled` 与 `superseded` 记录不会进入
模型上下文。最近聊天继续受 `CONTEXT_MAX_MESSAGES` 限制，长期记忆另受
`MEMORY_CONTEXT_LIMIT` 限制，完整原始消息始终保留在 SQLite。

记忆维护达到配置阈值后才在后台调用一次模型，同时尝试更新摘要和提出候选：

```text
原始聊天 -> memory candidate -> 来源/类型/置信度校验 -> active 记忆
                                                    -> pending/rejected/ignored
```

每个候选必须引用当前联系人真实存在的 user 消息 ID，并提供能在原消息中找到的
原文证据。相同 `memory_type + memory_key` 的事实发生变化时，候选必须执行
`update`；旧记录保留并标记为 `superseded`，新记录成为 `active`。摘要同样保存
消息起止 ID，并通过 `previous_summary_id` 形成增量更新链。维护请求失败只写日志，
不会撤销或阻断正常聊天。

### 记忆与画像管理命令

以下命令直接在运行中的聊天模拟器输入。`source_ids` 是逗号分隔的原始 user
消息 ID；可先用 `/memory candidates 联系人` 查看自动生成的候选及编号。

```text
/memory help
/memory list 张三
/memory list 张三 all
/memory show 1
/memory summary 张三
/memory candidates 张三
/memory messages 张三 20
/memory process 张三
/memory add 张三 preference food.cilantro 12 张三不吃香菜
/memory update 1 35 张三现在住在杭州
/memory accept 7
/memory disable 1
```

`delete` 是 `disable` 的兼容别名，数据不会物理删除。支持的联系人画像字段为
`preferred_name`、`relationship`、`communication_style`、
`stable_preferences`、`notes`：

```text
/profile show 张三
/profile set 张三 relationship 熟悉的朋友
/profile set 张三 communication_style 日常聊天较随意
/account show
/account set public_name 小陈
/account set public_information 平时做软件开发
/account set private_information 不应主动透露的真实信息
/debug on
/debug off
```

`/memory messages` 用于查找人工写入时需要引用的原始消息 ID。账号的
`private_information` 在 `/account show` 和 Debug 输出中只显示“已配置”，
不回显内容。Debug 只列出本次用到的消息、摘要 ID/范围、记忆 ID/来源和画像字段，
默认关闭；配置中的 API Key 会被替换为 `***`。

Mock 模式不会自动执行需要结构化 JSON 的后台记忆提取；聊天仍保持固定回复。
真实 `openai_compatible` 模式才会按阈值自动维护，也可以用
`/memory process 联系人` 手动触发一次。

## 测试

```bat
.venv\Scripts\python.exe -m pytest -q
```

pytest 中的模型测试全部使用 MockModel 或 Fake Client，不会连接真实模型或
任何收费 API。

### 手动聊天回放

固定测试集位于 `tests\chat_cases.json`，包含 29 组非唯一答案的中文聊天
场景。运行前确认 `.env` 已配置真实模型：

```bat
.venv\Scripts\python.exe -m app.tools.chat_replay --limit 5
```

只运行指定场景：

```bat
.venv\Scripts\python.exe -m app.tools.chat_replay --case rant_ide_crash --case during_generation_supplement
```

长期记忆回放案例会把已经确认的记忆作为独立结构化区块注入，不写入正式数据库：

```bat
.venv\Scripts\python.exe -m app.tools.chat_replay --case memory_driving_followup --case memory_cilantro_preference --case memory_location_superseded --case memory_contact_isolation --no-score
```

默认会逐项询问人工评分。非交互地收集输出可使用 `--no-score`。每次运行的
模型名、参数、Prompt 哈希、输出与人工评分会追加保存到
`data\chat_replay_results.json`，便于以后比较 Prompt 或模型版本。

回放命令默认拒绝 MockModel；只有检查工具本身时才使用
`--allow-mock --no-score`。真实模型回放不会被 pytest 自动触发。

## 错误与日志

命令行只显示简短错误，例如连接失败、超时、认证失败、模型不存在、服务异常或空回复。详细诊断记录在 `data/chatbot.log`，日志不会记录完整 API Key。

## 尚未实现

- QQ/NapCat 和微信/wxauto 适配器
- embedding、向量数据库和语义检索（当前仅关键词/类型/最近使用筛选）
- 自动从低置信度 `pending` 候选学习；仍需要人工接受
- 多 Agent、工具调用和联网搜索
- 图片、语音和 Web 管理后台
