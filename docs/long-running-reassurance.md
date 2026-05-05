# 长耗时等待提示

长耗时等待提示用于处理一种很具体的体验问题：玩家发出 `/dm` 请求后，如果 bot 进入较长的 router、LLM 或工具路径，聊天窗口可能会长时间没有任何反馈。这个功能会在请求超过配置阈值仍未完成时，发送一条很短的状态提示，让玩家知道 bot 仍在处理。

这条提示不是剧情叙述，不是线索，不是进度条，也不是游戏状态更新。

## 玩家可见行为

默认流程：

1. 玩家发送 `/dm` 请求。
2. 本地快速回复、重复请求保护、行动节流回复和安全阻断先执行。
3. 只有请求真正进入长 router、LLM 或工具路径后，插件才会启动一个延迟等待任务。
4. 如果最终 DM 回复在延迟时间内完成，不发送等待提示。
5. 如果请求超过延迟时间仍在运行，并且当前会话不在冷却期内，插件会独立发送一条短文本，例如：

```text
请等待回复：正在整理局势。
```

6. router 最终完成后，正式 DM 回复仍按原流程发送。

v1 对每个玩家请求最多发送一条等待提示。

## 配置项

| 配置项 | 默认值 | 含义 |
| --- | --- | --- |
| `reassurance_enabled` | `true` | 是否启用长耗时等待提示。 |
| `reassurance_delay_seconds` | `30` | 发送等待提示前的延迟秒数。请求在此之前完成则不发送。 |
| `reassurance_cooldown_seconds` | `300` | 同一会话的等待提示冷却时间，避免连续慢请求刷屏。 |
| `reassurance_prefix` | `请等待回复：` | 每条等待提示都会带上的明确状态前缀。建议保留，避免被误读成剧情事实。 |
| `reassurance_phrases` | 内置普通池 | 普通请求使用的等待提示池。 |
| `reassurance_map_phrases` | 内置地图池 | 明确地图、SVG、站位图或战场示意请求使用的等待提示池。 |
| `reassurance_style_phrases_enabled` | `true` | 是否允许根据战役背景选择风格化提示。背景不明确时仍回退到普通提示。 |
| `reassurance_style_phrase_pools` | 内置保守风格池 | 可选 JSON 对象，用于配置风格化提示池。 |

风格池 JSON 示例：

```json
{
  "fantasy": ["少女祈祷中。"],
  "post_apocalyptic": ["正在给旧终端拍灰。"]
}
```

自定义文案在发送前仍会经过本地安全过滤。

## 文案选择顺序

文案选择按以下顺序进行：

1. 如果玩家消息明确要求地图、SVG、战场地图、地形草图或战术布局，使用地图池。
2. 否则，如果启用了风格化提示，并且当前战役元信息能明确匹配某个已知风格池，使用对应风格池。
3. 否则使用普通池。
4. 如果配置的候选文案为空或不安全，回退到内置普通池。

地图判断故意保持保守。v1 的等待任务启动时，router 还没有决定是否真的会调用 `generate_map_svg`，所以只有玩家原始请求明显像地图请求时才使用地图池。

## 安全边界

等待提示必须始终是“处理状态”，不能变成剧情内容。文案不能：

- 暗示隐藏状态；
- 描述新的或尚未确认的场景事实；
- 承诺成功；
- 暗示敌人、避难点、线索、出口、资源或结局已经存在；
- 提供行动建议；
- 把玩家下一步包装成选项菜单。

较安全的例子：

```text
请等待回复：正在翻找合适的骰子。
请等待回复：战场草图还没干。
请等待回复：少女祈祷中。
```

不安全的例子：

```text
请等待回复：敌人正在行动。
请等待回复：正在确认避难点。
请等待回复：你马上会看到两个选择。
```

## 运行期状态

冷却时间保存在插件运行期状态里，按 `session_id` 区分。它不会写入 `scene` 或 `world_tags`，因为这些字段会进入 prompt 上下文，应该继续表示游戏和战役状态，而不是内部消息发送 bookkeeping。

如果插件重启，冷却状态会重置。v1 接受这个限制，因为冷却的目标只是减少短时间聊天噪声。

## 与回合 heartbeat 的关系

这个功能和已有的玩家回合 timeout heartbeat 是两套机制。

回合 heartbeat 处理的是主动回合超时、保守代管行动和场景结算推进。长耗时等待提示只表示“bot 仍在处理当前这一次慢请求”。它不会推进回合，不会改变战斗状态，也不会修改任何游戏事实。

## 日志与审计

插件私有日志会记录 scheduled、cancelled 等生命周期事件。

审计记录只用于可见或决策相关结果：

- `long_running_reassurance_sent`
- `long_running_reassurance_suppressed`
- `long_running_reassurance_send_failed`

审计记录只保存有边界的元数据，例如原因、延迟、冷却、文案来源、文案 hash 和玩家消息 hash。它不会保存完整候选文案，也不会保存完整玩家文本。

## 验证

建议验证命令：

```powershell
python -m pytest -q tests/test_long_running_reassurance.py -p no:cacheprovider
python -m pytest -q tests/test_dm_ack_and_outputs.py tests/test_router_usage.py tests/test_security_dm_guidance.py -p no:cacheprovider
python -m compileall -q astrbot_plugin_auto_trpg_dm tests
```
