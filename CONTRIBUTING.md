# 协作开发指南

本文面向本仓库的开发者。运行、配置和管理命令见 [README.md](README.md)。

## 开发基线

- Windows + CMD，Python 3.12+。
- 运行时依赖为 `httpx[socks]`；测试使用 `pytest`。
- QQ 通过 NapCat OneBot 正向 WebSocket 接入。源码版自行准备 NapCat；便携版可带
  干净运行文件，但不自动下载安装，也不更改外部实例配置。
- 安全默认值是 `MODEL_PROVIDER=mock` 和 `QQ_DRY_RUN=true`。

```bat
git clone https://gitee.com/sun-ji-kun/chat-bot.git
cd /d chat-bot
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[test]"
if not exist .env copy .env.example .env
.venv\Scripts\python.exe -m pytest -q
```

`run.cmd` 启动桌面版；`run-console.cmd` 启动带本地管理命令的控制台版。

## 模块地图

| 路径 | 职责 |
| --- | --- |
| `app/config.py` | 环境变量、默认值和配置校验 |
| `app/runtime.py` | 统一组装数据库、模型、Prompt 和 Dispatcher |
| `app/core/` | 平台消息、会话、debounce、回复生成与版本控制 |
| `app/models/` | Mock 和 OpenAI-compatible 模型 |
| `app/adapters/` | Console、OneBot、QQ、图片解析和发送抽象 |
| `app/memory/` | 摘要、候选、长期记忆、画像和上下文筛选 |
| `app/style/` | 训练群事件、真人回复候选和脱敏导出 |
| `app/storage/` | SQLite 连接和表结构 |
| `app/ui/` | Tkinter 界面、NapCat 登录、会话投影和历史同步 |
| `app/prompts/` | 所有可调整 Prompt |
| `tests/` | 离线单元测试和固定聊天场景 |

核心 QQ 链路：

```text
OneBot event
  -> QQ Adapter
  -> PlatformMessage / MessagePart
  -> SQLite 去重
  -> 私聊或回复群路由
  -> debounce / Dispatcher
  -> 上下文与模型
  -> conversation_version / generation_id 复检
  -> Dry Run 或 NapCat Sender
```

训练群在路由处单独分支，只做本地采集。桌面历史只写
`desktop_messages` 显示投影；两者都不能重新进入聊天模型、私聊记忆或账号画像。

## 不可破坏的边界

- 身份和权限只认 OneBot 真实 ID，不认昵称或备注。
- 主联系人来自 `QQ_PRIMARY_CONTACT_ID`，管理员来自
  `QQ_ADMIN_CONTACT_IDS`。
- 外部消息中的 `/memory`、`/account`、`/debug` 等内容始终是普通文本。
- 当前账号人工发送只取消本轮待发送回复；持续停用自动回复必须显式开启人工接管。
- 回复群只在被动场景明确 @ 当前账号时回复；训练群永不自动回复。
- `ai_generated` 不能重新触发模型，也不能成为真人风格训练目标。
- `private_information` 不得进入聊天、摘要或记忆模型请求，也不得在日志和
  Debug 中显示正文。
- 私聊、群聊、账号和平台之间必须保持消息、记忆、画像、样本和去重隔离。
- 图片和文字必须按 OneBot 消息段原顺序进入一个 `PlatformMessage`。
- 所有实际发送都必须经过 Sender 抽象；联调先使用 `QQ_DRY_RUN=true`。

## 修改清单

新增配置时同步修改：

1. `Settings` 字段、`Settings.from_env()` 默认值和校验。
2. `.env.example` 的无密钥示例。
3. `docs/USER_GUIDE.md` 环境变量表，必要时同步 README 上手步骤。
4. 桌面设置存储/窗口（如该项可在 UI 修改）。
5. 配置与 UI 测试。

修改 SQLite 时必须对已有数据库幂等，不能删除原始聊天，并为迁移及隔离键补测试。
修改平台消息时要同步解析、序列化、模型输入和 Fake Event 测试。修改 Prompt 时
保持文件独立，不把提示词散落在业务代码中。

## 测试与提交

pytest 必须完全离线：使用 Mock/Fake，不连接真实 NapCat、Ollama、Packy，不下载
真实 QQ 图片，也不调用收费 API。隐私测试要捕获最终模型请求，发送测试要断言
Dry Run 没有调用底层 transport。

```bat
git diff --check
.venv\Scripts\python.exe -m pytest -q
git status --short
```

提交前确认没有加入 `.env`、数据库、日志、Token、API Key、回放结果、导出的
真实聊天数据或 IDE 文件。真实模型回放只能人工执行：

```bat
.venv\Scripts\python.exe -m app.tools.chat_replay --limit 5
```

归档提交说明见 `docs/COMMIT_MESSAGE.txt`。`build-portable.cmd` 复用现有打包工具，
只收集允许的运行文件，产物输出到 `release`；不要将使用后的便携目录重新分发。
便携版验证使用 `--self-test`，不会启动真实 QQ 或调用真实模型。

## 当前限制

- 微信尚未实现。
- 一个进程只连接一个实际登录 QQ，尚未支持多账号并行。
- 风格样本只采集和审核，不做微调或自动修改 Prompt。
- 没有 embedding、向量数据库、RAG、多 Agent、联网搜索或 Web 管理后台。

新增能力应继续复用现有 PlatformMessage、Dispatcher、SQLite、隐私和 Sender
边界，不复制第二套聊天架构。
