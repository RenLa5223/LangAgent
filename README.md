# LangAgent

一个自托管的 AI 数字人项目，支持微信原生接入和 Web 界面对话。

## 功能

- **Web 界面** — 浏览器内与 AI 对话，支持流式输出
- **微信原生接入** — 通过微信 Bot API 直接收发消息，无需第三方依赖
- **记忆系统** — 艾宾浩斯衰减曲线，自动提炼长期记忆和用户画像
- **人物设定** — 自定义 AI 人设档案，对话严格遵循角色
- **主动关怀** — 按间隔和时段自动发送破冰消息
- **主题切换** — 5 套配色可选
- **桌面打包** — PyInstaller + Inno Setup，生成原生 Windows 安装程序

## 下载安装（普通用户）

不需要 Python 环境，直接装就能用。

1. 打开 [Releases](../../releases) 页面
2. 下载最新版 `LangAgent_Setup_vx.x.x.exe`
3. 双击安装 → 选择安装路径 → 完成
4. 桌面/开始菜单启动，浏览器打开 `http://localhost:5622` 进入对话

卸载：Windows 设置 → 应用 → 找到 LangAgent → 卸载。

## 从源码运行（开发者）

```bash
pip install -r requirements.txt
python server.py
```

打开 `http://localhost:5622`，完成初始化向导后即可使用。

## 微信接入

1. 启动服务器
2. 打开 Web 界面 → 侧边栏「微信接口」
3. 点击「开始扫码绑定」，扫描二维码
4. 绑定成功后启动消息服务

## 目录结构

```
├── server.py            # 主程序入口
├── wechat_agent.py      # 微信 Bot API 模块
├── index.html           # 前端界面
├── app_icon.ico         # 应用图标
├── build.bat            # PyInstaller 打包脚本
├── setup.iss            # Inno Setup 安装脚本
└── Data/                # 运行时数据（自动生成）
    ├── 模型配置/         # LLM API 配置
    ├── 微信配置/         # 微信 Bot 凭证
    ├── 记忆核心/         # 聊天历史、长期记忆
    ├── 人物档案/         # AI 人设
    ├── 用户档案/         # 用户画像
    └── 人物内心/         # AI 自动记录的用户情报
```

## 桌面打包

1. 双击 `build.bat` → 生成 `dist\LangAgent.exe`
2. 安装 [Inno Setup 6](https://jrsoftware.org/isdl.php)
3. 用 Inno Setup 打开 `setup.iss` 编译 → 生成安装程序

## 依赖

- Python 3.9+
- `pywebview` — 原生窗口
- `pystray` + `Pillow` — 系统托盘
- `PyInstaller` — 打包（可选）
- `Inno Setup 6` — 安装程序（可选）

## 免责声明

本工具仅供个人学习与合法通信用途。使用者不得利用本工具从事诈骗、骚扰、冒充、侵犯隐私或其他任何违反法律的行为。使用者需自行对自身行为负责，与项目作者无关。

## 许可证

本项目基于 [GNU General Public License v3.0](LICENSE) 开源。你可以自由使用、修改和分发，但衍生作品也必须以相同协议开源。
