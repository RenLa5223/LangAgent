打开 `http://localhost:5622`，完成初始化向导后即可使用。

## 微信接入

1. 启动服务器
2. 打开 Web 终端 → 侧边栏「微信接口」
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

本项目仅供个人学习与合法用途。请遵守微信 Bot API 使用规范及相关法律法规。
