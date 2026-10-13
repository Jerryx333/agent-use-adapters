# agent-use-adapters

[中文](README.md) | [English](README.en.md)

把你已经登录的 Codex、Claude Code 和免登录的 OpenCode 免费模型接进 [agent-use](https://github.com/Jerryx333/agent-use)：装一次、不用手写配置，就能用同一条命令把任务交给它们，某家不可用时自动换下一家。

## 解决什么问题

- **agent-use 核心只带演示适配器。** 想接真模型，得自己写注册表、摸清每家 CLI 的参数和出错方式。
- **直接在脚本里调这些 CLI 不够稳妥。** 一不小心就带上了工具权限、在工作目录里写文件，或留下一堆会话记录。

## 装上之后有什么不同

- **一条安装命令，三家直接可用。** 已登录的 Codex、Claude Code 拿来就用；OpenCode 的免费模型不用登录。
- **某家不可用时自动换下一家。** 默认按 Codex → Claude Code → OpenCode 的顺序尝试，也可以只用其中一家；你主动中断时整条停下，不会接着试下一家。
- **默认更保守。** 每单在空的临时目录里跑：Codex 用只读沙箱，Claude Code 禁用全部工具，OpenCode 只允许免费模型、拒绝工具调用；用完不保留会话（OpenCode 为尽力清理）。
- **失败原因统一归类。** 未登录、额度耗尽、限流、超时等都有固定的原因码，调用方可以按原因决定下一步。

## 你可能会担心的

**会不会花钱？** 实际调用用的是所选 CLI 自己的账号额度。OpenCode 只允许 `-free` 模型；上游要求付费时直接失败，不会转去付费模型。

**会碰我的账号凭据吗？** 不读取、不保存，认证全部交给各 CLI 自己。

**任务会发到哪？** 发给所选 CLI 及其上游服务；任务和回答以明文存在本机。

**会覆盖我已有的配置吗？** 不会。重复安装安全，已有的其它文件或软链不会被覆盖。

## 30 秒上手

需要 Python 3.11+、Bash 和 POSIX 系统（macOS/Linux），先装好官方 CLI；已有的官方登录直接可用，不用装额外的 Python 包。

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

不写 `providers` 时，默认按 Codex → Claude → OpenCode 顺序尝试，失败后自动换下一家（中断除外）。
可用 profile：`default`、`codex-only`、`claude-only`、`opencode-only`；
例如用 `"profile":"opencode-only"` 替代 `providers` 来选择 OpenCode。

安装把注册表放到哪、怎么改用自己的注册表，见[使用指南](docs/guide.zh-CN.md#安装做了什么)。

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

## 更多文档

- [使用指南](docs/guide.zh-CN.md)：安装细节、失败原因、信任模型。
- agent-use 核心的[调度协议](https://github.com/Jerryx333/agent-use/blob/main/docs/DISPATCHER.md)（英文）。

## 参与贡献

运行 `bash scripts/test.sh` 执行离线检查；改动走 PR，开发说明见 [CONTRIBUTING.md](CONTRIBUTING.md)。许可证：[Apache-2.0](LICENSE)。
