# Chatbot

一个保持最小依赖的异步聊天机器人原型。当前只提供本地命令行模拟器和固定回复的 `MockModel`，不会连接真实 AI、QQ 或微信。

## 环境要求

- Python 3.12+
- pytest（仅测试需要）

## 运行

```powershell
.\.venv\Scripts\python.exe -m app.main
```

输入格式：

```text
[张三] 你好
[李四] 在吗
```

相同联系人在合并窗口内连续输入的消息会合并后处理，不同联系人的会话互相隔离。默认合并窗口为 0.5 秒，可通过进程环境变量 `CHATBOT_MERGE_WINDOW` 调整。

人工接管命令：

```text
/takeover 张三 on
/takeover 张三 off
```

开启接管后，该联系人的消息仍会保存到 SQLite，但不会生成自动回复。数据默认保存在 `data/chatbot.db`。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## 尚未实现

- `app/models/openai.py`：真实 AI 模型占位接口
- `app/adapters/qq.py`：QQ/NapCat 占位接口
- `app/adapters/wechat.py`：微信/wxauto 占位接口
