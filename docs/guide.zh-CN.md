# agent-use-adapters 使用指南

先看 [README](../README.zh-CN.md) 了解它解决什么问题；本文讲安装细节、失败原因和信任模型。

## 安装做了什么

安装脚本把 `config/dispatcher.json` 软链到 `<配置目录>/agent-use/dispatcher.json`。配置目录默认是 `$HOME/.config`；只有 `XDG_CONFIG_HOME` 非空且为绝对路径时才改用它，相对路径会被忽略、仍用 `$HOME/.config`。重复安装安全，已有的其它文件或软链不会被覆盖。
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

所有适配器都会返回 `interrupted`。只有 `free_tier_rejected`、`payment_required` 和 `tool_use_rejected` 仅适用于 OpenCode。fatal 失败停止当前 provider 的尝试，不代表批准待审稿或授权花费。

## 信任模型

适配器不读取、不保存任何账号凭据，认证全部交给各 CLI 自己。
任务会发给所选 CLI 及其上游服务，任务和回答文件以明文保存在本机。
运行前请审阅适配器和注册表；CLI 的安全设置不会取消进程本身的普通用户权限。
OpenCode 会话删除采用尽力清理方式。

子进程会带上 `AIW_NESTED=1`，表示它是被委派的执行方。
搭配 [aiw](https://github.com/Jerryx333/aiw) 使用时生效，其它情况下无影响。
在 aiw 工作流中，安装本仓库的适配器，并把调度配置指向 agent-use 核心即可。

## ACP 引擎（显式启用）

指定 `opencode-acp` 或 `qwen-acp` 使用；两者均不进入默认 profile。
`adapters/acp_adapter.py` 只用 Python 3.11 标准库，以逐行 JSON-RPC 通信，协议版本固定为 ACP v1。
必填 `options.agent`：`opencode`、`qwen`、`agy`、`gemini`、`cursor`、`grok` 或 `kiro`。
用 `ACP_<AGENT>_BIN`（例如 `ACP_QWEN_BIN`）覆盖程序路径，不接受 shell 命令串。
其余引擎的本机版本及限制能力均为未核实。

输入遵循 [agent-use 请求协议](https://github.com/Jerryx333/agent-use/blob/main/docs/DISPATCHER.md#adapter-protocol)。
默认 `access=none`，不传 `workdir`，在空临时目录运行并拒绝全部审批；
`ro`/`rw` 必须传绝对路径的现有 `workdir`。`ro` 只批准 `read`/`search` 类工具，
`rw` 批准全部类型，优先单次选项。`ro` 遇到未知类型拒绝，无可用选项则返回 `cancelled`。
审批依赖引擎如实上报工具类型并发出请求，不是操作系统沙箱。
不实现客户端 `fs/*`、`terminal/*`，所有未知客户端方法返回 JSON-RPC `-32601`。

客户端始终发送 `mcpServers: []`，不实现 MCP、不续会话。
OpenCode 使用隔离的临时 home/config/data/cache/state，显式设置 `OPENCODE_TEST_HOME`，
并开启 `OPENCODE_DISABLE_EXTERNAL_SKILLS`、`OPENCODE_DISABLE_CLAUDE_CODE`、
`OPENCODE_DISABLE_PROJECT_CONFIG` 和 `OPENCODE_PURE`，关闭外部 skills、用户/项目指令和外部插件。
配置固定 `share: disabled`、`autoupdate: false`、`permission: {"*": "ask"}` 和空 MCP。
工具审批由 ACP 客户端按 `none/ro/rw` 执行，`none` 全拒绝、`ro` 只批读取/搜索。
不要在引擎端改成 `deny`：本机 OpenCode 1.19.6 搭配 `opencode/mimo-v2.6-flash-free`
实测会卡住 prompt；仅改成 `ask`、保留原读取循环后即可返回 PONG。
不导入本机账号或模型配置（已有环境凭据仍可由 CLI 使用）；
Qwen 使用空 setting sources、严格空 MCP 配置及明确的权限设置，
`none` 的内置工具列表为空，`ro` 只保留 `Read,Grep,Glob`；还传入
`--disable-builtin-skills`、`skills.enabled=false`、空 `context.fileName`、关闭自动记忆、hooks 和 agents。
这些 Qwen 设置已核对本机 `1.0.0-preview.1` 内置实现；在线文档可能对应不同版本。
Qwen 保留用户 home 供现有登录使用，仍可能写入 `~/.qwen/projects`；全局/受管指令及插件隔离**未核实**。
OpenCode 的系统受管配置隔离也未核实。具体限制写入 `status.json` 的 `isolation`；
收到的 `available_commands_update` 名称写入 `available_commands` 供检查技能泄漏，不把未收到通知当成空列表。
其他引擎可能自行加载配置，其 MCP 和工具隔离能力**未核实**，用于可信工作区前需配备限制启动器；
仅传空 MCP 列表不能阻止引擎加载自己的 MCP。

显式 `model` 必须精确匹配 `category=model`、`type=select` 的
[配置选项](https://agentclientprotocol.com/protocol/v1/session-config-options)（支持分组值）。
通过 `session/set_config_option` 设置并核对返回值，不支持即 `invalid_request`，
不回退到 `session/set_model` 或其他模型。省略 `model` 时用引擎默认值。
`opencode-acp` 默认明确指定免费模型；ACP 本身不沿用原 `opencode` 适配器的免费模型白名单。
Qwen 注册项使用本机 CLI `--list-models` 公布的 `qmodel_latest`，其 ACP 选择仍待实机验证。

实际要求认证时报 `credential_missing`，仅公布登录方法不算未登录；适配器不发起登录。
ACP 错误的正文及嵌套诊断字符串参与分类，RPC 数字错误码不当成 HTTP 状态。
Qwen 的免费额度耗尽、继续将使用付费额度提示归为 `payment_required`，不继续付费；成功正文不参与错误分类。
协议版本不符或 RPC 格式损坏为 `invalid_protocol`，进程崩溃为 `upstream_failed` 或更具体的 CLI 错误。
超时先发 `session/cancel`，留短暂处理时间，再终止并回收进程组。
完成时写 `response.txt`、`status.json`；失败不把部分回答当成功。
工具事件和审批决策复用有界的 `trajectory.jsonl`。引擎可能保留自己的历史，适配器不实现会话删除或续接。
离线替身为 `tests/fixtures/fake_acp_agent.py`。

离线验收：`bash scripts/test.sh`。实机验收显式运行
`python3 -B scripts/test_acp_live.py --output /tmp/acp-smoke`，
最多串行调用三次，每次上限 180 秒，保留请求和结果；只读用例要求观察到实际写入拒绝，
模型只口头说拒绝不算通过。两个命令均不安装引擎、不登录账号。
额度已耗尽时可加 `--expect-qwen-payment-required`，仅将 Qwen 两项明确返回
`payment_required` 视为预期结果；不代替有额度时的只读功能验收。
