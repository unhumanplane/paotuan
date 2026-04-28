# AstrBot Auto TRPG DM

基于 AstrBot v4.5.7+ 的全自然语言 TRPG DM 插件脚手架。

这个插件把 AstrBot 当作消息入口和 LLM 能力层，内部实现一个小型 TRPG 运行时：

- 玩家不需要 `/车卡`、`/开团`、`/move` 等命令。
- Intent Router 会按当前模式动态挂载工具。
- 多跳工具循环由 `IntentRouter` 手写驱动，每一步都调用 `context.llm_generate()`，底层仍走 AstrBot 的 LLM Provider。
- 优先使用 AstrBot Function Calling；若某个 Provider 不返回结构化工具调用，会降级解析模型输出的 `{"tool_calls":[...]}` JSON。
- LLM 可注册和复用本地规则函数。
- 战棋坐标、移动、视线和攻击距离由本地空间引擎确定性验证。
- 角色卡与世界设定使用 Tag 型无模式数据结构。

## 目录

```text
astrbot_plugin_auto_trpg_dm/
  main.py
  core/
    router.py
    modes.py
    models.py
    prompts.py
  tools/
    registry.py
    rule_tools.py
    spatial_tools.py
    memory_tools.py
  rules/
    python_runtime.py
    validator.py
    dice.py
  spatial/
    grid.py
    engine.py
    los.py
  storage/
    json_repository.py
tests/
```

## 架构要点

### Intent Router

`core/router.py` 是自然语言入口。它会：

1. 读取当前会话状态。
2. 用 `GameModeStateMachine` 判断模式。
3. 通过 `ToolRegistry` 只挂载当前模式允许的工具。
4. 构造 System Prompt。
5. 调用 `context.llm_generate()` 进行多跳工具循环。
6. 审计玩家输入、工具列表和最终回复。

### 动态 Tool 挂载

工具白名单由 `tools/registry.py` 控制。例如：

- `narrative`：剧情、世界 Tag、角色、规则工具。
- `character_creation`：角色卡、Tag、规则工具。
- `rule_authoring`：规则注册、执行测试、规则列表。
- `tactical`：战棋快照、建图、放置实体、移动、攻击向量、规则执行。
- `resolution`：规则结算、角色 Tag、场景更新。

模型在非战棋模式下不会看到 `move_entity` / `check_attack_vector`。

### Rule Runtime

MVP 使用 `PythonRuleRuntime`：

- 规则必须定义唯一的 `calculate(...)` 函数。
- 禁止 `import`、`open`、`eval`、`exec`、`getattr`、dunder 属性、类定义等危险语法。
- 规则版本化保存到 `data/rules/{rule_name}/v{n}.py`。
- 执行在独立进程中，有超时保护。
- 暴露受控骰子函数 `roll("2d6+1")`。

注意：Python 子集运行时是本地娱乐 MVP 的安全护栏，不是强安全边界。若未来开放给不可信用户，建议替换为 WASM / DSL / 独立隔离服务。

### Spatial Engine

`spatial/` 内部维护二维网格事实：

- 坐标边界。
- 移动阻挡。
- 实体占位。
- BFS 路径与移动力。
- Bresenham 视线。
- 曼哈顿攻击距离。

LLM 只能调用：

- `move_entity(entity_id, target_x, target_y)`
- `check_attack_vector(source_id, target_id)`

不能直接改坐标。

## 部署

把 `astrbot_plugin_auto_trpg_dm/` 放入 AstrBot 插件目录，确保 AstrBot 版本为 v4.5.7+。

运行数据会写入 AstrBot 数据目录：

```text
data/plugin_data/astrbot_plugin_auto_trpg_dm/
  saves/
  rules/
  audit/
```

插件入口是：

```text
astrbot_plugin_auto_trpg_dm/main.py
```

LLM 调用不使用 `requests` 或 `openai`，而是：

```python
await self.context.llm_generate(...)
```

这会由 AstrBot 负责调用当前会话配置的模型。
