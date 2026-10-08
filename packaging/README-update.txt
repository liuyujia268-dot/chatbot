Chatbot Windows x64 修复更新包

适用于之前已经完整解压并配置过的 Chatbot 便携版，不可单独运行。

1. 先备份原程序文件夹，关闭旧 Chatbot 窗口，确认旧 Chatbot.exe 已退出。
   这个更新不需要重新安装 QQ 或 NapCat，也不需要重新填写 API Key。
2. 解压更新包，把里面 Chatbot 文件夹的内容复制到原程序文件夹内，选择替换同名文件。
   原程序文件夹是包含 Chatbot.exe 的那一层；不要套进第二层 Chatbot 文件夹。
   必须一起替换所附 DLL 和运行库，不要仅替换 Chatbot.exe。
3. 双击原位置的 Chatbot.exe。
   设置 -> 会话模型 -> 模型网络模式：
   system 跟随电脑代理（默认）；direct 忽略电脑代理直连；custom 使用填写的指定代理。
   不需要代理的电脑，可选择 direct，保存，然后重新连接。重启后该选项仍保留。

本包包含 SOCKS 支持，不需要在客户电脑安装 Python 包。
代理地址支持 http(s)://、socks5://、socks5h://，含密码时界面掩码显示。
模型网络故障会显示本地错误提示，不会把报错消息发送给 QQ。
记忆摘要失败保留旧摘要和原始聊天，并冷却后再因后续消息尝试。

本包不会覆盖：.env、.env.example、data、app\prompts、NapCat.Shell。
因此保留你的模型配置、聊天/记忆、角色性格以及原 NapCat 配置和登录状态。
请勿同时运行新旧两个 Chatbot。未实际在 Win10 目标机器完成 QQ/API 验证，
更新后请用自己的测试联系人先检查模拟发送模式，再确认是否启用真实发送。

可在 CMD 原程序目录离线自检：
    Chatbot.exe --self-test self-test.json
它只使用临时数据库和 Fake/Mock，不登录 QQ，也不调用真实模型。
若需回退，关闭 Chatbot 后使用你更新前的整份备份，不要只回退 EXE。
