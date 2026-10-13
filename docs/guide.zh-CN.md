# agent-use-adapters 使用指南

先看 [README](../README.md) 了解它解决什么问题；本文讲安装细节、失败原因和信任模型。

## 安装做了什么

安装脚本把 `config/dispatcher.json` 软链到
`${XDG_CONFIG_HOME:-$HOME/.config}/agent-use/dispatcher.json`，仅接受非空的绝对路径
`XDG_CONFIG_HOME`。重复安装安全，已有的其它文件或软链不会被覆盖。
也可以把 `AGENT_USE_CONFIG` 临时设为本仓库 `config/dispatcher.json` 的完整路径，或自行合并注册表。
软链安装后请保留仓库位置；核心先解析软链，再展开 `{config_dir}`，因此能正确找到适配器。

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
