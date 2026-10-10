# Contributing

Use Python 3.11+ and POSIX (macOS/Linux). Python code must use only the standard
library. Run all offline tests and shell syntax checks from the repository root:

```sh
bash scripts/test.sh
```

Tests isolate HOME and never call real models or accounts. If a local agent-use
core is available next to this clone, registry compatibility is tested too;
set `AGENT_USE_HOME` to use another core checkout.

New adapters must implement the core's
[dispatcher protocol](https://github.com/Jerryx333/agent-use/blob/main/docs/DISPATCHER.md),
return drafts with `accepted: false`, and include offline tests using fake
executables or mocks for success, failure, and safety behavior. Document their
requirements, options, and failure reasons, and add their registry entries.

Never include credentials, account details, personal data, or machine-specific
paths in code, fixtures, documentation, or diagnostics committed to this project.
