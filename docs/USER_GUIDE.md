# 使用与配置详解

本文保留归档前的完整运行说明。初次使用请先阅读 [项目首页](../README.md)。

一个保持简单、可测试、可替换的异步聊天机器人。当前支持固定回复的
`MockModel`、OpenAI-compatible 文本/视觉模型，以及 NapCat OneBot
WebSocket 的 QQ 收发接入。QQ 可显式切换 Dry Run 或真实发送，微信尚未接入。

准备参与开发时，请同时阅读 [协作开发指南](../CONTRIBUTING.md)；本文负责运行与
配置，协作指南负责模块边界、测试约定和提交检查，未来方向记录在
[后续规划](../qq新项目.md)。

## 环境要求

- Windows（主要开发和实际运行环境）
- Windows CMD；文档命令不依赖 PowerShell、Bash 或 WSL
- Python 3.12+
- pytest（仅测试需要）

运行时使用 `httpx[socks]`，用于异步模型请求、SOCKS 代理支持和需要时的受限图片下载。
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
已确认发送或明确标记为模拟的自动回复，打开会话后清零；候选、待发送和失败
不会冒充已发送回复。

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
其他设置不会覆盖原 Key。设置页同时提供独立的 `QQ_WS_TOKEN`（OneBot 连接鉴权）
和 `QQ_WEBUI_TOKEN`（WebUI 登录），两者都采用掩码，不可混用。
保存前会校验完整合并配置，错误会指出字段，原有效配置不会被覆盖。
模型和连接参数需点击“重新连接”后生效；保存默认开关不会改变当前 Runtime 开关，
要即时变更请使用顶部开关。当前实际状态从 Runtime 回传，返回首页或重连不会用
启动配置覆盖显示。修改数据库路径需重新启动整个程序。

### 发送状态、草稿和账号隔离

会话中的发送记录明确区分候选、待发送、模拟、已发送、失败和结果未知。
只有收到平台成功回执才记为“已发送”（**不表示对方已读**）并进入真实 assistant
历史；分段回复每段分别确认。Dry Run 的模拟状态持久化，仅在模拟模式下作为
模拟历史使用，切回真实发送时不会冒充曾发给联系人的消息。

人工发送失败可在对应消息中“复制正文”或“重试”；重试复用原记录，不重复发送
已经成功的段落。超时或断线无法确认结果时显示“结果未知”，需要先核对 QQ 实际
记录，不自动重发。连接尚未就绪时正文保留在草稿中；异步失败不会覆盖随后输入
的新草稿。草稿按账号和会话分别保存，点击发送时固定目标和正文。

人工接管按账号和会话持久化，重连或重新启动后在入站处理前恢复；接管期间可以
人工发送，但不生成自动回复。关闭主动聊天或追加回复会使在途生成失效，各段
在提交前再次检查开关、账号、接管和版本。已经提交给平台的动作不承诺撤回。

阅读历史时刷新保留位置，有新消息会提示；接近底部时自动跟随。本人点击发送后
当前会话跟随最新记录。账号切换后的旧历史、头像和 Runtime 事件不会覆盖新账号。

内部会话键为 `scope:平台:账号:平台会话`，例如
`scope:qq:10001:qq:private:20002`；发送路由仍是 `qq:private:20002`。
消息、摘要、记忆、画像、接管、缓存和维护任务都使用内部键，账号资料使用
`account_profiles.account_key`。旧记录不会自动猜测归属或注入当前账号；
迁移仅针对人工选择的数据库副本。详见
[U01～U15 修复与迁移说明](ux_fixes_2026-10-07.md)。

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
| `MODEL_PROXY_MODE` | `system` | `system` 跟随电脑代理、`direct` 忽略代理直连、`custom` 使用指定代理；设置保存后重连生效 |
| `MODEL_PROXY_URL` | 空 | 仅 `custom` 使用，支持 HTTP(S)/SOCKS5/SOCKS5H；可能含代理密码，界面掩码，勿分享配置 |
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
| `MEMORY_FAILURE_RETRY_SECONDS` | `300` | 记忆/摘要失败后的冷却秒数；冷却后由后续消息触发重试，不阻止正常聊天 |
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
| `QQ_ADMIN_CONTACT_IDS` | 空 | 可使用受限管理员指令的真实 QQ 号列表 |
| `QQ_ADMIN_MEMORY_PREFIX` | `#指令` | 管理员记忆消息前缀 |
| `QQ_ADMIN_CONFIG_PREFIX` | `#配置` | 管理员安全配置命令前缀 |
| `QQ_TRAINING_GROUP_IDS` | 空 | 逗号分隔的只读训练群号 |
| `QQ_REPLY_GROUP_IDS` | 空 | 群回复白名单；仅被 @ 时回复，也参与随机主动消息 |
| `QQ_DRY_RUN` | `true` | `false` 时通过 OneBot 动作真实发送 |
| `QQ_AUTO_REPLY_ENABLED` | `true` | 桌面端启动时的自动回复总开关 |
| `QQ_PROACTIVE_ENABLED` | `false` | 是否允许受控的主动消息 |
| `QQ_PROACTIVE_CONTACT_IDS` | 空 | 主动私聊白名单；空时使用最近活跃的私聊联系人 |
| `QQ_PROACTIVE_EXCLUDE_CONTACT_IDS` | 空 | 永不主动联系的 QQ 号 |
| `QQ_PROACTIVE_MIN_SECONDS` | `1800` | 每个联系人独立随机间隔下限 |
| `QQ_PROACTIVE_MAX_SECONDS` | `3600` | 每个联系人独立随机间隔上限 |
| `QQ_PROACTIVE_DAILY_LIMIT` | `0` | 每名联系人每天最多主动发送条数；`0` 表示不限 |
| `QQ_PROACTIVE_RECENT_DAYS` | `7` | 空白名单时纳入的最近活跃天数 |
| `QQ_PROACTIVE_QUIET_START` | `23:59` | 本地静默时段开始（含） |
| `QQ_PROACTIVE_QUIET_END` | `07:00` | 本地静默时段结束（不含） |
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

登录失败、连接任务异常退出或需要换号时，可在桌面端顶部点击“重新连接”。
该按钮会停止旧 QQ Runtime、重建 WebSocket，并重新打开 NapCat 登录页。
项目不写死当前 QQ 账号，会以 OneBot 的
`get_login_info.user_id` 和事件 `self_id` 为准；一次运行连接一个账号。
NapCat 的 OneBot 网络配置按 QQ 账号分别保存，因此换号后必须在 WebUI 中为
新账号单独启用与 `QQ_WS_URL` 一致的正向 WebSocket Server。程序不会擅自
复制另一个账号的 Token 或修改 NapCat 配置。

QQ 被结束、尚未登录或 OneBot 暂时断开时，桌面端只在顶部显示连接状态，
不会因为自动重连反复弹出错误对话框。WebSocket 连通但账号/联系人尚未就绪时，
每隔 3 秒自动重试；重新确认当前 QQ 账号之前禁止提交发送。
如果仍未恢复，确认 QQ 已登录、该账号的 OneBot Server 已启用，再点“重新连接”。

### 切换 QQ 账号

换号推荐直接点顶部“切换账号”，不必先手动 `killqq` 再重启 Chatbot：

1. 确认后保存旧账号草稿，禁止新发送并停止旧连接/聊天任务。
2. 清理本机全部 QQ 后台（包括其他手动打开的 QQ）；清理失败就保持暂停，不启动第二个 QQ。
3. 清理成功后打开 WebUI，请人工登录另一个账号。没有 WebUI 服务时启动 NapCat；
   原 WebUI 仍在运行时，使用它的重启接口重建 QQ 登录服务，不再启动第二实例争抢端口。
   接口不支持或无法确认重启成功时，明确报错并请人工重启旧 NapCat，不盲目重复启动。
4. 使用 OneBot `get_login_info.user_id` 确认新账号，随后加载该账号的联系人、群聊、会话与接管状态。

如果启动器自动快速登录了旧账号，程序会等待其他账号，**不会恢复旧账号的回复**。
想取消换号并恢复原账号，可点击“重新连接”。换号不复制聊天/记忆/账号资料，
草稿、接管状态和已保存的会话仍按账号隔离；再次换回原账号可以继续使用原数据。
窗口顶部显示实际确认的 QQ 号，昵称、备注和管理员联系人 QQ 号都不是登录账号的判断依据。

即使 WebSocket 不断开，程序也每 10 秒核对一次登录账号；收到其他 `self_id` 的事件时会立即暂停并提前核对。
登录确认期间最多暂存 100 条消息，确认后只把当前账号的消息交回原有 QQ Adapter/Dispatcher，重复消息仍会去重；
旧账号事件不会带入新账号，超出暂存容量会在日志中告警。后台核对只读取 OneBot/WebUI 账号与网络配置，不读取聊天、不调用模型。

新 QQ 账号仍需要在 NapCat 中启用自己的 OneBot 正向 WebSocket Server。
桌面端对本机 WebUI 和本机 OneBot 做只读配置发现：使用 WebUI Token 获取当前 QQ 登录状态及
当前账号已启用的 WebSocket 地址/Token，自动在运行内存中切换现有 OneBot 客户端，不写入 `.env`。
多个本地服务时优先匹配 `QQ_WS_URL` 的端口；只有一个时可跟随其端口；无法唯一确定时明确提示，
不猜测服务、不复制旧账号配置、不自动开启新账号服务。远端连接仍以手动设置的地址/Token 为准。
WebUI 不可用/版本不支持时，保留手动 OneBot 连接；已发现的登录间隙则保持停止发送，直到新账号确认。
身份仍由 OneBot `get_login_info` 复核，WebUI 状态不直接将账号绑定到聊天业务。
程序不会替新账号复制或修改 NapCat 配置，也不会填写 QQ 登录凭据。远端 WebUI 不会执行本机清理换号，
请在远端切换后点击“重新连接”。

WebUI 配置查找、启动等待均在后台线程进行，不再阻塞桌面界面；已知配置路径优先且会缓存，
找到后不会再扫描无关 AppData/Program Files 目录。已有 WebUI 服务且已配置 Token 时跳过安装目录搜索。
可在设置填写 `QQ_WEBUI_CONFIG_PATH`
（`webui.json` 的完整路径）减少首次查找。启动后重新读取配置文件中的 Token，避免沿用启动前的值。
启动器/QQ 本身的冷启动耗时仍由 NapCat/QQ 决定，程序不会以打开浏览器代替实际连接成功。
网络探测仅使用本机 WebUI 的认证与读取接口；Token 只保留在内存，不显示在状态栏/日志。
自动重连不调用重启接口；重启接口仅在用户确认“切换账号”且 QQ 清理成功后使用。
相关接口依据 [NapCat 官方 WebUI 源码](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-webui-backend/src/router/QQLogin.ts)
和 [按账号读取 OneBot 配置的实现](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-webui-backend/src/api/OB11Config.ts)。

### 安全退出 QQ

桌面端顶部点击“退出 QQ”并确认：保存草稿 → 立即禁止新发送 → 停止聊天任务和
自动重连 → 退出本机全部 QQ 账号后台（包括其他手动打开的 QQ）。先尝试正常关闭，
仍驻留的进程会被强制结束，并检查是否有残留；权限不足或聊天任务未退出时会明确提示，
不会假报成功。不删除 QQ 聊天记录、项目数据库或配置，不结束无关 Node/Python 进程。
已经提交给平台的消息无法撤回，结果未知的消息需要人工核对。

未登录或断线时也能使用“退出 QQ”。退出完成后保持断开，只有主动点击“重新连接”
或从首页再次选择 QQ 才恢复连接。窗口右上角 × 只退出 Chatbot，不默认清理 QQ；
想清理全部 QQ 后台请先使用上述按钮。退出过程中请等待状态栏报告完成再关闭窗口。

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
QQ_ADMIN_CONFIG_PREFIX=#配置
QQ_TRAINING_GROUP_IDS=<群号1>,<群号2>
QQ_REPLY_GROUP_IDS=
QQ_DRY_RUN=true

QQ_PROACTIVE_ENABLED=true
QQ_PROACTIVE_CONTACT_IDS=
QQ_PROACTIVE_EXCLUDE_CONTACT_IDS=
QQ_PROACTIVE_MIN_SECONDS=1800
QQ_PROACTIVE_MAX_SECONDS=3600
QQ_PROACTIVE_DAILY_LIMIT=0
QQ_PROACTIVE_RECENT_DAYS=7
QQ_PROACTIVE_QUIET_START=23:59
QQ_PROACTIVE_QUIET_END=07:00

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
本人消息标记为 `human_self`，不会触发回复，只会使当次正在等待或生成的自动
回复失效，不会自动开启持续人工接管；`ai_generated` 永远不能成为
真人风格训练目标。

主动消息对每个目标独立安排 30～60 分钟的随机时间，不限制每日次数，只按时间
判断；23:59（含）到次日 07:00（不含）不会主动发起。私聊白名单留空时使用
最近活跃联系人；不再设置每日条数或“必须等对方回复”的限制。训练群永不参与。

管理员发送 `#指令 [详细语句]` 时，该语句会作为管理员私聊会话的长期记忆
保存并保留原始消息来源；非管理员发送相同格式只会作为普通聊天文本。
管理员权限只比较 OneBot 事件中的真实 `sender_id` 是否位于
`QQ_ADMIN_CONTACT_IDS`；昵称、备注和 `QQ_PRIMARY_CONTACT_NAME` 均不参与
管理员身份判断。
管理员还可以使用 `#配置` 白名单命令查看或变更自动回复、主动聊天、追加回复、
追加概率、主动间隔、每日上限和静默时段。发送 `#配置 帮助` 查看格式；不允许
通过聊天修改 API Key、Token、模型地址、Prompt 或任意环境变量。配置会立即应用
并安全写回本机 `.env`。非管理员发送相同文本仍按普通聊天处理。
生成期间若收到该联系人的新消息，旧主动回复会被版本机制取消和丢弃。

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
最多修复一次；首条回复确认发送或模拟后，可按配置概率在短暂延迟后追加第二条。
私聊新消息、相关群请求补充或人工接管会使尚未发送的旧生成失效；
无关未 @ 的群背景消息不会直接取消待回答请求，也不会单独触发普通自动回复。
追加回复若与首条仅有空白或标点差异，
会被直接丢弃，不保存也不发送。

训练群只在本地保存原始事件，并从当前账号真实发送的 `human_self` 文本中建立
`candidate` 风格样本；不调用聊天模型、不自动回复，也不进入任何私聊记忆。
`ai_generated` 消息不会成为真人风格训练目标。

斜杠管理命令只由本地控制台解释。外部聊天中的 `/memory`、`/profile`、
`/account` 或 `/debug` 只按普通文本处理。管理员私聊的 `#指令` 和
`#配置` 是两个独立、受 QQ user_id 白名单约束的入口；`#配置` 仅支持文档列出
的安全项，不开放任意本地管理命令。

按账号隔离的 `account_profiles.private_information` 可以保存在 SQLite，但不会进入
聊天模型、记忆维护模型或摘要模型的任何请求；`/account show` 与 Debug 也不
显示正文。本人消息通过 OneBot 的 `self_id` 和发送者 ID 识别，保存为
`human_self`，并使该会话当前处于 debounce、生成或等待追加的回复失效；它不会
自动开启持续人工接管，下一条入站消息仍默认自动回复。该本人消息自身禁止再次
触发模型，避免消息循环。

`QQ_DRY_RUN=true` 是模板启动默认值；运行期间以 Runtime 实际开关为准，
只有明确关闭 Dry Run 才会调用 OneBot 发送动作。训练群无论该开关为何值都不会
自动发送。

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

QQ 会话请优先点击右侧“复制管理标识”，使用完整 `scope` 键：

```text
/memory list scope:qq:10001:qq:private:20002
/memory messages scope:qq:10001:qq:private:20002 20
/profile show scope:qq:10001:qq:private:20002
/profile set scope:qq:10001:qq:private:20002 notes 人工确认的备注
```

上例 ID 为合成示例，实际以复制结果为准。在当前账号范围内也支持已知 QQ 号和
唯一昵称；重名会明确提示歧义，不按昵称猜测。未找到目标与“该联系人没有记忆”
是不同结果。下面的 `张三` 示例适用于已经存在的本地模拟会话。

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
/account set public_image_path app/assets/whale_girl_profile.jpg
/account set private_information 不应主动透露的真实信息
/debug on
/debug off
```

`/memory messages` 用于查找人工写入时需要引用的原始消息 ID。账号的
`private_information` 在 `/account show` 和 Debug 输出中只显示“已配置”，
不回显内容。Debug 只列出本次用到的消息、摘要 ID/范围、记忆 ID/来源和画像字段，
默认关闭；配置中的 API Key 会被替换为 `***`。

Mock 模式不会自动执行需要结构化 JSON 的后台记忆提取；聊天仍保持固定回复。
`public_image_path` 是可公开资料图的项目内路径。本项目默认配置为鲸鱼娘资料图。
QQ 联系人或白名单群成员明确索要鲸鱼娘的资料、照片或形象时，程序会随正常
文字回复附带该图；训练群不会因此回复。该路径不会进入模型请求，且在
`/account show` 和 Debug 输出中隐藏。

真实 `openai_compatible` 模式才会按阈值自动维护，也可以用
`/memory process 联系人` 手动触发一次。

## 测试

```bat
.venv\Scripts\python.exe -m pytest -q
```

pytest 中的模型测试全部使用 MockModel 或 Fake Client，不会连接真实模型或
任何收费 API。
测试护栏还禁止读取项目现用 `.env`、正式数据目录及进行真实应用网络连接。
U01～U15 的正确行为回归在 `tests/test_ux_*.py` 中；
`artifacts/ux_acceptance_probe.py` 是原始 Bug 复现证据，不作为修复通过条件。

准备提交时同时执行格式和空白检查：

```bat
git diff --check
.venv\Scripts\python.exe -m pytest -q
```

新增配置、数据库结构、平台消息字段或发送行为时需要同步修改的文件清单，见
[协作开发指南](../CONTRIBUTING.md)。

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

命令行只显示简短错误，例如连接失败、超时、认证失败、模型不存在、服务异常或
空回复。详细诊断记录在 `data/chatbot.log`，日志不会记录完整 API Key。
NapCat WebSocket 会在空闲时发送心跳，握手、心跳和关闭都有超时；断线后持续
重连，重复的同类连接错误会节流记录，避免日志被 traceback 刷满。

## 尚未实现

- 微信/wxauto 适配器；QQ群当前仅支持训练群只读采集，以及白名单群被 @ 后回复和受控主动消息
- embedding、向量数据库和语义检索（当前仅关键词/类型/最近使用筛选）
- 自动从低置信度 `pending` 候选学习；仍需要人工接受
- 多 Agent、工具调用和联网搜索
- 语音、表情包分析和 Web 管理后台
