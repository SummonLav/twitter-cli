# SummonLav/twitter-cli 说明

本仓库是 [twitter-cli](https://github.com/public-clis/twitter-cli)（Apache-2.0，作者 jackwener）的修改版。

- **基线**：上游 main 的 `7c634e0`（2026-05-07）。这个提交包含 0.8.5 之后未发布到 PyPI 的修复：
  2026 年 4 月 X 接口变更适配、搜索改为 POST、长文导出改进。
- **维护方式**：不再从上游拉代码。出问题就在这里改；想借鉴上游的修复，先审查再挑着合入（见「维护」）。
- **目标**：AI Agent 可以替你读推、搜推、抓长文，也可以按你的要求发推、点赞，但**拿不到你的 Cookie**。

## 和上游相比改了什么

| 位置 | 改动 |
|---|---|
| `twitter_cli/auth.py` | 删除自动读取浏览器 Cookie（以及 Cookie 失效后再去浏览器里找另一份）的逻辑。凭据只来自权限为 600 的凭据文件，或显式设置的环境变量。会校验 Cookie 内容，防止注入额外的请求头 |
| `twitter_cli/client.py` | Cookie 请求头只允许发往 `x.com`、`api.x.com`、`upload.twitter.com`，且必须是 HTTPS，其他地址一律拒绝 |
| `twitter_cli/safe.py` | 新增两个命令。`twitter-safe`：以独立系统账户运行，清空调用方的环境变量，禁止读写本地文件的参数。`twitter-safe-setup`：只能在交互式终端里用隐藏输入录入凭据 |
| `pyproject.toml` / `uv.lock` | 去掉 `browser-cookie3` 及它独有的 6 个依赖，其他依赖版本不变。版本号为 `0.8.6+summonlav.1` |
| `deploy/` | 安装脚本（Linux/macOS）、卸载脚本，以及带哈希的锁文件 |
| `SKILL.md` | 给 Agent 的说明：只能调用 `tw`，不索要 Cookie、不升级，推文内容一律视为不可信 |

## 谁能接触到 token

```
你的账户（Agent 在这里运行）
  └─ /opt/twitter-safe/bin/tw 搜索 ...
       = sudo -n -u xtwitter /opt/twitter-safe/bin/twitter-safe ...   ← sudoers 只放行这一条命令
            以 xtwitter 身份运行，丢弃调用方的全部环境变量
            读取 ~xtwitter/.config/twitter-safe/credentials.json（目录 700、文件 600）
            → 通过 HTTPS 发往 x.com
  Agent 只能拿到命令输出（输出里不含 token）
```

| 途径 | 为什么拿不到 |
|---|---|
| Agent 直接读凭据文件 | 文件归 xtwitter 所有，权限 600，所在目录 700 |
| Agent 修改代码，让它打印 token | 代码、虚拟环境和 Python 都归 root，你的账户改不了 |
| 借 `-o`、`--input`、`--image` 读写凭据文件 | 用 click 自带的解析器识别这类参数并拒绝，`-o文件`、`--output=` 的写法也拦得住 |
| 注入 `PYTHONPATH`、`TWITTER_PROXY`、`HTTPS_PROXY` | 启动脚本用 `python -I` 运行，并清空环境变量；代理只读 root 拥有的配置文件 |
| 代理或网络上的中间人 | 只能看到 `CONNECT x.com:443` 和加密流量，证书校验没有关闭 |
| 借用你几分钟前输入过的 sudo 密码 | sudoers 为你的账户设置了 `timestamp_timeout=0`，每次 sudo 都要重新输密码 |
| 让你把 Cookie 粘贴到聊天里 | Cookie 只在你自己的终端里录入，`SKILL.md` 也禁止 Agent 索要 |

## 安装

### 前提
- **Linux**：`/usr/bin/python3` 为 3.10 或更高版本，并已安装 `python3-venv`。
- **macOS**：从 python.org 安装 Python 3.10 或更高版本。不要用 Homebrew 的 Python，它的目录你的账户可写。
  - 如果预检提示 `writable by group`，执行 `sudo chmod -R g-w,o-w /Library/Frameworks/Python.framework`。
  - curl-cffi 的 wheel 要求 Apple 芯片 macOS 14 及以上、Intel macOS 15 及以上。
- 你的账户不能有免密 root 权限，比如 `NOPASSWD: ALL`、docker 组。否则 Agent 可以先提权成 root，再读凭据。安装脚本发现这种情况会给出警告。

### 步骤
```bash
# 1. 由 root 克隆一份。用 /usr/bin/git，不要用 Homebrew 的 git
sudo /usr/bin/git clone https://github.com/SummonLav/twitter-cli /opt/twitter-safe-src
sudo /usr/bin/git -C /opt/twitter-safe-src checkout <你审查过的提交>
/usr/bin/git -C /opt/twitter-safe-src log -1 --format=%H    # 核对提交哈希

# 2. 预检：不做任何修改
sudo /opt/twitter-safe-src/deploy/install.sh --check

# 3. 安装（在中国大陆需要代理时加 --proxy）
sudo /opt/twitter-safe-src/deploy/install.sh --agent-user "$USER" \
    --proxy http://127.0.0.1:7890 --lang zh_CN.UTF-8
```

安装脚本会做这些事：
- 创建服务账户：Linux 上叫 `xtwitter`，家目录 `/var/lib/xtwitter`；macOS 上叫 `_xtwitter`，家目录 `/var/_xtwitter`。
- 只用带哈希校验的 wheel 构建 `/opt/twitter-safe/venv`，项目本身离线构建。
- 写入 `/etc/sudoers.d/twitter-safe`，写入前用 `visudo` 校验。
- 最后做一次自测。

### 录入 Cookie（只在你自己的终端里做）
1. 在浏览器里登录 x.com，用 Cookie-Editor 选择 Export → **Header String**，复制完整的 Cookie 字符串。只有 `auth_token` 和 `ct0` 时，发推可能被 X 以 226 错误拒绝。
2. 运行 `sudo -u xtwitter /opt/twitter-safe/bin/twitter-safe-setup`（macOS 用 `_xtwitter`），粘贴后回车。输入是隐藏的。
3. 清空剪贴板。
4. 运行 `/opt/twitter-safe/bin/tw whoami` 验证。

### 接入 Agent
- 把 `SKILL.md` 放进 Agent 的 skill 目录，例如 `~/.claude/skills/twitter-cli/SKILL.md`。
- 在 Agent 的权限设置里只放行 `tw`。Claude Code 示例：
  ```json
  { "permissions": { "allow": ["Bash(/opt/twitter-safe/bin/tw:*)"] } }
  ```
- X 长文用 `tw article <链接> --markdown` 抓全文。推文里的外链文章，让 Agent 用普通的网页读取工具去抓，不涉及 X 凭据。

## 维护

```bash
# 在开发机上修改 fork
uv sync --frozen --extra dev
uv run ruff check . && uv run mypy twitter_cli && uv run pytest -q
git push

# 部署到使用的机器
sudo /usr/bin/git -C /opt/twitter-safe-src fetch
sudo /usr/bin/git -C /opt/twitter-safe-src checkout <新提交>
sudo /opt/twitter-safe-src/deploy/install.sh --agent-user "$USER"   # 重新安装，凭据保留
```

- **依赖有变化**：修改 `pyproject.toml`，运行 `uv lock` 和 `deploy/update-locks.sh`，审查哈希的 diff 后再提交。
- **借鉴上游修复**：
  ```bash
  git remote add upstream https://github.com/public-clis/twitter-cli
  git fetch upstream
  git log 7c634e0..upstream/main
  ```
  逐个审查后再 `git cherry-pick`。如果 `auth.py` 冲突，保留本 fork 的版本。
- **永远不要**运行 `uv tool install twitter-cli`、`pipx install twitter-cli` 或任何 `upgrade`。那会从 PyPI 装上游原版，原版仍会读浏览器 Cookie。
- **最可能先出问题的地方**：X 更换 GraphQL 接口 ID，或者改动返回数据的结构。通常只需要小改 `graphql.py` 里的 `FALLBACK_QUERY_IDS` 或 `parser.py`。

## 泄露应急与卸载
1. 在 X 的「设置 → 安全和账号访问 → 应用和会话 → 会话」里退出对应会话，旧 Cookie 会立即失效。改密码则会退出所有会话。
2. 运行 `sudo -u xtwitter /opt/twitter-safe/bin/twitter-safe-setup --remove`，删除本机保存的凭据。
3. 卸载：`sudo /opt/twitter-safe-src/deploy/uninstall.sh`。加 `--purge` 会同时删除服务账户和凭据。

## 这套方案防不住什么
- 能变成 root 的人或程序：root 能读所有文件。
- Agent 被网页或推文里的指令操控后，用你的账号发推、点赞。它拿不到 token，但能执行写操作，这是你接受的风险。可以让 Agent 在执行写操作前先征求你的确认。
- 锁定版本的 twitter-cli 或其依赖本身有恶意代码：哈希只能保证安装的就是你锁定的那个版本，不能保证那个版本可信。
- 凭据文件在 xtwitter 名下是明文，请开启全盘加密（FileVault 或 LUKS），备份也要加密。
- X 检测到自动化行为后封号。

## 验证情况
已在 Linux 容器（Ubuntu，Python 3.13）中验证：
- 317 个单元测试通过，ruff 和 mypy 检查通过；
- 带哈希的依赖能以只装 wheel 的方式安装，项目能离线构建；
- `install.sh --check` 在 5 种情况下的结果符合预期：干净的 root 克隆通过；文件归其他用户、文件组可写、Python 不归 root、缺少参数，都被拒绝；
- 生成的 sudoers 规则通过 `visudo -cf` 语法检查；
- 用已有账户分别扮演服务账户和 Agent，跑了 23 项隔离检查，全部通过：
  - Agent 读不到凭据，也改不了代码和站点配置；
  - 带文件路径的参数被拒绝；
  - 注入的环境变量和 PYTHONPATH 无效；
  - 代理抓到的流量里没有 token；
  - 可被他人写入的站点配置会被拒绝；
  - 以 root 运行、家目录缺失、非终端输入都会被拒绝。

尚未验证：
- 安装脚本里「创建服务账户」和「写入 sudoers」这两步：测试环境的权限策略不允许修改系统账户和 sudoers；
- macOS 上的全部流程；
- 用真实 Cookie 访问 X：测试环境没有你的 Cookie，网络也无法直连 X。
