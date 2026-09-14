# Chatbot

一个保持简单、可测试、可替换的异步聊天机器人。当前支持固定回复的
`MockModel`、OpenAI-compatible 文本/视觉模型，以及 NapCat OneBot
WebSocket 的 QQ 收发接入。QQ 可显式切换 Dry Run 或真实发送，微信尚未接入。

准备参与开发时，请同时阅读 [协作开发指南](CONTRIBUTING.md)；README 负责运行与
配置，协作指南负责模块边界、测试约定和提交检查，未来方向记录在
[后续规划](qq新项目.md)。

## 环境要求

- Windows（主要开发和实际运行环境）
- Windows CMD；文档命令不依赖 PowerShell、Bash 或 WSL
- Python 3.12+
- pytest（仅测试需要）

项目只有一个运行时依赖：`httpx`，用于异步模型请求和需要时的受限图片下载。
NapCat WebSocket 接收使用 Python 标准库，不增加新的依赖。

## 首次初始化（CMD）

从远程仓库首次获取项目时，在 CMD 中执行：

```bat
git clone https://gitee.com/sun-ji-kun/chat-bot.git
cd /d chat-bot
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[test]"
copy .env.example .env
.venv\Scripts\python.exe -m pytest -q
```

已经克隆仓库时，从创建虚拟环境这一步开始即可。`.env`、SQLite 数据库、日志、
聊天回放结果和风格样本都是本机数据，不应提交或直接发给协作者。共享新增配置时
只更新不含真实账号、Token 和 API Key 的 `.env.example`。

## 一键运行

直接双击项目根目录已有的快捷方式，或运行 `run.cmd`。现在默认打开桌面界面，
脚本会自动进入项目目录并优先使用 `.venv` 中的 `pythonw.exe`，不会保留
命令行窗口。

也可以在 CMD 中运行：

```bat
run.cmd
```

需要排查日志或继续使用旧控制台时运行：

```bat
run-console.cmd
```

## 桌面界面

首页可以选择 QQ 或微信；微信入口当前只提示“尚未实现”。选择 QQ 后会打开配置的
NapCat WebUI，并在 OneBot WebSocket 返回有效登录账号前保持“QQ 登录中”状态。
未登录时，自动回复、主动聊天、追加回复和 Dry Run 开关均不可用，设置按钮始终
可用。

登录后左侧按“联系人”和“群聊”两个固定分组展示好友与群。每个分组的新项目只会
追加到末尾，收到新消息不会改变顺序；拖动每行最右侧的“☰”可以调整顺序，
点击左侧内容只选择会话，也可以将会话置顶。排序、
置顶状态和未读数保存在当前 SQLite 数据库的桌面投影表中。未读数包含对方消息和
模型生成的回复，打开会话后清零。

右侧会话区支持人工发送文本。图片和 QQ 表情默认只显示可点击的代号；图片仍会按
原消息结构进入视觉模型，不受桌面折叠设置影响。标准库 Tk 能识别的图片格式会在
点击后内嵌显示，其他格式提供浏览器查看入口。可以在设置中开启默认展开。
标题栏右侧的 `<` / `>` 用于切换当前分组内的上一或下一会话，
`<<` / `>>` 用于切换上一或下一分组；到达边界时对应按钮自动禁用。
首次打开一个会话时，桌面端会通过 NapCat 只读同步最近一批历史消息。历史只写入
桌面显示表，不会重新进入 Dispatcher、触发模型回复、长期记忆或风格训练。
快速切换时，较晚返回的其他联系人历史不会覆盖当前会话。

设置页可以修改 Provider、Base URL、模型名、超时、Temperature、最大 Tokens、
上下文条数、最大字符数和 debounce 等参数。已保存的 API Key 只显示掩码；保存
其他设置不会覆盖原 Key。模型和连接参数需要重新连接或重启后生效。

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
| `REPLY_REPAIR_PROMPT_PATH` | `app/prompts/reply_repair.txt` | 低信息回复单次修复指令 |
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
| `QQ_ENABLED` | `false` | 是否启动 NapCat OneBot WebSocket 事件接收 |
| `QQ_WEBUI_URL` | `http://127.0.0.1:6099/webui/` | 桌面端点击 QQ 时打开的 NapCat WebUI |
| `QQ_WEBUI_TOKEN` | 空 | NapCat WebUI 登录 Token；与 OneBot Token 分开且不会写入日志 |
| `QQ_WEBUI_CONFIG_PATH` | 空 | 可选的 `webui.json` 完整路径；留空时自动查找常见 NapCat 安装位置 |
| `QQ_WS_URL` | `ws://127.0.0.1:3001` | NapCat 正向 WebSocket Server 地址 |
| `QQ_WS_TOKEN` | 空 | NapCat WebSocket Token；日志不输出正文 |
| `QQ_PRIMARY_CONTACT_ID` | 空 | 主联系人的真实 QQ user_id，不按昵称自动绑定 |
| `QQ_PRIMARY_CONTACT_NAME` | `宇飞` | 仅用于显示和未绑定时提示候选 |
| `QQ_ADMIN_CONTACT_IDS` | 空 | 可使用 `#指令` 写入可追溯记忆的管理员 QQ 号 |
| `QQ_ADMIN_MEMORY_PREFIX` | `#指令` | 管理员记忆消息前缀 |
| `QQ_TRAINING_GROUP_IDS` | 空 | 逗号分隔的只读训练群号 |
| `QQ_REPLY_GROUP_IDS` | 空 | 群回复白名单；仅被 @ 时回复，也参与随机主动消息 |
| `QQ_DRY_RUN` | `true` | `false` 时通过 OneBot 动作真实发送 |
| `QQ_AUTO_REPLY_ENABLED` | `true` | 桌面端启动时的自动回复总开关 |
| `QQ_PROACTIVE_ENABLED` | `false` | 是否允许受控的主动消息 |
| `QQ_PROACTIVE_CONTACT_IDS` | 空 | 主动私聊白名单；空时使用最近活跃的私聊联系人 |
| `QQ_PROACTIVE_EXCLUDE_CONTACT_IDS` | 空 | 永不主动联系的 QQ 号 |
| `QQ_PROACTIVE_MIN_SECONDS` | `2400` | 每个联系人独立随机间隔下限 |
| `QQ_PROACTIVE_MAX_SECONDS` | `4800` | 每个联系人独立随机间隔上限 |
| `QQ_PROACTIVE_DAILY_LIMIT` | `3` | 每名联系人每天最多主动发送条数 |
| `QQ_PROACTIVE_RECENT_DAYS` | `7` | 空白名单时纳入的最近活跃天数 |
| `QQ_PROACTIVE_QUIET_START` | `00:00` | 本地静默时段开始 |
| `QQ_PROACTIVE_QUIET_END` | `08:00` | 本地静默时段结束 |
| `PROACTIVE_PROMPT_PATH` | `app/prompts/proactive.txt` | 主动消息生成指令文件 |
| `QQ_FOLLOWUP_ENABLED` | `true` | 是否允许概率追加第二条回复 |
| `QQ_FOLLOWUP_PROBABILITY` | `0.15` | 首条成功发送后的追加概率 |
| `QQ_FOLLOWUP_MIN_DELAY_SECONDS` | `2` | 追加回复最短延迟 |
| `QQ_FOLLOWUP_MAX_DELAY_SECONDS` | `7` | 追加回复最长延迟 |
| `FOLLOWUP_PROMPT_PATH` | `app/prompts/followup.txt` | 追加回复生成指令 |
| `QQ_IMAGE_MAX_BYTES` | `8388608` | 单张 QQ 图片允许的最大字节数 |
| `QQ_IMAGE_TIMEOUT_SECONDS` | `10` | 需要本地转码时的图片下载超时 |
| `QQ_IMAGE_ALLOW_REMOTE_URL` | `true` | 模型 Provider 是否可直接读取远程 image_url |
| `QQ_IMAGE_HISTORY_LIMIT` | `1` | 历史上下文最多重复发送的原图消息数 |
| `STYLE_CONTEXT_MIN_MESSAGES` | `3` | 生成风格候选所需的最少前文数 |
| `STYLE_CONTEXT_MAX_MESSAGES` | `10` | 风格候选最多保留的前文数 |
| `STYLE_EXPORT_PATH` | `data/style_samples.jsonl` | 已接受样本的脱敏 JSONL 输出位置 |
| `DESKTOP_AUTO_SHOW_MEDIA` | `false` | 桌面会话中是否默认展开图片和表情 |
| `DESKTOP_HISTORY_MESSAGE_LIMIT` | `50` | 首次打开会话时从 NapCat 同步的最近历史条数 |

旧名称 `MODEL_HISTORY_LIMIT`、`MODEL_MAX_OUTPUT_TOKENS` 和
`CHATBOT_MERGE_WINDOW` 仍兼容，但建议新配置统一使用表中的名称。

## 命令行聊天

`QQ_ENABLED=false` 时，输入会作为本地模拟的入站消息：

```text
[张三] 你好
[李四] 在吗
```

`QQ_ENABLED=true` 时，同样的输入改为当前账号通过 NapCat 直接发送，
不会送给模型当作测试消息：

```text
[宇飞] 这是发给主联系人的人工消息
[12345678] 这是发给指定 QQ 号的私聊
[群:87654321] 这是发给指定群的人工消息
```

主联系人名称按 `QQ_PRIMARY_CONTACT_NAME` 映射到
`QQ_PRIMARY_CONTACT_ID`。人工发送使用独立 Sender，不会标记成
`ai_generated`；`QQ_DRY_RUN=true` 时仍只记录而不实际发出。

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
/takeover 宇飞 on
/takeover 12345678 off
```

本地模拟模式使用联系人名称；QQ 模式既可使用
`QQ_PRIMARY_CONTACT_NAME`，也可直接使用 QQ user_id。
开启接管后，该联系人的新消息仍会保存，但不会调用模型。生成中的旧回复会被
取消，且不再显示或保存。消息到达时会递增该会话的
`conversation_version`；每次生成同时记录独立 `generation_id`。发布回复前
必须同时匹配最新会话版本和活动生成编号，否则回复按过期结果丢弃。版本校验是
最终防线，即使模型请求没有正确响应取消，也不能发布旧回复。

## NapCat QQ 收发接入

桌面端选择 QQ 时会按以下顺序取得 WebUI 登录 Token：
`QQ_WEBUI_TOKEN`、`QQ_WEBUI_URL` 中已有的 `token` 查询参数、本机
NapCat `config\webui.json`。自动读取到的 Token 只在打开浏览器时使用，
不会复制到 `.env`、状态栏或日志。`QQ_WEBUI_TOKEN` 与
`QQ_WS_TOKEN` 是两个独立配置；自动查找失败时可在“设置 → QQ 与界面”
填写 Token 或 `webui.json` 完整路径。

若本地 WebUI 尚未启动，桌面端会从同一 NapCat 安装目录定位
`launcher-user.bat`，先启动 NapCat 并等待 6099 端口就绪，再使用上述
Token 直接进入 WebUI 登录路由并刷新认证凭据。这样不会把浏览器缓存中的
“未知用户”状态误判为当前已登录的 NapCat。

登录失败、关闭了登录页或需要换号时，可在桌面端顶部点击“重新登录”，无需
重启 Chatbot。项目不写死当前 QQ 账号，会以 OneBot 的
`get_login_info.user_id` 和事件 `self_id` 为准；一次运行连接一个账号。
NapCat 的 OneBot 网络配置按 QQ 账号分别保存，因此换号后必须在 WebUI 中为
新账号单独启用与 `QQ_WS_URL` 一致的正向 WebSocket Server。程序不会擅自
复制另一个账号的 Token 或修改 NapCat 配置。

先在 NapCat WebUI 中人工启用 OneBot 正向 WebSocket Server，并记录地址和
Token。若需要采集当前 QQ 账号本人手动发送的消息，还需人工开启该连接的
`reportSelfMessage`。程序不会修改 NapCat 配置。

在 `.env` 中填写真实 ID；昵称只用于显示，不能代替 user_id：

```dotenv
QQ_ENABLED=true
QQ_WS_URL=ws://127.0.0.1:3001
QQ_WS_TOKEN=<NapCat WebSocket Token>
QQ_PRIMARY_CONTACT_ID=<主联系人 QQ user_id>
QQ_PRIMARY_CONTACT_NAME=宇飞
QQ_ADMIN_CONTACT_IDS=<管理员 QQ号>
QQ_ADMIN_MEMORY_PREFIX=#指令
QQ_TRAINING_GROUP_IDS=<群号1>,<群号2>
QQ_REPLY_GROUP_IDS=
QQ_DRY_RUN=true

QQ_PROACTIVE_ENABLED=true
QQ_PROACTIVE_CONTACT_IDS=
QQ_PROACTIVE_EXCLUDE_CONTACT_IDS=
QQ_PROACTIVE_MIN_SECONDS=2400
QQ_PROACTIVE_MAX_SECONDS=4800
QQ_PROACTIVE_DAILY_LIMIT=3
QQ_PROACTIVE_RECENT_DAYS=7
QQ_PROACTIVE_QUIET_START=00:00
QQ_PROACTIVE_QUIET_END=08:00

QQ_FOLLOWUP_ENABLED=true
QQ_FOLLOWUP_PROBABILITY=0.15
QQ_FOLLOWUP_MIN_DELAY_SECONDS=2
QQ_FOLLOWUP_MAX_DELAY_SECONDS=7
```

上面保留安全的 Dry Run。确认入站事件、账号 ID、群范围和候选回复都正确后，只有
明确准备真实发送时才将 `QQ_DRY_RUN` 改为 `false`。

所有非本人私聊都复用现有 Dispatcher 并生成回复；`QQ_DRY_RUN=false`
时会通过 NapCat 真实发送。昵称只用于显示，主联系人身份仍按 user_id 识别。
手机或桌面端的人工消息会使当前正在生成、等待追加或尚处于 debounce 的回复
立即过期，但不会永久关闭该会话的自动回复；下一条联系人消息仍默认自动回复。
需要连续人工接管时，在桌面端选中会话并勾选顶部“人工接管”，取消勾选后恢复。
训练群只保存本地原始事件和样本候选，不进入私聊历史、记忆或聊天模型。
只有 `QQ_REPLY_GROUP_IDS` 中的群允许回复，且仅在消息明确 @ 当前账号时回复；
配置层禁止回复群与训练群重叠。回复群的普通消息只保存为该群的上下文。
本人消息标记为 `human_self`，不会触发回复，并会开启对应会话的人工接管；
`ai_generated` 永远不能成为
真人风格训练目标。

主动消息对每个目标独立安排 40～80 分钟的随机时间。私聊白名单留空时使用
最近活跃联系人；发出一条但对方尚未回复时不会继续催发。训练群永不参与。

管理员发送 `#指令 [详细语句]` 时，该语句会作为管理员私聊会话的长期记忆
保存并保留原始消息来源；非管理员发送相同格式只会作为普通聊天文本。
管理员权限只比较 OneBot 事件中的真实 `sender_id` 是否位于
`QQ_ADMIN_CONTACT_IDS`；昵称、备注和 `QQ_PRIMARY_CONTACT_NAME` 均不参与
管理员身份判断。
主动消息按配置的随机区间安排，并限制每名联系人每日条数。生成期间若收到
该联系人的新消息，旧主动回复会被版本机制取消和丢弃。

图片与同一消息内的文本按 OneBot 消息段原顺序进入视觉模型。当前图片可以直接
使用远程 `image_url`；若 Provider 不支持，可将
`QQ_IMAGE_ALLOW_REMOTE_URL=false`，程序会按超时、大小和 Content-Type
限制下载到临时目录并转成 data URL，临时文件随后删除。较旧图片默认只保留
摘要或模型理解后的简短描述，不重复发送原图。

## 消息链路与安全边界

QQ 消息只使用一套处理链路：

```text
OneBot Event
  -> QQ Adapter
  -> PlatformMessage
  -> SQLite 持久化去重
  -> 私聊 / 回复群 / 训练群路由
```

私聊和允许回复的群会继续经过 debounce、Dispatcher、上下文组装、模型调用、
`conversation_version` 校验和 OutboundSender。低信息或明显答非所问的结果
最多修复一次；首条回复成功后，可按配置概率在短暂延迟后追加第二条。任何新消息
或人工接管都会使尚未发送的旧生成失效。

训练群只在本地保存原始事件，并从当前账号真实发送的 `human_self` 文本中建立
`candidate` 风格样本；不调用聊天模型、不自动回复，也不进入任何私聊记忆。
`ai_generated` 消息不会成为真人风格训练目标。

管理命令只由本地控制台解释。外部聊天中的 `/memory`、`/profile`、
`/account` 或 `/debug` 只按普通文本处理。管理员私聊的 `#指令` 是单独、
受 QQ user_id 白名单约束的记忆入口，不会获得其他本地管理权限。

`current_account_profile.private_information` 可以保存在 SQLite，但不会进入
聊天模型、记忆维护模型或摘要模型的任何请求；`/account show` 与 Debug 也不
显示正文。本人消息通过 OneBot 的 `self_id` 和发送者 ID 识别，保存为
`human_self`，并使该会话当前处于 debounce、生成或等待追加的回复失效；它不会
自动开启持续人工接管，下一条入站消息仍默认自动回复。该本人消息自身禁止再次
触发模型，避免消息循环。

`QQ_DRY_RUN=true` 是模板默认值；只有显式设为 `false` 才会调用 OneBot
发送动作。训练群无论该开关为何值都不会自动发送。

风格样本只能从本地 CLI 人工管理：

```text
/style stats
/style list
/style show 1
/style accept 1
/style reject 1
/style export
```

`/style export` 默认只导出 `accepted` 样本，并移除 QQ号、群号、昵称、
message_id 和真实时间戳。

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

以下命令直接在运行中的本地管理控制台输入。`source_ids` 是逗号分隔的原始 user
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

准备提交时同时执行格式和空白检查：

```bat
git diff --check
.venv\Scripts\python.exe -m pytest -q
```

新增配置、数据库结构、平台消息字段或发送行为时需要同步修改的文件清单，见
[协作开发指南](CONTRIBUTING.md)。

### 手动聊天回放

固定测试集位于 `tests\chat_cases.json`，包含 34 组非唯一答案的中文聊天
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

- 微信/wxauto 适配器；QQ群当前仅支持训练群只读采集，以及白名单群被 @ 后回复和受控主动消息
- embedding、向量数据库和语义检索（当前仅关键词/类型/最近使用筛选）
- 自动从低置信度 `pending` 候选学习；仍需要人工接受
- 多 Agent、工具调用和联网搜索
- 语音、表情包分析和 Web 管理后台
