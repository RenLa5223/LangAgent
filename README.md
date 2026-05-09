LangAgent Terminal | Agent 沉浸式终端
https://img.shields.io/badge/License-GPLv3-blue.svg
https://img.shields.io/badge/python-3.8+-blue.svg
https://img.shields.io/badge/OpenClaw-Ready-brightgreen

一个拥有长期记忆、主动关怀、OpenClaw 桥接的沉浸式 AI Agent 终端。
像真实朋友一样陪伴，聊天、记录、遗忘、再想起。

✨ 核心特性
🧠 长期记忆 + 艾宾浩斯遗忘曲线
记忆会随时间自然衰减，重要的事记得更久，最终“遗忘”归于虚无。

💌 主动破冰消息
当沉寂超过设定时间，Agent 会按人设主动发来一条关心或分享（可自定义时间段与随机间隔）。

🦞 OpenClaw 桥接（龙虾接口）
完美兼容 OpenAI API 格式，可直接接入 OpenClaw / 微信机器人，让 Agent 自动接管外部消息。

🎨 多主题 + 头像管理
内置“默认/青岚/樱语”三种视觉主题，支持上传 Agent / 用户头像。

📸 多模态对话
支持发送图片，Agent 可理解图像内容（取决于所选模型能力）。

📁 完整的档案系统

人物档案（Agent 人设）

用户档案（你的基础信息）

人物内心（自动化生成的用户画像与私密笔记）

临时记忆（对话上下文）

长期记忆（衰减式摘要）

🔄 可插拔大模型
兼容任意 OpenAI API 格式（Ollama、DeepSeek、GPT、Claude 等），一键探测模型列表。

🧩 模块化前端
聊天界面 + 控制终端（档案室），支持实时编辑、手动保存、Token 估算。

🏗 系统架构
text
┌─────────────────┐     ┌──────────────────────────┐
│  浏览器 (index)  │────▶│   Python HTTP Server     │
│  • 聊天界面      │     │  • 静态文件服务           │
│  • 档案室管理    │     │  • 大模型调用             │
│  • 头像上传      │     │  • 记忆存取与衰减         │
└─────────────────┘     │  • 主动消息定时器         │
                        │  • OpenClaw 兼容 API      │
                        └───────────┬──────────────┘
                                    ▼
                        ┌──────────────────────────┐
                        │   本地文件系统 (Data/)    │
                        │   • 人物档案.txt          │
                        │   • 用户档案.txt          │
                        │   • 人物内心.txt          │
                        │   • chat_history.json    │
                        │   • memory_summary.json  │
                        │   • 头像文件              │
                        └──────────────────────────┘
🚀 快速开始
1. 克隆项目
bash
git clone https://github.com/yourname/langagent-terminal.git
cd langagent-terminal
2. 安装依赖（仅 Python 标准库 + 可选）
项目后端仅使用 Python 标准库，无需额外安装第三方包。
（如需更好性能可安装 aiohttp，但非必须）

3. 启动服务
bash
python server.py
控制台输出：

text
============================================================
🚀 安全级 Agent 驱动中枢已启动 [时间衰减+心跳架构+防脏数据]
🌐 正在监听端口 5622
🔗 OpenClaw 桥接地址: http://127.0.0.1:5622/v1
============================================================
4. 访问前端
打开浏览器访问 http://localhost:5622
首次启动会进入初始化向导，设置 Agent 名称和你的名称，之后自动进入主界面。

5. 配置大模型（重要‼️）
点击聊天界面左上角 ⚙️ 模型配置 → 填写：

API Base URL（例如 Ollama: http://localhost:11434/v1/chat/completions）

API Key（如不需要则留空）

模型名称（点击 📡 探测 自动拉取并填充）

隐藏深度思考过程（推荐开启，避免 <think> 标签显示）

保存后即可开始聊天。

⚙️ 功能配置详解
主动破冰消息
在 档案室 → 功能设置 中：

开启 主动消息破冰

设置 允许时段（如 08:00 至 23:00）

设置 间隔范围（例如最低 2 小时，最高 4 小时，系统随机延迟）
Agent 会在无对话超过最低间隔后，以你的身份主动发送一条短消息（≤15字）。

OpenClaw 桥接（龙虾接口）
支持将 Agent 接入微信/QQ等外部平台，自动回复外部消息。

在 档案室 → 龙虾接口 中打开 接管开关

在 OpenClaw 等工具中配置：

Base URL: http://127.0.0.1:5622/v1

API Key: 任意（如 any）

Model Name: agent-model（或任意）

点击 检查本地桥接通信 验证连通性

⚠️ 主动破冰 与 龙虾接口 互斥，开启其一将自动关闭另一。

记忆机制
临时记忆：存储最近 20 条对话上下文，超过 20 条后会触发自动摘要，形成一条长期记忆。

长期记忆：以 JSON 格式存储，每条记录包含内容、重要度（1~10）、时间戳。

遗忘算法：
score = importance × 2^( -hours_elapsed / half_life )
半衰期随重要度指数增长（重要度 1 → 半衰期 24h，重要度 10 → 半衰期约 543h）。
后台每 30 分钟执行一次衰减清理，剔除鲜活度 < 0.3 的条目。

人物内心（自动化画像）
每次摘要时，Agent 会从对话中提取关于你的新情报，覆盖或新增至 人物内心.txt，形成持续更新的用户画像。

📁 目录结构
text
langagent-terminal/
├── server.py                 # 后端主程序
├── index.html                # 前端单页
├── Data/                     # 运行时数据目录（自动创建）
│   ├── 记忆核心/
│   │   ├── chat_history.json      # 对话流水
│   │   ├── memory_summary.json    # 长期记忆（衰减）
│   │   └── daily_signature.json   # 每日个性签名缓存
│   ├── 模型配置/
│   │   └── config.json            # 模型、主动消息、主题等配置
│   ├── 人物档案/
│   │   └── 人物档案.txt            # Agent 人设
│   ├── 用户档案/
│   │   └── 用户档案.txt            # 你的基本信息
│   ├── 人物内心/
│   │   └── 人物内心.txt            # 自动生成的用户画像 + 私密笔记
│   ├── 人物头像/                   # Agent 头像存储
│   └── 用户头像/                   # 用户头像存储
└── README.md
📡 主要 API（供二次开发）
端点	方法	说明
/api/chat	POST	发送消息，返回流式分句
/api/save	POST	保存档案或配置
/api/read/{folder}/{file}	GET	读取文本/JSON 文件
/api/upload_avatar	POST	上传头像（base64）
/api/reset	POST	重置所有用户数据
/api/get_models	POST	探测模型列表
/api/poll	GET	轮询新消息（用于主动消息）
/v1/chat/completions	POST	OpenAI 兼容接口（OpenClaw）
/v1/models	GET	返回模拟模型列表
🧪 开发与测试
本地测试大模型（Ollama 示例）
bash
ollama pull qwen2.5:7b
ollama serve
然后在模型配置中填入：

URL: http://localhost:11434/v1/chat/completions

模型名称: qwen2.5:7b

运行单元测试（手动）
bash
python -c "import server; print('OK')"   # 检查语法
🤝 贡献指南
欢迎提交 Issue / PR / 自定义插件。
任何改进建议（记忆算法、前端交互、OpenClaw 深度集成）均受欢迎。

提交前请确保：

后端 Python 代码仅使用标准库（保持轻量）

前端无外部 CDN 依赖（已内联所有样式/脚本）

新功能不影响现有数据格式

📄 开源协议
本项目采用 GNU General Public License v3.0
您可以自由使用、修改、分发，但必须公开源代码并保留相同协议。详见 LICENSE 文件。
