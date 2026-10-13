# agent-use-adapters

[English](README.md)

为 [agent-use](https://github.com/Jerryx333/agent-use) 提供开箱即用的 CLI 适配器。
核心保持厂商无关，本仓库放 Codex、Claude Code、OpenCode 的具体适配器，以及一键安装的注册表。

需要 Python 3.11+、Bash 和 POSIX 系统（macOS/Linux）。先安装官方 CLI；已有的官方登录
可以直接使用，无需手写配置或安装额外 Python 包。

## 30 秒上手

把两个仓库克隆到同一个父目录：

```sh
git clone https://github.com/Jerryx333/agent-use.git
git clone https://github.com/Jerryx333/agent-use-adapters.git
cd agent-use-adapters
bash scripts/install.sh
cd ../agent-use
python3 -B scripts/agent_use.py list
printf '%s' '{"version":1,"task":"用一句话解释什么是 CLI。","providers":["codex"],"output":"runs/first"}' | python3 -B scripts/agent_use.py run
```

尚未登录时，运行 `codex login`，或进入 `claude` 后使用 `/login`。
OpenCode 的免费 Zen 模型无需登录。实际调用会使用所选 CLI 的额度。
成功结果仍是待审稿：`state: staged_for_review`、`accepted: false`。
查看 `runs/first/response.txt` 和 `runs/first/status.json`；新任务或重试请使用新的输出目录。

安装脚本把 `config/dispatcher.json` 软链到
`${XDG_CONFIG_HOME:-$HOME/.config}/agent-use/dispatcher.json`，仅接受非空的绝对路径
`XDG_CONFIG_HOME`。重复安装安全，已有的其它文件或软链不会被覆盖。
也可以把 `AGENT_USE_CONFIG` 临时设为本仓库 `config/dispatcher.json` 的完整路径，或自行合并注册表。
软链安装后请保留仓库位置；核心先解析软链，再展开 `{config_dir}`，因此能正确找到适配器。

不写 `providers` 时，默认按 Codex → Claude → OpenCode 顺序尝试，失败后自动顶替。
可用 profile：`default`、`codex-only`、`claude-only`、`opencode-only`；
例如用 `"profile":"opencode-only"` 替代 `providers` 来选择 OpenCode。

## 适配器

| 名称 | 需要什么 / 官方登录 | 安全措施 | 支持的 options |
| --- | --- | --- | --- |
| `codex` | Codex CLI；`codex login` | 空临时目录、只读沙箱、不持久化会话 | `effort`：`minimal`、`low`（默认）、`medium`、`high`、`xhigh` |
| `claude` | Claude Code；在 `claude` 中 `/login` | 空临时目录、禁用全部工具、单轮、不持久化会话 | `effort`：`low`（默认）、`medium`、`high`、`max` |
| `opencode` | 支持 `run --pure` 的 OpenCode CLI；免费 Zen 模型无需登录 | 只允许 `-free` 模型；工具调用需要授权，因此被拒绝；结束后删除观察到的会话；在共享时间预算内按 `fallback_models` 依次备选 | `fallback_models`：逗号分隔的 `-free` 模型名 |

默认模型依次为 `gpt-6.1-sol`、`claude-opus-5-5`、`muse-spark-1.3-contributor-free`。
OpenCode 默认备选为 `mimo-v2.6-flash-free,longcat-2.5-preview-free`。
模型是否可用取决于 CLI 和上游服务。需要指定可执行文件位置时，设置 `CODEX_BIN`、
`CLAUDE_BIN` 或 `OPENCODE_BIN`。

## 失败原因

| reason | 含义 |
| --- | --- |
| `credential_missing` | 未找到 CLI，或需要完成官方认证。 |
| `quota_exhausted` | 账号或服务的使用额度耗尽。 |
| `rate_limited` | 请求超过服务的频率限制。 |
| `network_failed` | 连接、超时、DNS 或归类为网络问题的服务器错误。 |
| `upstream_failed` | CLI 或上游失败，未匹配更具体的分类。 |
| `empty_response` | CLI 完成，但没有可用文本。 |
| `timeout` | 适配器执行超出时间预算。 |
| `invalid_request` | 协议、options、模型或其它必要请求字段无效。 |
| `free_tier_rejected` | OpenCode 上游拒绝免费档访问。 |
| `payment_required` | OpenCode 上游要求付费或补充余额。 |
| `tool_use_rejected` | OpenCode 尝试调用工具，且没有可用的最终文本。 |
| `interrupted` | 执行被取消或收到终止信号；核心据此停止整条顶替链。 |

三个适配器都会返回 `interrupted`。只有 `free_tier_rejected`、`payment_required` 和 `tool_use_rejected` 仅适用于 OpenCode。fatal 失败停止当前 provider 的尝试，不代表批准待审稿或授权花费。

## 信任模型

适配器不读取、不保存任何账号凭据，认证全部交给各 CLI 自己。
任务会发给所选 CLI 及其上游服务，任务和回答文件以明文保存在本机。
运行前请审阅适配器和注册表；CLI 的安全设置不会取消进程本身的普通用户权限。
OpenCode 会话删除采用尽力清理方式。

子进程会带上 `AIW_NESTED=1`，表示它是被委派的执行方。
搭配 [aiw](https://github.com/Jerryx333/aiw) 使用时生效，其它情况下无影响。
在 aiw 工作流中，安装本仓库的适配器，并把调度配置指向 agent-use 核心即可。

开发说明见 [CONTRIBUTING.md](CONTRIBUTING.md)，协议见核心的
[DISPATCHER.md](https://github.com/Jerryx333/agent-use/blob/main/docs/DISPATCHER.md)。
运行 `bash scripts/test.sh` 执行离线检查。

许可证为 [Apache-2.0](LICENSE)。
