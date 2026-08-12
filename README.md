简体中文 | [English](README.en.md)

# WuWa Term Bot

自建的《鸣潮》官方本地化翻译服务：中文术语译为官方英文，英文亦可反向译回中文，并支持两个方向的术语锁定整句翻译。系统由一个 protocol-neutral 应用层同时服务两个 presentation adapter——Telegram bot 与版本化的 HTTP API——另有一个消费该 API 的 Windows 桌面客户端。

服务遵循 dictionary-first（词典优先）。词典精确命中时，直接逐字节返回本地 SQLite 数据库中的官方字符串，不调用 LLM。翻译方向按文字体系自动判定：中文源文本默认译为英文，英文/拉丁字母源文本默认译为中文。两种语言的自由文本都只在已知词条被锁定之后才送往 OpenAI 兼容端点，因此官方术语会在目标语言中按原样还原，而不是被改写。

## 架构概述

- **应用层**：`src/wuwaterm/application.py` 唯一一次持有 dictionary-first 翻译流水线。它是 protocol-neutral 的——不导入任何表示层模块，也不导入聊天 SDK（[ADR 0009](docs/adr/0009-http-api-adapter.md)）。
- **两个 presentation adapter**：Telegram bot（`src/wuwaterm/bot.py`、`channel.py`）负责命令、会话授权、聊天措辞与富文本标记；版本化的 HTTP API（`src/wuwaterm_api/`）负责版本化路由、设备认证、统一错误信封与纯文本响应。两个对外入口均由这同一条流水线提供服务（[架构文档](docs/architecture.md)）。
- **device-principal 设备主体认证**：所有 `/v1` 路由都要求设备凭据。凭据可单独吊销，不涉及 Telegram bot 自身的访问控制；凭据存储中只保存加盐 scrypt 校验值（[ADR 0010](docs/adr/0010-device-principal-authentication.md)）。
- **Windows 桌面客户端**：`client/` 下的客户端有意不作为 adapter，而是 API 已发布契约的消费方，自身不含任何翻译逻辑。它经由 HTTPS 访问服务（[ADR 0011](docs/adr/0011-pc-client-stack.md)、[ADR 0012](docs/adr/0012-client-transport-selection.md)）。
- **已发布契约**：API 契约快照提交在 [`docs/api/openapi.json`](docs/api/openapi.json)，由 `scripts/check_api_contract.py` 做漂移门禁。

0.3.0 的发布说明将这一步描述为「API-first release: the Telegram-only bot becomes a multi-adapter system」（[更新日志](CHANGELOG.md)）。以上各点的决策依据见 [ADR 索引](docs/adr/README.md)；模块、请求流与信任边界的维护者地图见 [架构文档](docs/architecture.md)。

## 快速开始

以下命令假定运行在 POSIX shell 中。若在 WSL 下工作，请把工作副本放在 WSL 文件系统上（例如 `~/projects/...`），以便文件监视、权限、换行符与虚拟环境脚本的行为与 Linux 一致。

```bash
test -x .venv/bin/python || uv venv .venv
uv sync --locked --extra dev
```

如果 WSL 镜像中已安装 `python3-venv` 与 pip，标准库路径同样可用：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e ".[dev]"
```

仅在确实要从零重建本地虚拟环境时才使用 `uv venv --clear .venv`。

## 常用命令

构建本地词典：

```bash
.venv/bin/python -m wuwaterm.cli refresh-data --dest data/wutheringdata --profile arikatsu
.venv/bin/python -m wuwaterm.cli build-db --data-dir data/wutheringdata --db data/terms.candidate.db --profile arikatsu --atomic
.venv/bin/python scripts/verify_db.py data/terms.candidate.db --profile arikatsu
```

查询术语并翻译整句：

```bash
.venv/bin/python -m wuwaterm.cli lookup --db data/terms.db 声骸
.venv/bin/python -m wuwaterm.cli sentence --db data/terms.db "今汐装备了声骸"
```

运行 Telegram bot：

```bash
export TELEGRAM_BOT_TOKEN="..."
export WUWATERM_DB_PATH="data/terms.db"
.venv/bin/python -m wuwaterm.cli bot
```

运行 HTTP API 适配器（需要 `api` extra，其中包含 FastAPI 与 uvicorn；默认绑定 loopback）：

```bash
export WUWATERM_DB_PATH="data/terms.db"
.venv/bin/python -m wuwaterm_api.cli serve
```

Telegram 命令示例：

- `/tr 声骸` -> `Echo`
- `/tr Echo` -> `声骸`
- `/tr --to en 今汐装备了声骸` 与 `/tr -to en 今汐装备了声骸`
  强制输出英文
- `/tr --to zh Jinhsi equipped an Echo` 与
  `/tr -to zh Jinhsi equipped an Echo` 强制输出中文
- `/sentence --to en 今汐装备了声骸` 与 `/sent --to en 今汐装备了声骸`
  强制整句译为英文
- `/sentence --to zh Jinhsi equipped an Echo` 与
  `/sent --to zh Jinhsi equipped an Echo` 强制整句译为中文

未给出方向参数时，默认仍为自动判定。要对某条消息作出回复式翻译，发送 `/tr --to en`、`/tr -to en`、`/sentence --to zh` 或 `/sent --to zh`，bot 会按指定方向翻译被回复的文本。就校验而言：非法的 --to 取值只返回用法说明，不调用 LLM；词典精确命中同样不调用 LLM。对于关联频道的贴文，频道自动翻译始终为自动判定方向，不接受命令方向参数。

运行标准校验集：

```bash
.venv/bin/python scripts/check_repo_hygiene.py
.venv/bin/python scripts/check_non_goals.py
.venv/bin/python scripts/check_architecture_boundaries.py
.venv/bin/python -m pytest
```

## 数据来源与许可边界

主数据源：

- `https://github.com/Arikatsu/WutheringWaves_Data`
- 钉住提交：`dae29691c04ef0f48d0810b5d244fb0b37288c60`
- 钉住版本：`GameVer 3.5.0 | ResVer 3.5.5 | Changelist 8059200`

主数据源不可用时可手动尝试的备用镜像：

- `https://github.com/Dimbreath/WutheringData`，仅作为 legacy fallback profile
  保留，在 `src/wuwaterm/constants.py` 中钉住于
  `e9234ffe094b2d944d16b222d31102e8ab32d954`。

当前启用的 Arikatsu 源 profile 只对 `README.md`、`BinData` 与 `Textmaps` 做稀疏检出。其中根目录的 README 是必需的版本溯源文件。大体量 TextMap 数据与生成的数据库都是本地产物，已被 Git 忽略。本项目不对《鸣潮》游戏数据做任何再分发，只在本地从上述公开源构建一份小规模的派生术语词典。所有《鸣潮》游戏数据与游戏内术语版权归 © Kuro Games 所有。

刷新、构建与校验的细节见[数据刷新](docs/data-refresh.md)。

## 指南

- [架构](docs/architecture.md)：模块、请求流、信任边界、单实例拓扑与 ADR 的维护者地图。
- [更新日志](CHANGELOG.md)：按发布版本记录的源码变更。
- [部署](docs/deployment.md)：VPS 上的 Docker Compose 服务、`.env` 处理、数据刷新命令与冒烟检查。
- [数据刷新](docs/data-refresh.md)：源 profile、本地准备、数据库构建、查询命令与数据许可边界。
- [Telegram 行为](docs/telegram-behavior.md)：命令、群组授权、公开模式、关联频道自动翻译与 Telegram 侧限制。
- [HTTP API 契约](docs/api/openapi.json)：版本化 `/v1` 路由已提交的契约快照。
- [桌面客户端](client/README.md)：HTTP API 的 Windows 客户端，含技术栈、设置、凭据处理与构建方式。
- [隐私与 LLM](docs/privacy-and-llm.md)：dictionary-first 隐私边界、LLM 配置、提示注入防护、占位符完整性、fail-closed 设置与密钥处理。
- [校验](docs/validation.md)：离线校验命令、线上冒烟的注意事项与 Windows 参考命令。
- [发布检查单](docs/release-checklist.md)：发布元数据、校验、隐私说明、分发边界与发布说明模板。

## 部署入口

VPS 目标环境使用 Docker Compose，因为该机器上现有的系统 Python 版本低于本项目要求。`/opt/wuwaterm/current` 必须是干净的 Git 工作副本，其 `HEAD` 可与刚拉取的 `origin/main` 校验一致；不带 `.git` 的导出式源码副本会被有意拒绝。请依据 `deploy/env.example` 创建 `/opt/wuwaterm/current/.env`，权限设为 `600`，并通过 `deploy/docker-compose.yml` 运行 Compose。

```bash
cd /opt/wuwaterm/current
docker compose -f deploy/docker-compose.yml run --rm wuwaterm-builder refresh-data
WUWATERM_DEPLOY_ROOT=/opt/wuwaterm/current sh deploy/vps-update.sh
```

更新脚本会先构建并强校验一份独立的候选数据库与一个不可变的源码修订镜像，然后才停止旧服务；随后执行提升、启动、冒烟、写入不可变清单，并原子地发布 `.deploy_commit`。提升之后的任何失败都会回滚数据库、镜像与指针。`deploy/vps-update.sh` 会对两个服务容器一并停止、重启、冒烟与回读，回滚同样覆盖两者。

两个服务容器都出自 `runtime` Docker target 与同一镜像，区别只在入口命令：`bot` 运行 Telegram bot（`wuwaterm-bot`），`api` 运行 HTTP API（`wuwaterm-api`）。runtime 镜像还接受仅供运维使用的 `device` 命令，以一次性容器方式管理凭据，除此之外的命令一律拒绝。数据刷新、构建与校验则通过 `wuwaterm-builder` 服务使用 `builder` target。两个服务容器都以只读方式挂载 `data/`。bot 使用可写的 `state/` 存放 `chat_settings.json` 与 `channel_replies.json`；API 使用与之并列的 `state-api/` 存放自己的设备凭据存储，因此 bot 的读写挂载永远不会覆盖到它。
升级较早的部署时，请使用 `deploy/vps-update.sh`，或[部署](docs/deployment.md)文档中的仅状态迁移路径。两者都会在经过校验的一次性原子迁移之前停止旧运行时。切勿在旧 bot 仍在运行时手工复制状态文件。请删除或更新那些仍把这些文件指向 `data/` 的旧 `.env` 覆盖项。
运行时密钥只通过 Compose 的 `env_file` 注入到服务容器；builder 没有 `env_file`，且 `.env` 被忽略并排除在镜像构建上下文之外。完整部署说明见[部署](docs/deployment.md)。

## 校验入口

完整的本地校验流程：

```bash
.venv/bin/python scripts/verify_db.py data/terms.candidate.db --profile arikatsu
.venv/bin/python scripts/verify_seed_terms.py data/terms.candidate.db --discrepancies goal-runs/wuwaterm-v2-translator/seed-discrepancies.json
.venv/bin/python scripts/verify_exact_hits.py data/terms.candidate.db --sample-size 500
.venv/bin/python scripts/verify_idempotent_build.py --data-dir data/wutheringdata --out-dir goal-runs/wuwaterm-v2-translator --profile arikatsu
.venv/bin/python scripts/check_repo_hygiene.py
.venv/bin/python scripts/check_non_goals.py
.venv/bin/python scripts/check_architecture_boundaries.py
.venv/bin/python -m pytest
```

上面的 `goal-runs/` 路径是本地工作产物，已被 Git 忽略；这些文件由运行校验的机器上的脚本创建或读取。

`scripts/deploy_smoke.py` 是部署可达性检查，不是长轮询处理链路的端到端测试。确切的校验范围与线上 Telegram 冒烟的注意事项见[校验](docs/validation.md)。

## 维护

这是个人业余项目，按尽力而为的方式维护。不保证会回应 issue 或 pull request。

## 许可

本项目以 [MIT 许可证](LICENSE)发布，© 2026 My-Denia。该 MIT 许可仅覆盖本项目的源代码，不覆盖上游《鸣潮》游戏数据或游戏内术语——后者版权归 © Kuro Games 所有。参见[数据来源与许可边界](#数据来源与许可边界)。
