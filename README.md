<p align="center">
  <img src="images/logo.png" alt="HACS Private Repo Syncer Logo" width="160" height="160">
</p>

<h1 align="center">HACS Private Repo Syncer</h1>

<p align="center">
  <strong>安全、自动化地将 GitHub 私有仓库中的自定义组件同步至 Home Assistant</strong>
</p>

<p align="center">
  <a href="https://github.com/hacs/default"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS Custom"></a>
  <a href="https://github.com/xiaoxianbuild/hacs-private-repo-syncer/actions/workflows/release.yml"><img src="https://github.com/xiaoxianbuild/hacs-private-repo-syncer/actions/workflows/release.yml/badge.svg" alt="Release"></a>
  <a href="https://github.com/xiaoxianbuild/hacs-private-repo-syncer/releases"><img src="https://github.com/xiaoxianbuild/hacs-private-repo-syncer" alt="GitHub Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/xiaoxianbuild/hacs-private-repo-syncer" alt="License"></a>
</p>

---

## ✨ 核心特性

- 🚀 **智能混合项目提取（Hybrid / Monorepo Support）**：
  若您的私有仓库是全栈或混合仓库（例如包含 Go/Node 后端服务、`Dockerfile`、`node_modules`、前端源码等），同步引擎将**只提取** `custom_components/<domain>` 子目录，彻底杜绝垃圾文件污染 Home Assistant。
- 📦 **原生 Update Entity 支持**：
  为每个受监控的私有仓库自动创建 Home Assistant 原生的 `Update` 实体。在 HA 的“设置 -> 系统 -> 更新”面板中直观查看版本变动、Release Notes，并支持一键点击安装更新。
- 🔘 **实体级手动触发（Button Entities）**：
  每个私有仓库设备均自动配备 **“立即同步 (Sync Now)”** 与 **“检查更新 (Check Updates)”** 按钮实体。无论当前是否有新版本，随时可以在仪表盘或设备卡片上一键手动拉取最新代码并覆盖安装！
- 🎯 **两步向导 & 自动版本发现**：
  - **第 1 步**：输入 PAT 与标准仓库 URL，即时在线校验权限与连通性；
  - **第 2 步**：自动列出该仓库的所有 Release、Tag 标签与 Branch 分支供您可视化选择。
- 🔒 **全异步 & 零环境依赖**：
  无需在 HA OS / 容器中安装 `git` 或配置复杂的 SSH 密钥。直接基于 `aiohttp` 异步流式下载 GitHub API zip 归档，性能极高且无阻塞。
- 🛡️ **生产级安全防护**：
  - 内置 **Zip Slip 路径穿越漏洞防御**；
  - 更新时自动备份旧版本并在解压异常时**原子回滚**，保障系统稳定性。
- ⚙️ **完整的 UI 配置流程**：
  支持通过 Home Assistant 原生 Config Flow 与 Options Flow 随时切换分支/标签或调整更新轮询周期。
- 🔔 **更新完成智能通知**：
  插件更新完成后自动发送 HA 持久化通知（Persistent Notification），提示用户重启 Home Assistant 生效。

---

## 📁 支持的私有仓库目录结构

本同步器能够智能识别以下各类仓库结构：

### 结构 1：混合/全栈 Monorepo 项目（推荐）
```text
my-private-project/
├── API_EN.md
├── docker-compose.yml
├── Dockerfile
├── go.mod
├── main.go
├── node_modules/             <-- 自动被忽略
├── package.json
├── custom_components/        <-- 仅提取此目录
│   └── bandwagon_usage/
│       ├── manifest.json
│       ├── __init__.py
│       └── sensor.py
└── README.md
```

### 结构 2：标准 HACS 仓库
```text
my-hacs-component/
├── hacs.json
├── README.md
└── custom_components/
    └── my_service/
        ├── manifest.json
        └── ...
```

### 结构 3：扁平根目录结构
```text
my-flat-component/
├── manifest.json             <-- 包含 "domain": "my_service"
├── __init__.py
└── ...
```

---

## 🔑 GitHub 访问令牌准备 (PAT)

由于是访问私有仓库，需要生成一个 GitHub Personal Access Token：

1. 登录 GitHub，访问 **Settings** -> **Developer settings** -> **Personal access tokens**；
2. 推荐选择 **Fine-grained tokens** 或 **Tokens (classic)**：
   - **Fine-grained token**：选择对应的私有仓库，在 **Repository permissions** 中设置 **Contents: Read-only** 即可；
   - **Classic token**：勾选 `repo` 作用域。
3. 复制生成的 Token 备用。

---

## 📥 安装方法

### 方式一：通过 HACS 自定义存储库添加（推荐）

1. 打开 Home Assistant 前端的 **HACS**；
2. 点击右上角的三个点，选择 **自定义存储库 (Custom repositories)**；
3. 输入存储库 URL：`xiaoxianbuild/hacs-private-repo-syncer`，类别选择 **集成 (Integration)**；
4. 点击添加后，搜索并下载 **HACS Private Repo Syncer**；
5. 重启 Home Assistant。

### 方式二：手动安装

将本仓库的 `custom_components/private_repo_syncer` 文件夹复制到 Home Assistant 的 `/config/custom_components/` 目录下，并重启 Home Assistant。

---

## 🛠️ 配置使用指南

1. 进入 Home Assistant **设置 (Settings)** -> **设备与服务 (Devices & Services)**；
2. 点击右下角 **添加集成 (Add Integration)**，搜索 `HACS Private Repo Syncer`；
3. **第 1 步：身份验证与仓库地址**：
   - 填写 GitHub Personal Access Token；
   - 填写私有仓库标准 URL，例如：`https://github.com/your_username/repo_a`；
   - 点击下一步，系统将自动校验 Token 权限及仓库连通性。若 Token 无权限或仓库不存在，将直接标红提示修改。
4. **第 2 步：选择安装目标 (Release / Tag / Branch)**：
   - 校验成功后，系统自动在线拉取该仓库的最新版本列表；
   - 在下拉菜单中自由选择：
     - 🚀 **Latest Release**（推荐：始终跟随作者最新发布的正式版本）
     - 📦 **特定 Release**（如 `v1.2.0`）
     - 🏷️ **特定 Tag 标签**（如 `v1.0.0`）
     - 🌿 **特定 Branch 分支**（如 `main` 或 `dev` 分支最新代码）
   - 设置更新检查间隔（默认 120 分钟）；
5. 点击提交即完成安装！插件会自动在后台下载并提取到 `/config/custom_components` 目录下。

---

## 🔘 手动触发更新仓库的三种方式

### 方式 1：通过按钮实体一键触发（最推荐）
每个监控的私有仓库都会在对应设备下生成控制按钮：
- **`button.<repo>_sync_now`（立即同步）**：点击该按钮（按下/Press），系统立即重新从 GitHub 拉取该分支/标签的代码并覆盖更新至 `/config/custom_components`。
- **`button.<repo>_check_updates`（检查更新）**：点击该按钮，立即向 GitHub 发起请求刷新最新版本信息。

您可以将这些按钮直接放置在 Lovelace 仪表盘，或在自动化中任意调用！

### 方式 2：在系统更新面板中点击“安装”
当仓库发布了新版本（或分支有了新提交），Home Assistant 原生的“设置 -> 系统 -> 更新”面板会展示该插件的更新提示，点击“安装”即可一键升级。

### 方式 3：调用集成服务 (Services)
在“开发者工具 -> 动作/服务”中调用：
```yaml
action: private_repo_syncer.sync
data:
  repository: "your_username/my-private-component" # 指定仓库，留空则同步全部配置的仓库
  force: true                                       # 是否强制覆盖
```

---

## 🚀 自动化发布与 Tag 规则 (GitHub Actions)

仓库内置了自动 Release 工作流（`.github/workflows/release.yml`）：
- **触发规则**：推送符合语义化版本规则的 Git Tag，例如 `v1.0.0`, `v1.0.6` 等。
- **执行流程**：自动运行单元测试、打包发布资产并创建 GitHub Release。

发布新版本只需执行：
```bash
git tag v1.0.6
git push origin v1.0.6
```

---

## 🧪 单元测试

本项目核心解压引擎与 URL 解析器包含严格的自动化测试：
```bash
python3 -m unittest discover -s tests -p "test_*.py"
```

---

## 📄 License

MIT License
