# 手写 Agent Loop 而非使用 LangChain AgentExecutor

我们要一个完全可控的 Agent 执行循环——能精确控制 temperature、轮次上限、tool 结果截断、结构化错误返回和 prompt caching。LangChain 的 AgentExecutor 将这些封装为内部实现，修改任何一个环节都需要绕过其抽象层。

**Considered Options**

- **LangChain AgentExecutor**: 开发快但黑盒，内部状态不可控。prompt caching 需要 hack 其内部消息构造逻辑，结构化错误回传不兼容其异常处理模型。
- **手写 while 循环**: 每行逻辑显式可见，改任何参数都是一行代码。维护成本在可接受范围内（~250 行，含注释）。

**Consequences**

- Agent Loop 通过函数参数接收 tools 和 handlers，不 import 任何全局状态——测试时可传入 mock handler。
- 新增 tool 改 4 处配置，Agent Loop 一行不动（开闭原则）。
- 代价：需要自行维护 streaming 事件解析、消息列表管理、token 预算联动——但这些恰好是需要精确控制的环节，不算额外负担。
