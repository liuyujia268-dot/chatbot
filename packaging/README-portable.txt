Chatbot + NapCat.Shell Windows 便携版（64 位）

1. 把整个压缩包解压到有写入权限的目录，例如 Documents\Chatbot。
2. 双击 Chatbot.exe 打开桌面界面。不需要安装 Python 或 Node.js。
   本版随包包含 NapCat.Shell，但电脑仍需安装官方 Windows QQ。
   找到 QQ 后自动打开 QQ 页面、启动随包 NapCat 并打开登录页，按页面扫码登录。
3. 保留整个 Chatbot 文件夹，里面的 DLL、Tcl/Tk 和资源文件都是运行所需。
   不要只复制 EXE，也不要在压缩包内直接双击运行。

首次配置

默认模型为 Mock，QQ Dry Run 已开启，自动回复、主动聊天和追加回复已关闭。
首次启动在自己的解压目录生成独立 WebUI / OneBot Token；仅监听本机。
OneBot WebSocket 与本人消息上报已在干净默认配置中启用，无需手工复制 Token。
如果默认端口被占用，首次启动会选择附近的空闲端口并同步本地连接地址。
正常使用后不会在每次启动时重置配置；已有账号单独配置仍以 NapCat 设置为准。
找不到 QQ 时显示安装提示，并停留首页；不会自动安装软件。
可以在设置中填写自己的模型地址、API Key 和模型名称。
保存模型或连接设置后，按界面提示重新连接。

模型网络模式（保存后重连，程序重启后仍保留）

system：跟随电脑的系统/环境代理，默认选项。
direct：忽略电脑代理，直接连接模型；不需要代理时优先尝试此项。
custom：使用填写的代理地址，支持 http(s)://、socks5://、socks5h://。
新包已包含 SOCKS 支持，不需要安装 Python 包，不会修改 Windows 系统代理。
代理地址可能包含密码，界面会掩码；不要把自己的 .env 发给别人。
模型网络错误会在界面模型状态和当前会话中显示（仅本地，不发送给 QQ）。
QQ OneBot 断开/重连和模型 API 网络故障是两类独立问题。
记忆整理失败不会阻止正常聊天；默认至少冷却 300 秒后再因新消息尝试。
缺少 summary 时保留旧摘要和原始消息，不用编造摘要，也不逐条立即重试。
可在 .env 设置 MEMORY_FAILURE_RETRY_SECONDS（正数秒值）。

从旧包升级

先关闭旧 Chatbot 程序，不要强制结束 QQ；把新包完整解压到另一个目录。
先在新包运行离线自检，再迁移配置；不要直接用默认 .env 覆盖旧配置。
需要继续使用原账号时，可在新包设置中指向原 NapCat 的 config\webui.json，
并使用其实际 WebUI / WebSocket 地址和 Token；原 NapCat 不需要重新安装。
需要保留聊天与记忆时，先备份旧 data，再在程序停止后复制到新包。
自己的 .env、data 和已改过的 app\prompts 可保留；不要覆盖 NapCat 登录配置。
新旧 Chatbot 不要同时连接同一个账号，避免重复处理消息。

本包不包含 QQ、模型服务、开发者密钥、聊天数据和账号登录信息。
不复制发送者的 NapCat config、cache、logs、数据库或快捷方式。
随包 NapCat 使用独立目录，首次使用不去修改外部 NapCat 的配置。
如需使用自己的其他 NapCat，请在设置中填写其 config\webui.json 完整路径，
以及实际 WebUI 和 WebSocket 地址；自己启动该实例后点击 QQ。
WebUI Token 和 OneBot WebSocket Token 是两个独立配置。
实际发送需要关闭 Dry Run；请先用自己的测试联系人确认发送模式。

文件与数据

.env：你的本地配置，设置窗口会保存到这里。
.env.example：无密钥的初始配置示例。
app\prompts：5 份 Prompt，包含 chat.txt 中当前的角色性格，原样随包发布，可自行编辑。
账号资料、联系人画像与记忆保存在 SQLite，不属于 Prompt，没有随包复制。
app\assets：随程序发布的静态图片资源。
NapCat.Shell：经过清理的运行文件、上游许可证、干净默认配置。
data：运行后生成的数据库和日志。此发行包没有预装聊天记录。
不要把使用后的 .env 和 data 发给别人；转发请使用原始发行压缩包。

本地命令行模式

双击 run-console.cmd，或在 CMD 中执行：
    Chatbot.exe --console
默认以 Mock 模型运行命令行聊天。输入 /memory help 或 /style help 查看管理命令。
QQ_ENABLED=true 时可能进入 QQ 收发模式，请按自己的需要配置。

自定义 QQ 安装路径（只有自动查找失败时才需要）

在 CMD 中进入解压后的 Chatbot 文件夹，再执行：
    set "CHATBOT_QQ_EXE=D:\QQNT\QQ.exe"
    Chatbot.exe
请替换成自己实际的 QQ.exe 路径。此设置只影响当前 CMD，不修改系统 PATH。

离线启动自检

在 CMD 中执行：
    Chatbot.exe --self-test self-test.json
程序会自动退出并把结果写入 self-test.json；看到 status 为 passed 即自检通过。
自检使用临时数据库和 Mock，不连接 NapCat、不调用模型 API、不读取现用聊天数据。
它验证依赖（包括 SOCKS 代理初始化）、Prompt、SQLite、Mock 链路、去重、Dry Run、界面控件构造，
以及临时目录中的随包 NapCat 首次配置；不会真正启动 QQ / NapCat。
不代表真实 QQ 登录、模型服务或完整聊天体验已经验收通过。

适用范围

本构建使用 Windows x64、Python 3.12；建议用于 Windows 10/11 64 位。
未在其他 Windows 版本、32 位或 ARM 原生环境验证。
该 EXE 未进行代码签名。下载后如果 Windows 提示来源未知，应核对发行来源。
NapCat 源码：https://github.com/NapNeko/NapCatQQ
NapCat 的上游许可证限制商业用途；完整许可证在 NapCat.Shell\LICENSE.txt。

致谢

感谢 NapCatQQ 的开发者和贡献者提供 QQ 登录、OneBot 接口和消息收发能力。
Chatbot 的 QQ 接入依赖 NapCat；NapCat 是独立上游项目，不是本项目原创组件。
随包保留来源、版权及完整许可证，使用和分发请遵守上游许可。
项目介绍和完整使用说明见 README.md、docs\USER_GUIDE.md，归档记录见 CHANGELOG.md。

出现问题时保留 data\chatbot.log；对外提供日志前隐藏私人内容和密钥。
