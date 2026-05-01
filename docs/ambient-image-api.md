# TRPG 氛围图片生成指南

本文说明 paotuan 的氛围图片生成功能：它解决什么问题、如何配置、什么时候会触发、生成结果保存在哪里，以及启用前需要理解的隐私和费用边界。

## 适用范围

氛围图片只用于跑团视觉辅助，目标是帮助玩家理解当前场景、人物关系、剧情转折和故事情绪。它不是战棋地图，不参与坐标、距离、视线、掩体、回合顺序或规则结算。

这项能力默认关闭。关闭时不会生成图片 prompt，也不会调用图片 API。

## 非目标

- 不接受玩家直接命令生图。
- 不在战斗或战棋模式中生图。
- 不把生成图片当作权威剧情事实。
- 不生成多张候选图。
- 不替代现有 SVG 地图功能。
- 不把 API key 写进配置文件或仓库。

## API 兼容

当前目标 API 是 PackyAPI `gpt-image-2`。官方文档示例支持两条路径：

- `POST /v1/images/generations`
- `POST /v1/chat/completions`

插件通过 `ambient_image_api_mode` 手动选择路径。默认值是 `images`。

`images` 模式会发送类似字段：

```json
{
  "model": "gpt-image-2",
  "prompt": "...",
  "n": 1,
  "size": "1536x1024",
  "quality": "medium",
  "output_format": "png",
  "response_format": "url"
}
```

`chat_completions` 模式会发送：

```json
{
  "model": "gpt-image-2",
  "messages": [
    {"role": "user", "content": "..."}
  ]
}
```

插件会解析 `data[0].url`、`data[0].b64_json`，也会从 Chat Completions 返回文本里的 Markdown 图片链接中提取图片 URL。

## 配置

在 AstrBot 插件配置中设置：

| 配置名 | 默认值 | 说明 |
| --- | --- | --- |
| `ambient_image_enabled` | `false` | 总开关。关闭时不生成 prompt、不调用图片 API。 |
| `ambient_image_api_mode` | `images` | `images` 或 `chat_completions`。 |
| `ambient_image_base_url` | `https://www.packyapi.com` | API base URL，插件会自动拼接 endpoint。 |
| `ambient_image_api_key_env` | `PACKYAPI_SORA_API_KEY` | API key 所在环境变量名。不要填真实 key。 |
| `ambient_image_model` | `gpt-image-2` | 图片生成模型。 |
| `ambient_image_prompt_model` | 空 | 用来生成生图 prompt 的对话模型/provider ID。空值表示使用当前对话模型。 |
| `ambient_image_size` | `1536x1024` | 默认横向 1.5k。 |
| `ambient_image_quality` | `medium` | 默认 medium 质量。 |
| `ambient_image_output_format` | `png` | 保存格式提示。 |
| `ambient_image_response_format` | `url` | Images API 返回 URL 或 `b64_json`。 |
| `ambient_image_timeout_seconds` | `120` | 单次 API 等待时间。超时只跳过本轮图片。 |
| `ambient_image_send_to_chat` | `true` | 成功后是否把图片随本轮回复发送。 |
| `ambient_image_frequency` | `medium` | 普通触发频率：`low`、`medium`、`high`。 |
| `ambient_image_prompt_template` | 空 | 生成生图 prompt 前发送给 prompt 模型的模板。留空时使用内置模板。 |

Windows PowerShell 示例：

```powershell
$env:PACKYAPI_SORA_API_KEY = "你的 PackyAPI Sora 分组令牌"
```

Linux 或 Docker 环境示例：

```bash
export PACKYAPI_SORA_API_KEY="你的 PackyAPI Sora 分组令牌"
```

生产环境应把环境变量写入 AstrBot 服务的运行环境，而不是写入仓库、README、`.env` 或聊天记录。

## 触发规则

氛围图是内部模块，只由跑团节奏触发。玩家说“画一张图”“生成图片”“给 NPC 配图”不会直接触发氛围图。

基础门禁：

- `ambient_image_enabled=false` 时跳过。
- 缺少 API key 时跳过。
- 战斗中或战棋模式中跳过。
- 直接用户生图请求跳过。

普通触发需要先完成预热：

1. 玩家交互超过 10 次，或会话活动持续超过 5 分钟，视为开始频繁交互。
2. 从开始频繁交互后再等待 5 分钟，普通氛围图才可触发。

普通频率：

- `low`：至少 30 轮或 40 分钟，二者先满足即可。
- `medium`：至少 10 轮或 20 分钟，二者先满足即可。
- `high`：至少 5 轮或 5 分钟，二者先满足即可。

特殊触发：

- 故事结尾可以触发一次。
- 暂停和暂停恢复可以触发，但不受普通预热限制，只受独立 2 小时冷却限制。
- 开场图能力已保留为显式内部能力，但第一阶段不会自动启用。后续其它功能明确使用边界后再接入。

暂停和恢复的 2 小时冷却互相独立：暂停只和上一次暂停氛围图比较，恢复只和上一次恢复氛围图比较。也就是说，刚刚恢复生成过图片，不会阻止之后一次暂停触发；刚刚暂停生成过图片，也不会阻止之后一次恢复触发。

## Prompt 生成

插件不会把玩家的话直接当作生图 prompt。流程是：

1. 内部触发条件选中一个故事时刻。
2. 用 `ambient_image_prompt_template` 填充当前场景、记忆摘要、角色、世界标签、故事时刻和故事级视觉风格。
3. 使用配置的 `ambient_image_prompt_model` 生成最终图片 prompt。
4. 如果 `ambient_image_prompt_model` 为空，则使用当前 AstrBot 对话 provider。
5. 最终发送给图片 API 的 prompt 会保存到 metadata。

内置模板采用结构化 prompt：先说明图片目的和单图约束，再提供故事时刻、触发理由、场景、角色、世界标签、故事级风格和输出默认值。这样做是为了让图片模型获得足够具体的主题、地点、人物轮廓、情绪、光线、构图、镜头、材质和色彩信息，同时显式排除文字、UI、战棋地图、规则表、海报标题、logo 和多张图。

可用模板占位符包括：

| 占位符 | 含义 |
| --- | --- |
| `{story_style}` | 当前故事应延续的视觉风格。 |
| `{story_moment}` | 本次被选中的剧情时刻。 |
| `{rationale}` | 为什么此刻值得出氛围图。 |
| `{scene}` | 当前场景状态的裁剪 JSON。 |
| `{memory_summary}` | 压缩后的跑团记忆摘要。 |
| `{characters}` | 主要角色摘要。 |
| `{world_tags}` | 世界设定标签。 |
| `{player_message}` | 玩家本轮发言，只用于理解剧情，不作为生图指令。 |
| `{output_defaults}` | 单图、横向 1.5k、medium 质量等输出默认值。 |

模板渲染只替换已知占位符；未知占位符会原样保留，不会因为模板写了 JSON 花括号而中断跑团。

### 故事级风格

prompt 系统约束要求：

- 只生成有助于氛围、剧情理解或关键场景展示的画面。
- 不画 UI、战棋地图、规则图、表格或文字海报。
- 保持同一跑团故事内的视觉风格一致。
- 不包含 API key、平台 ID、本地路径或其它敏感信息。

风格稳定只作用于同一个跑团故事。插件会用开场时间、开场引导或标题/摘要推导当前故事 key，并把第一次成功生成时的风格描述保存在 `scene["ambient_image_style"]`。如果进入新故事，故事 key 会变化，旧故事的风格不会继续约束新故事。

## 输出与保存

生成成功后，文件保存到 AstrBot 插件数据目录：

```text
data/plugin_data/astrbot_plugin_auto_trpg_dm/ambient_images/
```

每张图旁边会保存一个 `.json` metadata 文件，包含：

- 生成时间。
- 图片文件名。
- 最终生图 prompt。
- prompt 模型/provider。
- 故事时刻和触发理由。
- API mode、图片模型、尺寸、质量、来源。

会话状态中也会记录最近一次氛围图和最近 prompt 历史，方便调试和回看。默认只保留最近 20 条 prompt 记录在会话状态里。

## 失败降级

以下失败都不会中断跑团流程：

- 功能关闭。
- 缺少 API key。
- base URL 或 API mode 配置错误。
- prompt 模型失败。
- 图片 API 超时、网络失败或 HTTP 错误。
- API 返回内容里没有可解析图片。
- URL 图片下载失败。
- 本地图片附件发送失败。

失败时插件会写入审计记录，并继续返回正常 DM 回复。公开聊天里不会输出 API key、Authorization header 或本机绝对路径。

## 隐私与费用边界

启用后，插件会把经过整理的图片 prompt 发送给配置的图片 API。prompt 可能包含当前场景、角色轮廓、地点、情绪、世界标签和剧情摘要。不要在跑团内容中放入不希望发送给外部 API 的敏感信息。

API key 只应存在于 AstrBot 运行环境变量中。不要把真实 key 写入：

- `_conf_schema.json`
- README 或 docs
- `.env`
- Trellis 本地任务文件
- Git commit
- 聊天记录

图片 API 可能产生费用，并可能受到速率限制。默认频率是 `medium`，且功能默认关闭。第一次启用时建议先低频测试，确认计费和返回格式符合预期后再提高频率。

## 排错

如果没有图片：

1. 确认 `ambient_image_enabled=true`。
2. 确认 AstrBot 进程能读取 `ambient_image_api_key_env` 指向的环境变量。
3. 确认 `ambient_image_api_mode` 是 `images` 或 `chat_completions`。
4. 确认当前不在战斗或战棋模式。
5. 普通触发需要先过预热和频率门槛。
6. 暂停触发需要距离上一次暂停氛围图超过 2 小时；恢复触发需要距离上一次恢复氛围图超过 2 小时。
7. 查看插件私有日志和会话 audit，搜索 `ambient_image`。

如果图片生成了但没有发出：

1. 确认 `ambient_image_send_to_chat=true`。
2. 确认 `ambient_images/` 下图片文件存在。
3. 查看日志中是否有 `ambient_image_attached` 或“无法附加”相关记录。
