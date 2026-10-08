# Chatbot

一个 Windows 上的中文即时聊天机器人，带桌面会话界面，通过 NapCat 连接 QQ。
支持 OpenAI-compatible 文本/视觉模型、独立会话、SQLite 长期记忆和人工接管。
本仓库保留归档快照；微信尚未实现，聊天样本仅供本地审核，不会自动训练模型。

## 开始使用

### 便携版：解压后双击

1. 在 `release` 中找到 `Chatbot-With-NapCat-Windows-x64-*.zip` 完整包，
   解压到有写入权限的文件夹。不要选择 `Chatbot-Update-*` 更新包首次安装。
2. 双击解压目录内的 **Chatbot.exe**。无需安装 Python 或 Node.js；
   QQ 接入仍需要电脑已安装官方 Windows QQ。
3. 按登录页提示登录 QQ，在右上角“设置”中填写自己的模型地址、名称与 API Key。
   保存后按提示重新连接；微信入口目前只显示未实现。
4. 先保持 **Dry Run**，确认候选回复正常后再自行启用自动回复、关闭 Dry Run。

便携版默认使用 Mock 模型，Dry Run 开启，自动回复、主动聊天和追加回复关闭。
随包 NapCat 使用干净配置，首次启动在解压目录生成独立本机 Token；
不携带开发者密钥、聊天记录或 QQ 登录信息，不修改外部 NapCat 配置。
请保留完整文件夹，不能只复制 EXE。详细步骤见包内 `README-first.txt`。
建议 Windows 10/11 64 位；EXE 未签名，其他系统尚未验证。

### 源码版：Windows CMD

需要 Git 和 Python 3.12+。首次获取和初始化：

```cmd
git clone https://gitee.com/sun-ji-kun/chat-bot.git
cd /d chat-bot
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[test]"
if not exist .env copy .env.example .env
run.cmd
```

已经下载源码时，先在 CMD 中进入项目目录，从创建 `.venv` 那一行继续。
以后双击 `run.cmd` 即可打开桌面界面；`run-console.cmd` 打开命令行模拟器及管理入口。
源码版连接 QQ 需自行运行 NapCat，在设置中填写实际 WebUI、WebSocket 地址和 Token。
**WebUI Token 用于登录管理页，`QQ_WS_TOKEN` 用于 OneBot 连接，两者不同。**

## 模型配置

离线体验保持 `MODEL_PROVIDER=mock`，不需要 API Key 或网络。
使用真实模型，在设置中填写，或编辑本机 `.env`：

```dotenv
MODEL_PROVIDER=openai_compatible
MODEL_BASE_URL=<服务商提供的基础地址，或 http://127.0.0.1:11434/v1>
MODEL_API_KEY=<自己的 API Key；本机 Ollama 可填 ollama>
MODEL_NAME=<服务商模型名称，或已在 Ollama 安装的模型>
```

模型和连接设置通常保存后重连生效，数据库路径变更需要重启。
OpenAI-compatible 服务、Ollama 模型由使用者自行准备，程序不会自动安装或下载。
代理可选 `system`、`direct`、`custom`，支持 HTTP(S) 和 SOCKS。

## 日常使用与安全

- 私聊走现有聊天链路；群聊只按白名单和被 @ 条件回复，训练群只读采集。
- 手工发送会取消当前待发送自动回复；要持续接管，使用“人工接管”开关。
- “模拟”不是已发送；“已发送”仅表示平台确认，不代表对方已读。
  “结果未知”应先核对 QQ，不能盲目重发。
- “切换账号”先停止旧任务再登录新号。“退出 QQ”确认后清理本机全部 QQ 后台，
  包括其他手工打开的 QQ；普通关闭 Chatbot 窗口不等于退出 QQ。
- `.env`、`data` 和使用后的 NapCat 配置属于私人数据，不要提交或转发。
  账号 `private_information` 不进入聊天、记忆或摘要模型请求。

## 测试与打包

在源码目录的 CMD 中执行：

```cmd
.venv\Scripts\python.exe -m pytest -q
build-portable.cmd
```

测试使用 Mock/Fake 和临时数据，禁止真实 QQ 发送、真实模型调用和读取现用聊天数据。
打包复用已安装的 PyInstaller 和本机 NapCat 运行文件，不自动安装依赖；
产物在 `release` 的时间戳目录中，附文件清单、SHA-256 和离线验证结果。
其他 NapCat 路径可用 `build-portable.cmd --napcat-source "D:\NapCat.Shell"` 指定。

便携版离线自检（在解压目录执行）：

```cmd
Chatbot.exe --self-test self-test.json
```

自检不连接 QQ 或收费模型；通过不等于真实登录和模型服务已验收。
更新包仅适用于已有完整便携版，使用前先备份，按包内 `README-update.txt` 操作。

## 文档

- [使用与配置详解](docs/USER_GUIDE.md)：全部环境变量、记忆管理、样本和回放命令。
- [协作开发指南](CONTRIBUTING.md)：模块边界、离线测试和提交约定。
- [发送状态与旧数据迁移](docs/ux_fixes_2026-10-07.md)：账号隔离及兼容注意事项。
- [提交与归档记录](CHANGELOG.md)；[本次提交说明](docs/COMMIT_MESSAGE.txt)。

## 致谢

感谢 [NapCatQQ](https://github.com/NapNeko/NapCatQQ) 的开发者和贡献者，
本项目的 QQ 接入、登录管理和 OneBot 消息收发建立在 NapCat 提供的能力上。
NapCat 是独立的上游项目；随包保留其来源、版权声明和完整许可证，
见 `NapCat.Shell\NOTICE.txt`、`NapCat.Shell\LICENSE.txt`，使用与分发请遵守上游许可。
