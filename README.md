# LangAgent Terminal | Agent 沉浸式终端

一个运行在浏览器的私密 AI 伙伴终端，支持自定义人设、长期记忆、主动关怀与视觉交互。
由 RenLa5223 提供核心思路，Gemini 3.1 Pro 编写，DeepSeek V4 审计。

## 核心特性

- 沉浸式对话界面：毛玻璃拟物风格，适配 PC 与移动端。
- 双人设定：可自定义 Agent 与用户名称，构建专属数字关系。
- 深度人设系统：支持人物档案、用户档案、内心独白，让 AI 拥有人格。
- 记忆引擎：短期上下文与长期时间衰减记忆，自动摘要与重要度排序，自动提取并更新用户画像。
- 主动破冰：可设置时间段和随机间隔，让 Agent 主动发起问候。
- 模型热插拔：兼容任何 OpenAI 接口格式的大模型，一键测试连接。
- 图像支持：对话可发送图片，系统自动压缩优化。
- 头像上传：为 Agent 和用户分别设置头像。
- 系统重置：一键清除所有记忆和人设，恢复出厂状态。
- 纯文件存储：所有数据以 JSON 与 TXT 形式保存在 Data 目录，无数据库依赖。
- 安全隔离：前端与后端分离，API 路径白名单，15MB 请求限制，多线程锁保护。

## 技术栈

前端采用原生 HTML/CSS/JS，后端基于 Python 3 内置 http.server 与多线程，存储全部使用 JSON 及 TXT 文件，通信为 RESTful API 与 JSON 格式，AI 引擎支持所有兼容 OpenAI Chat Completions API 的模型。

## 目录结构

LangAgent-Terminal/
├── index.html           # 前端界面与交互逻辑
├── server.py            # Python 后端服务器
├── 启动控制台.bat         # Windows 一键启动脚本
├── Data/                # 运行时自动生成的数据目录
│   ├── 记忆核心/
│   │   ├── chat_history.json       # 短期对话记录
│   │   ├── memory_summary.json     # 长期记忆摘要
│   │   └── daily_signature.json   # 每日签名缓存
│   ├── 人物档案/
│   │   └── 人物档案.txt           # Agent 人设
│   ├── 用户档案/
│   │   └── 用户档案.txt           # 用户基础信息
│   ├── 人物内心/
│   │   └── 人物内心.txt           # AI 自动提炼的用户画像
│   ├── 模型配置/
│   │   └── config.json            # 连接信息、名称、主动设置
│   ├── 人物头像/                 # Agent 头像 (avatar.png)
│   └── 用户头像/                 # 用户头像 (avatar.png)
└── README.md            # 本文件

## 快速开始

1. 环境要求
   - Python 3.6 以上版本，无需额外库
   - 现代浏览器
2. 启动服务
   - Windows：双击 启动控制台.bat ，自动打开浏览器并启动服务器
   - macOS / Linux：执行 python3 server.py ，访问 http://localhost:5622
3. 首次设置
   - 页面弹出引导，输入 Agent 名称和你的名称
   - 进入聊天界面，点击左上角模型配置，填入 API 地址、密钥、模型名称，点击探测测试连接后保存
   - 在档案室中完善人物档案与用户档案，系统会自动读取

## 使用技巧

- 对话累计 22 轮后，系统自动将前 20 轮提炼为长期记忆并更新内心画像。
- 在功能设置中开启主动消息破冰，设置时间段与随机间隔，Agent 会主动问候。
- 进入人物档案或用户档案模块，可点击上传按钮替换头像。
- 在档案室侧边栏点击重置系统，输入确认短语可清空所有数据。

## 注意事项

- 本项目设计为单用户本地使用，请勿直接暴露于公网。
- 分享给他人时请确保其懂得配置自己的模型并保护 API Key。
- 所有数据以明文形式存储，敏感信息需自行管控。

## 许可证

本项目基于 MIT 许可证发布。

MIT License

Copyright (c) 2025 RenLa5223

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## 致谢

- RenLa5223：项目思路、产品设计、需求定义
- Gemini 3.1 Pro：全栈代码编写、架构实现
- DeepSeek V4：代码审计、安全加固、稳定性测试
