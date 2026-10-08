# Windows 便携发行包

运行根目录 `build-portable.cmd`，产物会放入本目录的独立时间戳子目录。
ZIP、EXE 运行目录和构建报告不进入 Git；源码、文档与打包脚本保留在仓库。

- `Chatbot-With-NapCat-Windows-x64-*.zip`：完整包。解压全部文件，双击 `Chatbot\Chatbot.exe`。
- `Chatbot-Update-Windows-x64-*.zip`：仅应用更新包，不能单独首次启动；先读包内升级说明。
- `build-result.json`：ZIP 路径、大小与 SHA-256。
- `verification.json`：离线自检、文件完整性、隐私排除和更新保留配置的结果。
- `SHA256SUMS.txt`：可用于核对两个 ZIP 的完整性。

完整包需要官方 Windows QQ，但不需要安装 Python / Node.js。
默认 Mock + Dry Run，自动、主动和追加回复关闭；真实模型由使用者配置。
发行包不含开发者密钥、聊天数据库或 QQ 登录信息。

NapCat 上游：[NapCatQQ](https://github.com/NapNeko/NapCatQQ)。感谢上游开发者与贡献者，
完整包保留 `NapCat.Shell\LICENSE.txt` 与 `NOTICE.txt`，请遵守其许可。
