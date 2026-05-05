from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict

from pydantic import BaseModel, Field

from ..core.ambient_image import (
    AmbientImageConfig,
    AmbientImageProvider,
    redact_ambient_image_text,
)
from ..core.models import GameMode, utc_now_iso
from ..core.plugin_log import get_plugin_logger
from ..storage.json_repository import JsonGameRepository


PromptLlmGenerate = Callable[..., Awaitable[Any]]
PAUSE_RESUME_TRIGGER_WINDOW_MINUTES = 10
AMBIENT_IMAGE_GENERATION_IN_PROGRESS_MINUTES = 5
RECENT_PLAYER_MESSAGES_LIMIT = 50


class GenerateAmbientImageArgs(BaseModel):
    story_moment: str = Field(
        default="",
        description="DM 选择的叙事时刻摘要。不要照抄玩家命令；用于提炼氛围图主题。",
    )
    rationale: str = Field(
        default="",
        description="为什么此刻值得生成氛围图，例如新地点、关键 NPC 出场、剧情转折或重要发现。",
    )
    send_to_chat: bool = Field(default=True, description="是否把生成图片随本轮回复发送")


class AmbientImageTools:
    def __init__(
        self,
        repository: JsonGameRepository,
        session_id: str,
        config: AmbientImageConfig,
        provider: AmbientImageProvider,
        *,
        actor: dict[str, str] | None = None,
        message: str = "",
        llm_generate: PromptLlmGenerate | None = None,
        chat_provider_id: str = "",
    ):
        self.repository = repository
        self.session_id = session_id
        self.config = config
        self.provider = provider
        self.actor = actor or {}
        self.message = message
        self.llm_generate = llm_generate
        self.chat_provider_id = chat_provider_id

    async def generate_ambient_image(
        self,
        story_moment: str = "",
        rationale: str = "",
        send_to_chat: bool = True,
        trigger_override: str = "",
        ignore_generation_in_progress: bool = False,
    ) -> Dict[str, Any]:
        session = self.repository.load_session(self.session_id)
        provider_unavailable = _provider_unavailable(self.provider)
        if provider_unavailable:
            result = {
                **audit_safe_ambient_image_result(provider_unavailable),
                "message": "氛围图这轮没有生成；跑团流程继续。",
            }
            self._audit("generate_ambient_image", locals_without_self(locals()), result)
            return result

        gate = ambient_image_gate(
            session,
            self.config,
            trigger_override=trigger_override,
            ignore_generation_in_progress=ignore_generation_in_progress,
        )
        if not gate.get("ok"):
            result = gate
            self._audit("generate_ambient_image", locals_without_self(locals()), result)
            return result

        final_send_to_chat = bool(send_to_chat and self.config.send_to_chat)
        prompt_result = await self._build_image_prompt(session, story_moment=story_moment, rationale=rationale)
        if not prompt_result.get("ok"):
            self._audit("generate_ambient_image", locals_without_self(locals()), prompt_result)
            return prompt_result

        image_prompt = str(prompt_result.get("prompt", "")).strip()
        provider_result = await self.provider.generate(image_prompt)
        if not provider_result.get("ok") or not provider_result.get("available"):
            result = {
                **audit_safe_ambient_image_result(provider_result),
                "message": "氛围图这轮没有生成；跑团流程继续。",
            }
            self._audit("generate_ambient_image", locals_without_self(locals()), result)
            return result

        image_bytes = provider_result.get("image_bytes")
        if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
            result = {"ok": False, "available": False, "error": "ambient_image_empty_bytes"}
            self._audit("generate_ambient_image", locals_without_self(locals()), result)
            return result

        image_path = self._write_image(bytes(image_bytes), provider_result)
        metadata_path = self._write_metadata(
            image_path,
            image_prompt=image_prompt,
            prompt_result=prompt_result,
            provider_result=provider_result,
            rationale=rationale,
            story_moment=story_moment,
        )

        latest_session = self.repository.load_session(self.session_id)
        record = {
            "type": "ambient_image",
            "title": str(prompt_result.get("title") or "氛围图")[:80],
            "name": image_path.name,
            "path": str(image_path),
            "metadata_path": str(metadata_path),
            "created_at": utc_now_iso(),
            "visual_only": True,
            "prompt": image_prompt,
            "prompt_model": prompt_result.get("prompt_model", ""),
            "story_key": prompt_result.get("story_key", ""),
            "style_seed": prompt_result.get("style_seed", ""),
            "api_mode": provider_result.get("api_mode", ""),
            "size": provider_result.get("size", self.config.size),
            "quality": provider_result.get("quality", self.config.quality),
            "source": provider_result.get("source", ""),
        }
        if prompt_result.get("prompt_retry_reason"):
            record["prompt_retry_reason"] = prompt_result.get("prompt_retry_reason", "")
        if prompt_result.get("prompt_similarity") is not None:
            record["prompt_similarity"] = prompt_result.get("prompt_similarity")
        if prompt_result.get("retry_source_player_id"):
            record["retry_source_player_id"] = prompt_result.get("retry_source_player_id", "")
        latest_session.scene["last_ambient_image"] = record
        latest_session.scene["ambient_image_state"] = {
            **dict(latest_session.scene.get("ambient_image_state") or {}),
            "last_generated_at": record["created_at"],
            "last_turn_index": _ambient_turn_index(latest_session),
            "frequency": self.config.frequency,
        }
        mark_ambient_image_trigger(latest_session, str(gate.get("trigger") or "interval"))
        _remember_ambient_style(latest_session, str(prompt_result.get("style_seed") or ""))
        history = list(latest_session.scene.get("ambient_image_prompts") or [])
        history.append(
            {
                "created_at": record["created_at"],
                "prompt": image_prompt,
                "title": record["title"],
                "file_name": image_path.name,
                "api_mode": record["api_mode"],
                "story_key": record["story_key"],
                "prompt_retry_reason": prompt_result.get("prompt_retry_reason", ""),
                "retry_source_player_id": prompt_result.get("retry_source_player_id", ""),
            }
        )
        latest_session.scene["ambient_image_prompts"] = history[-20:]
        self.repository.save_session(latest_session)

        result = {
            "ok": True,
            "available": True,
            "title": record["title"],
            "file_path": str(image_path),
            "file_name": image_path.name,
            "metadata_path": str(metadata_path),
            "send_to_chat": final_send_to_chat,
            "api_mode": record["api_mode"],
            "visual_only": True,
            "message": "氛围图已生成；它只是视觉辅助，不改变跑团事实。",
        }
        self._audit("generate_ambient_image", locals_without_self(locals()), result)
        get_plugin_logger().info(
            "ambient_image_completed session=%s file=%s api_mode=%s send_to_chat=%s",
            self.session_id,
            image_path,
            record["api_mode"],
            final_send_to_chat,
        )
        return result

    async def _build_image_prompt(self, session: Any, *, story_moment: str, rationale: str) -> dict[str, Any]:
        if self.llm_generate is None:
            return {"ok": False, "available": False, "error": "ambient_image_prompt_model_missing"}
        prompt_model = str(self.config.prompt_model or self.chat_provider_id or "").strip()
        story_key = _ambient_story_key(session)
        style_seed = _ambient_style_seed(session)
        initial_player_id = str(self.actor.get("player_id") or "").strip()
        prompt_result = await self._request_prompt_for_player_message(
            session,
            player_message=self.message,
            prompt_model=prompt_model,
            story_key=story_key,
            style_seed=style_seed,
            story_moment=story_moment,
            rationale=rationale,
        )
        if not prompt_result.get("ok"):
            return prompt_result

        similarity = await self._judge_prompt_similarity(session, prompt_result, prompt_model=prompt_model)
        if not similarity.get("too_similar"):
            if similarity.get("judge_degraded"):
                prompt_result["prompt_similarity_judge_degraded"] = True
                prompt_result["prompt_similarity_reason"] = similarity.get("reason", "")
            return prompt_result

        if not bool(self.config.similarity_retry_enabled):
            return _prompt_too_similar_result(similarity, retry_attempted=False)

        alternate_source = _select_alternate_player_message(
            session,
            initial_player_id=initial_player_id,
            initial_message=self.message,
        )
        if not alternate_source:
            return {
                **_prompt_too_similar_result(similarity, retry_attempted=False),
                "alternate_source_missing": True,
            }

        retry_result = await self._request_prompt_for_player_message(
            session,
            player_message=str(alternate_source.get("message", "")),
            prompt_model=str(prompt_result.get("prompt_model") or prompt_model),
            story_key=story_key,
            style_seed=style_seed,
            story_moment=story_moment,
            rationale=rationale,
        )
        if not retry_result.get("ok"):
            return retry_result

        retry_similarity = await self._judge_prompt_similarity(
            session,
            retry_result,
            prompt_model=str(retry_result.get("prompt_model") or prompt_model),
        )
        if retry_similarity.get("too_similar"):
            return _prompt_too_similar_result(retry_similarity, retry_attempted=True)
        retry_result["prompt_retry_reason"] = "ambient_image_prompt_similar_recent"
        retry_result["prompt_similarity"] = similarity.get("score", 0.0)
        retry_result["prompt_similarity_reason"] = similarity.get("reason", "")
        retry_result["retry_attempted"] = True
        retry_result["retry_source_player_id"] = alternate_source.get("player_id", "")
        if retry_similarity.get("judge_degraded"):
            retry_result["prompt_similarity_judge_degraded"] = True
            retry_result["prompt_similarity_reason"] = retry_similarity.get("reason", "")
        return retry_result

    async def _request_prompt_for_player_message(
        self,
        session: Any,
        *,
        player_message: str,
        prompt_model: str,
        story_key: str,
        style_seed: str,
        story_moment: str,
        rationale: str,
    ) -> dict[str, Any]:
        generation_prompt = build_ambient_image_prompt_request(
            session,
            player_message=player_message,
            dm_story_moment=story_moment,
            rationale=rationale,
            style_seed=style_seed,
            prompt_template=self.config.prompt_template,
        )
        return await self._request_prompt_result(
            session,
            generation_prompt=generation_prompt,
            prompt_model=prompt_model,
            story_key=story_key,
            style_seed=style_seed,
        )

    async def _judge_prompt_similarity(
        self,
        session: Any,
        prompt_result: dict[str, Any],
        *,
        prompt_model: str,
    ) -> dict[str, Any]:
        recent_count = _clamp_int(self.config.similarity_recent_count, 1, 10)
        threshold = _clamp_float(self.config.similarity_threshold, 0.0, 1.0)
        recent_prompts = _recent_ambient_prompts(session, limit=recent_count)
        if not recent_prompts:
            return {"too_similar": False, "score": 0.0, "similar_to_recent_count": 0}
        try:
            judgment = await self._request_similarity_judgment(
                candidate=prompt_result,
                recent_prompts=recent_prompts,
                prompt_model=prompt_model,
                threshold=threshold,
            )
        except Exception as exc:
            return {
                "too_similar": False,
                "score": 0.0,
                "similar_to_recent_count": 0,
                "judge_degraded": True,
                "reason": redact_ambient_image_text(str(exc), limit=200),
            }
        if not judgment.get("ok"):
            return {
                "too_similar": False,
                "score": 0.0,
                "similar_to_recent_count": 0,
                "judge_degraded": True,
                "reason": judgment.get("reason", "semantic_judge_failed"),
            }
        score = _clamp_float(judgment.get("score", 1.0 if judgment.get("too_similar") else 0.0), 0.0, 1.0)
        return {
            "too_similar": score >= threshold,
            "score": round(score, 4),
            "similarity_threshold": threshold,
            "similar_to_recent_count": 1 if score >= threshold else 0,
            "matched_recent_index": _safe_int(judgment.get("matched_recent_index"), -1),
            "reason": str(judgment.get("reason") or ""),
        }

    async def _request_similarity_judgment(
        self,
        *,
        candidate: dict[str, Any],
        recent_prompts: list[dict[str, Any]],
        prompt_model: str,
        threshold: float,
    ) -> dict[str, Any]:
        if self.llm_generate is None:
            return {"ok": False, "reason": "ambient_image_prompt_model_missing"}
        kwargs: dict[str, Any] = {
            "prompt": build_ambient_image_similarity_judge_prompt(
                candidate=candidate,
                recent_prompts=recent_prompts,
                threshold=threshold,
            ),
            "contexts": [],
            "system_prompt": AMBIENT_IMAGE_SIMILARITY_SYSTEM,
        }
        if prompt_model:
            kwargs["chat_provider_id"] = prompt_model
        try:
            response = await self.llm_generate(**kwargs)
        except TypeError as exc:
            if "chat_provider_id" not in kwargs:
                return {"ok": False, "reason": redact_ambient_image_text(str(exc), limit=200)}
            kwargs.pop("chat_provider_id", None)
            try:
                response = await self.llm_generate(**kwargs)
            except Exception as retry_exc:
                return {"ok": False, "reason": redact_ambient_image_text(str(retry_exc), limit=200)}
        except Exception as exc:
            return {"ok": False, "reason": redact_ambient_image_text(str(exc), limit=200)}
        raw_text = getattr(response, "completion_text", "") or str(response)
        return parse_prompt_similarity_judgment(raw_text)

    async def _request_prompt_result(
        self,
        session: Any,
        *,
        generation_prompt: str,
        prompt_model: str,
        story_key: str,
        style_seed: str,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "prompt": generation_prompt,
            "contexts": [],
            "system_prompt": AMBIENT_IMAGE_PROMPT_SYSTEM,
        }
        if prompt_model:
            kwargs["chat_provider_id"] = prompt_model
        try:
            response = await self.llm_generate(**kwargs)
        except TypeError as exc:
            if "chat_provider_id" not in kwargs:
                return {
                    "ok": False,
                    "available": False,
                    "error": "ambient_image_prompt_model_failed",
                    "reason": redact_ambient_image_text(str(exc), limit=200),
                }
            kwargs.pop("chat_provider_id", None)
            try:
                response = await self.llm_generate(**kwargs)
            except Exception as retry_exc:
                return {
                    "ok": False,
                    "available": False,
                    "error": "ambient_image_prompt_model_failed",
                    "reason": redact_ambient_image_text(str(retry_exc), limit=200),
                }
            prompt_model = self.chat_provider_id
        except Exception as exc:
            return {
                "ok": False,
                "available": False,
                "error": "ambient_image_prompt_model_failed",
                "reason": redact_ambient_image_text(str(exc), limit=200),
            }

        raw_text = getattr(response, "completion_text", "") or str(response)
        return _compose_prompt_result(
            session,
            raw_text=raw_text,
            prompt_model=prompt_model,
            story_key=story_key,
            style_seed=style_seed,
        )

    def _write_image(self, image_bytes: bytes, provider_result: dict[str, Any]) -> Path:
        images_dir = self.repository.ambient_images_dir()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        extension = str(provider_result.get("extension") or "png").strip().lower().lstrip(".") or "png"
        file_name = f"{stamp}_ambient.{extension}"
        path = images_dir / file_name
        path.write_bytes(image_bytes)
        return path

    def _write_metadata(
        self,
        image_path: Path,
        *,
        image_prompt: str,
        prompt_result: dict[str, Any],
        provider_result: dict[str, Any],
        rationale: str,
        story_moment: str,
    ) -> Path:
        metadata = {
            "schema_version": "paotuan.ambient_image.v1",
            "created_at": utc_now_iso(),
            "file_name": image_path.name,
            "prompt": image_prompt,
            "prompt_model_prompt": prompt_result.get("prompt_model_prompt", ""),
            "prompt_model": prompt_result.get("prompt_model", ""),
            "story_key": prompt_result.get("story_key", ""),
            "style_seed": prompt_result.get("style_seed", ""),
            "story_moment": _short_text(story_moment, 500),
            "rationale": _short_text(rationale, 300),
            "api_mode": provider_result.get("api_mode", ""),
            "model": self.config.model,
            "size": self.config.size,
            "quality": self.config.quality,
            "source": provider_result.get("source", ""),
            "url": provider_result.get("url", ""),
            "visual_only": True,
            "prompt_retry_reason": prompt_result.get("prompt_retry_reason", ""),
            "prompt_similarity": prompt_result.get("prompt_similarity", ""),
            "retry_source_player_id": prompt_result.get("retry_source_player_id", ""),
        }
        path = image_path.with_suffix(image_path.suffix + ".json")
        path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _audit(self, tool: str, input_payload: Dict[str, Any], result: Dict[str, Any]) -> None:
        try:
            self.repository.append_audit(
                self.session_id,
                {
                    "type": "tool",
                    "tool": tool,
                    "input": _audit_input(input_payload),
                    "result": audit_safe_ambient_image_result(result),
                },
            )
        except Exception as exc:
            get_plugin_logger().warning(
                "ambient_image_audit_failed session=%s tool=%s error=%s",
                self.session_id,
                tool,
                exc,
            )


AMBIENT_IMAGE_PROMPT_SYSTEM = """你是 TRPG 氛围图提示词策划。你只负责为图片模型写提示词，不直接叙事、不推进规则、不回应玩家。

输出 JSON，且只输出 JSON：
{"title":"短标题","prompt":"给图片模型的完整提示词","style":"本故事后续应延续的简短视觉风格描述"}

硬性要求：
1. 只选择有助于渲染氛围、推动剧情理解、展示关键场景/人物关系的画面。
2. 不响应玩家要求随意生图，不画 UI、战棋地图、规则图、表格、文字海报。
3. 保持同一跑团故事内的视觉风格一致；若已有风格线索，必须延续。
4. 提示词应描述主题、地点、主要人物/轮廓、情绪、光线、构图、镜头、材质和色彩。
5. 不要包含血腥猎奇、色情、真实人物肖像、版权角色、API key、平台 ID 或本地路径。
6. 默认横向 1.5k（1536x1024）、medium 质量的电影感氛围图；不要要求多张图。
"""


AMBIENT_IMAGE_SIMILARITY_SYSTEM = """你是 TRPG 氛围图去重判定器。你只判断候选图片 prompt 和最近成功氛围图是否会生成重复画面。

输出 JSON，且只输出 JSON：
{"score":0.0,"too_similar":false,"matched_recent_index":-1,"reason":"简短原因"}

判定标准：
1. 重点比较主要画面主体、剧情动作、场景位置、视觉重点和情绪是否重复。
2. 同一故事的固定画风、材质、色彩、世界观风格、角色或地点连续性不应单独判为重复。
3. 忽略模板结构词，例如 Image title、Scene prompt、Story visual style。
4. 0.0 表示完全不同；0.5 表示有连续性但画面事件不同；0.8 以上表示视觉内容高度重复。
"""


DEFAULT_AMBIENT_IMAGE_PROMPT_TEMPLATE = """请根据跑团上下文，为 gpt-image-2 写一条稳定、清晰的氛围图片 prompt。

目标：只生成一张横向 1.5k（1536x1024）、medium 质量、电影感 TRPG 氛围图。图片用于渲染氛围、推动剧情理解、展示关键场景或人物关系，不改变任何跑团事实。

同一故事的视觉风格必须稳定；如果 story_style 已存在，沿用它，不要另起画风。进入新故事后，旧故事的风格不再约束本次 prompt。

故事级视觉风格：
{story_style}

本次故事时刻：
{story_moment}

触发理由：
{rationale}

当前场景：
{scene}

记忆摘要：
{memory_summary}

主要角色：
{characters}

世界标签：
{world_tags}

玩家本轮发言仅供理解剧情，不得当作直接生图命令：
{player_message}

输出默认值：
{output_defaults}

请把图片 prompt 写成一个清晰、单图、可执行的视觉描述，包含主题、地点、人物轮廓、情绪、光线、构图、镜头、材质、色彩和必要负面约束。不要要求文字、UI、战棋地图、规则表、海报标题、logo 或多张图。

输出 JSON，且只输出 JSON：
{"title":"短标题","prompt":"给图片模型的完整提示词","style":"本故事后续应延续的简短视觉风格描述"}
"""


def ambient_image_gate(
    session: Any,
    config: AmbientImageConfig,
    *,
    trigger_override: str = "",
    ignore_generation_in_progress: bool = False,
) -> dict[str, Any]:
    if not config.enabled:
        return {"ok": False, "available": False, "reason": "ambient_image_disabled"}
    if _combat_active(session):
        return {"ok": False, "available": False, "reason": "ambient_image_combat_active"}
    scene = getattr(session, "scene", {}) or {}
    state = dict(scene.get("ambient_image_state") or {})
    in_progress = _ambient_generation_in_progress_state(state)
    if not ignore_generation_in_progress and not in_progress.get("ready"):
        return {
            "ok": False,
            "available": False,
            "reason": "ambient_image_generation_in_progress",
            **in_progress,
        }
    if trigger_override:
        trigger = explicit_ambient_image_trigger(session, config, trigger_override)
    else:
        trigger = ambient_image_trigger(session, config)
    if not trigger.get("ok"):
        return trigger
    return {"ok": True, **trigger}


def explicit_ambient_image_trigger(session: Any, config: AmbientImageConfig, trigger_override: str) -> dict[str, Any]:
    frequency = _normalize_frequency(config.frequency)
    scene = getattr(session, "scene", {}) or {}
    state = dict(scene.get("ambient_image_state") or {})
    trigger = str(trigger_override or "").strip().lower()
    if trigger == "opening":
        opening_key = _story_opening_key(session)
        if not opening_key:
            return {"ok": False, "available": False, "reason": "ambient_image_opening_not_ready"}
        if state.get("opening_generated_key") == opening_key:
            return {"ok": False, "available": False, "reason": "ambient_image_opening_already_generated"}
        return {"ok": True, "trigger": "opening", "opening_key": opening_key, "frequency": frequency}
    if trigger == "ending":
        ending_key = _story_ending_key(session)
        if not ending_key:
            return {"ok": False, "available": False, "reason": "ambient_image_ending_not_ready"}
        if state.get("ending_generated_key") == ending_key:
            return {"ok": False, "available": False, "reason": "ambient_image_ending_already_generated"}
        return {"ok": True, "trigger": "ending", "ending_key": ending_key, "frequency": frequency}
    if trigger == "pause_resume":
        pause_resume = _pause_resume_trigger_result(session, state, frequency, strict=True)
        if pause_resume:
            return pause_resume
        return {"ok": False, "available": False, "reason": "ambient_image_pause_resume_not_ready"}
    if trigger == "manual":
        return {"ok": True, "trigger": "manual", "frequency": frequency}
    return {
        "ok": False,
        "available": False,
        "reason": "ambient_image_trigger_override_invalid",
        "trigger_override": trigger,
    }


def ambient_image_trigger(session: Any, config: AmbientImageConfig) -> dict[str, Any]:
    frequency = _normalize_frequency(config.frequency)
    scene = getattr(session, "scene", {}) or {}
    state = dict(scene.get("ambient_image_state") or {})
    pause_resume = _pause_resume_trigger_result(session, state, frequency, strict=False)
    if pause_resume:
        return pause_resume
    ending_key = _story_ending_key(session)
    if ending_key and state.get("ending_generated_key") != ending_key:
        return {"ok": True, "trigger": "ending", "frequency": frequency}
    warmup = _ambient_warmup_state(state)
    if not warmup.get("ready"):
        return {
            "ok": False,
            "available": False,
            "reason": "ambient_image_warmup_wait",
            **warmup,
        }
    current_turn = _ambient_turn_index(session)
    last_turn = _safe_int(state.get("last_turn_index"), 0)
    last_at = str(state.get("last_generated_at", "") or "")
    min_turns, min_minutes = _frequency_thresholds(frequency)
    turns_elapsed = current_turn - last_turn if last_turn else min_turns
    minutes_elapsed = _minutes_since(last_at)
    if last_turn and turns_elapsed < min_turns and minutes_elapsed < min_minutes:
        return {
            "ok": False,
            "available": False,
            "reason": "ambient_image_frequency_wait",
            "frequency": frequency,
            "turns_elapsed": turns_elapsed,
            "minutes_elapsed": minutes_elapsed,
            "min_turns": min_turns,
            "min_minutes": min_minutes,
        }
    activity = _ambient_activity_state(session, config)
    if not activity.get("ready"):
        return {
            "ok": False,
            "available": False,
            "reason": "ambient_image_activity_wait",
            **activity,
        }
    return {"ok": True, "trigger": "interval", "frequency": frequency}


def should_offer_ambient_image(
    session: Any,
    config: AmbientImageConfig,
    mode: GameMode,
    *,
    player_message: str = "",
    ignore_generation_in_progress: bool = False,
) -> bool:
    if mode != GameMode.NARRATIVE:
        return False
    gate = ambient_image_gate(
        session,
        config,
        ignore_generation_in_progress=ignore_generation_in_progress,
    )
    if not gate.get("ok"):
        return False
    if _looks_like_direct_ambient_image_request(player_message):
        return False
    if gate.get("trigger") in {"pause_resume", "ending"}:
        return True
    scene = getattr(session, "scene", {}) or {}
    if not _campaign_has_started(session):
        return False
    summary = str(scene.get("summary", "") or "")
    conflict = str(scene.get("current_conflict", "") or "")
    text = f"{summary}\n{conflict}".lower()
    return bool(text.strip()) and not _looks_like_setup_only(text)


def update_ambient_image_activity_state(
    session: Any,
    actor: dict[str, str] | None = None,
    player_message: str = "",
) -> None:
    scene = getattr(session, "scene", {}) or {}
    now = utc_now_iso()
    state = dict(scene.get("ambient_image_state") or {})
    state.setdefault("first_interaction_at", now)
    interaction_count = _safe_int(state.get("interaction_count"), 0) + 1
    state["interaction_count"] = interaction_count
    state["last_interaction_at"] = now
    if not state.get("warmup_started_at"):
        first_at = str(state.get("first_interaction_at", "") or "")
        if interaction_count > 10 or _minutes_since(first_at) >= 5:
            state["warmup_started_at"] = now
    scene["ambient_image_state"] = state
    _remember_recent_player_message(scene, actor or {}, player_message, now)


def mark_ambient_image_trigger(session: Any, trigger: str) -> None:
    scene = getattr(session, "scene", {}) or {}
    state = dict(scene.get("ambient_image_state") or {})
    if trigger == "opening":
        key = _story_opening_key(session)
        if key:
            state["opening_generated_key"] = key
    elif trigger == "ending":
        key = _story_ending_key(session)
        if key:
            state["ending_generated_key"] = key
    elif trigger == "pause_resume":
        event = _pause_resume_event(session)
        key = event.get("key", "")
        kind = event.get("kind", "")
        if key and kind in {"pause", "resume"}:
            state[f"last_{kind}_key"] = key
            state[f"last_{kind}_generated_at"] = utc_now_iso()
    elif trigger == "manual":
        state["last_manual_generated_at"] = utc_now_iso()
    scene["ambient_image_state"] = state


def build_ambient_image_prompt_request(
    session: Any,
    *,
    player_message: str,
    dm_story_moment: str,
    rationale: str,
    style_seed: str,
    prompt_template: str = "",
) -> str:
    snapshot = session.compact_snapshot() if hasattr(session, "compact_snapshot") else {}
    values = {
        "session_title": _short_text(getattr(session, "title", ""), 120),
        "scene": _prompt_json(snapshot.get("scene", {}), 1800),
        "memory_summary": _short_text(snapshot.get("memory_summary", ""), 1200),
        "characters": _prompt_json(snapshot.get("characters", [])[:8], 1800),
        "world_tags": _prompt_json(snapshot.get("world_tags", {}), 1600),
        "player_message": _short_text(player_message, 500),
        "story_moment": _short_text(dm_story_moment, 700),
        "dm_story_moment": _short_text(dm_story_moment, 700),
        "rationale": _short_text(rationale, 300),
        "story_style": _short_text(style_seed, 800),
        "style_seed": _short_text(style_seed, 800),
        "story_key": _ambient_story_key(session),
        "output_defaults": _prompt_json({
            "count": 1,
            "size": "1536x1024",
            "quality": "medium",
            "purpose": "ambient story illustration only",
        }, 500),
    }
    return render_ambient_image_prompt_template(prompt_template, values)


def parse_prompt_model_output(text: str) -> dict[str, str]:
    payload = _extract_json_object(text)
    if payload:
        try:
            data = json.loads(payload)
            prompt = _short_text(str(data.get("prompt", "") or ""), 2400)
            title = _short_text(str(data.get("title", "") or "氛围图"), 80)
            style = _short_text(
                str(data.get("style") or data.get("style_seed") or data.get("visual_style") or ""),
                500,
            )
            return {"prompt": prompt, "title": title, "style": style}
        except json.JSONDecodeError:
            pass
    cleaned = _short_text(text, 2400)
    return {"prompt": cleaned, "title": "氛围图"} if cleaned else {}


def build_ambient_image_similarity_judge_prompt(
    *,
    candidate: dict[str, Any],
    recent_prompts: list[dict[str, Any]],
    threshold: float,
) -> str:
    lines = [
        "请判断候选氛围图 prompt 是否与最近成功氛围图在视觉内容上过于相似。",
        f"相似阈值：{float(threshold):.2f}",
        "",
        "候选：",
        f"title={_short_text(candidate.get('title', ''), 100)}",
        f"prompt={_short_text(candidate.get('prompt', ''), 1800)}",
        "",
        "最近成功氛围图：",
    ]
    for index, item in enumerate(recent_prompts):
        title = _short_text(item.get("title", ""), 80)
        prompt = _short_text(item.get("prompt", ""), 900)
        lines.append(f"- index={index}; title={title}; prompt={prompt}")
    lines.append("")
    lines.append('只输出 JSON：{"score":0.0,"too_similar":false,"matched_recent_index":-1,"reason":"简短原因"}')
    return "\n".join(lines)


def parse_prompt_similarity_judgment(text: str) -> dict[str, Any]:
    payload = _extract_json_object(text)
    if not payload:
        return {"ok": False, "reason": "similarity_judge_invalid_json"}
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return {"ok": False, "reason": "similarity_judge_invalid_json"}
    score = _clamp_float(data.get("score", 1.0 if data.get("too_similar") else 0.0), 0.0, 1.0)
    return {
        "ok": True,
        "score": score,
        "too_similar": bool(data.get("too_similar", score >= 0.82)),
        "matched_recent_index": _safe_int(data.get("matched_recent_index"), -1),
        "reason": _short_text(data.get("reason", ""), 240),
    }


def _compose_prompt_result(
    session: Any,
    *,
    raw_text: str,
    prompt_model: str,
    story_key: str,
    style_seed: str,
) -> dict[str, Any]:
    parsed = parse_prompt_model_output(raw_text)
    if not parsed.get("prompt"):
        return {"ok": False, "available": False, "error": "ambient_image_prompt_empty"}
    locked_story_style = _ambient_existing_story_style(session)
    title = parsed.get("title", "氛围图")
    prompt_model_prompt = parsed["prompt"]
    effective_style = locked_story_style or parsed.get("style") or style_seed
    image_prompt = compose_ambient_image_prompt(
        title=title,
        prompt=prompt_model_prompt,
        style=effective_style,
    )
    if not image_prompt:
        return {"ok": False, "available": False, "error": "ambient_image_prompt_empty"}
    return {
        "ok": True,
        "prompt": image_prompt,
        "prompt_model_prompt": prompt_model_prompt,
        "title": title,
        "style_seed": effective_style,
        "story_key": story_key,
        "prompt_model": prompt_model,
        "raw_excerpt": redact_ambient_image_text(raw_text, limit=300),
    }


def compose_ambient_image_prompt(*, title: str, prompt: str, style: str) -> str:
    prompt_text = _short_text(prompt, 2400)
    if not prompt_text:
        return ""
    title_text = _short_text(title or "氛围图", 80)
    style_text = _short_text(style, 500)
    sections = [
        f"Image title: {title_text}",
        f"Scene prompt:\n{prompt_text}",
    ]
    if style_text:
        sections.append(f"Story visual style:\n{style_text}")
    return _short_text("\n\n".join(sections), 3200)


def audit_safe_ambient_image_result(result: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in (result or {}).items():
        if key in {"image_bytes", "b64_json"}:
            safe[f"{key}_omitted"] = True
        elif key == "prompt":
            safe["prompt_chars"] = len(str(value or ""))
        elif key in {"reason", "url", "api_key_env"}:
            safe[key] = redact_ambient_image_text(value, limit=240)
        else:
            safe[key] = _json_safe(value)
    return safe


def _audit_input(input_payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = {
        key: value
        for key, value in input_payload.items()
        if key not in {"self", "session", "latest_session", "provider_result", "image_bytes"}
    }
    return _json_safe(cleaned)


def locals_without_self(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "self"}


def _provider_unavailable(provider: Any) -> dict[str, Any]:
    checker = getattr(provider, "_unavailable", None)
    if not callable(checker):
        return {}
    result = checker()
    return dict(result) if isinstance(result, dict) and result else {}


def render_ambient_image_prompt_template(template: str, values: dict[str, Any]) -> str:
    source = str(template or "").strip() or DEFAULT_AMBIENT_IMAGE_PROMPT_TEMPLATE
    pattern = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            return match.group(0)
        return str(values.get(key, "") or "")

    return pattern.sub(replace, source)


def _prompt_json(value: Any, limit: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2)
    except TypeError:
        text = str(value or "")
    return _short_text(text, limit)


def _ambient_turn_index(session: Any) -> int:
    scene = getattr(session, "scene", {}) or {}
    recent = scene.get("_recent_narrative_events")
    if isinstance(recent, list):
        return len(recent)
    trace = scene.get("narrative_trace")
    if isinstance(trace, list):
        return len(trace)
    history = scene.get("history")
    if isinstance(history, list):
        return len(history)
    return _safe_int(scene.get("turn_index"), 0)


def _frequency_thresholds(frequency: str) -> tuple[int, int]:
    normalized = _normalize_frequency(frequency)
    if normalized == "high":
        return 5, 5
    if normalized == "low":
        return 30, 40
    return 10, 20


def _ambient_warmup_state(state: dict[str, Any]) -> dict[str, Any]:
    interaction_count = _safe_int(state.get("interaction_count"), 0)
    warmup_started_at = str(state.get("warmup_started_at", "") or "")
    if not warmup_started_at:
        return {
            "ready": False,
            "interaction_count": interaction_count,
            "warmup_started": False,
            "warmup_minutes_elapsed": 0,
        }
    elapsed = _minutes_since(warmup_started_at)
    return {
        "ready": elapsed >= 5,
        "interaction_count": interaction_count,
        "warmup_started": True,
        "warmup_minutes_elapsed": elapsed,
    }


def _ambient_activity_state(session: Any, config: AmbientImageConfig) -> dict[str, Any]:
    window_minutes = max(0, _safe_int(config.activity_window_minutes, 60))
    min_messages = max(0, _safe_int(config.activity_min_messages, 10))
    min_players = max(0, _safe_int(config.activity_min_players, 2))
    if window_minutes <= 0 or (min_messages <= 0 and min_players <= 1):
        return {"ready": True}

    recent_messages = _recent_player_messages(session, window_minutes=window_minutes)
    message_count = len(recent_messages)
    player_ids = {
        str(item.get("player_id") or "").strip()
        for item in recent_messages
        if str(item.get("player_id") or "").strip()
    }
    player_count = len(player_ids)
    message_ready = min_messages <= 0 or message_count >= min_messages
    player_ready = min_players <= 1 or player_count >= min_players
    return {
        "ready": message_ready and player_ready,
        "activity_window_minutes": window_minutes,
        "activity_message_count": message_count,
        "activity_min_messages": min_messages,
        "activity_player_count": player_count,
        "activity_min_players": min_players,
    }


def _ambient_generation_in_progress_state(state: dict[str, Any]) -> dict[str, Any]:
    started_at = str(state.get("generation_started_at", "") or "")
    if not started_at:
        return {"ready": True}
    elapsed = _minutes_since(started_at)
    return {
        "ready": elapsed >= AMBIENT_IMAGE_GENERATION_IN_PROGRESS_MINUTES,
        "generation_minutes_elapsed": elapsed,
        "generation_minutes_required": AMBIENT_IMAGE_GENERATION_IN_PROGRESS_MINUTES,
        "generation_started_at": started_at,
    }


def _normalize_frequency(value: str) -> str:
    normalized = str(value or "medium").strip().lower()
    if normalized in {"low", "medium", "high"}:
        return normalized
    if normalized in {"低频", "低"}:
        return "low"
    if normalized in {"高频", "高"}:
        return "high"
    return "medium"


def _combat_active(session: Any) -> bool:
    battle = getattr(session, "battle", {}) or {}
    turn = battle.get("turn") if isinstance(battle.get("turn"), dict) else {}
    return bool(battle.get("active") or turn.get("active") or getattr(session, "mode", None) == GameMode.TACTICAL)


def _campaign_has_started(session: Any) -> bool:
    scene = getattr(session, "scene", {}) or {}
    summary = str(scene.get("summary", "") or "")
    return bool(getattr(session, "characters", {}) or scene.get("current_conflict") or summary.strip()) and "尚未开局" not in summary


def _story_opening_key(session: Any) -> str:
    scene = getattr(session, "scene", {}) or {}
    summary = str(scene.get("summary", "") or "").strip()
    if not summary or "尚未开局" in summary:
        return ""
    if scene.get("opening_completed") or scene.get("game_started") or _safe_int(scene.get("story_turn"), 0) <= 3:
        return _short_text(summary, 80) or "opening"
    recent = scene.get("_recent_narrative_events")
    if isinstance(recent, list) and 1 <= len(recent) <= 3:
        return _short_text(summary, 80) or "opening"
    return ""


def _story_ending_key(session: Any) -> str:
    scene = getattr(session, "scene", {}) or {}
    if scene.get("ending_completed") or scene.get("story_ended"):
        return _short_text(str(scene.get("summary", "") or "ending"), 80) or "ending"
    conflict = str(scene.get("current_conflict", "") or "").lower()
    summary = str(scene.get("summary", "") or "").lower()
    ending_terms = ("终章", "结尾", "落幕", "尾声", "故事结束", "本章结束", "战役结束", "ending", "finale")
    if any(term in conflict or term in summary for term in ending_terms):
        return _short_text(str(scene.get("summary", "") or "ending"), 80) or "ending"
    return ""


def _pause_resume_event(session: Any) -> dict[str, str]:
    scene = getattr(session, "scene", {}) or {}
    paused_at = str(scene.get("_dm_paused_at", "") or "").strip()
    resumed_at = str(scene.get("_dm_resumed_at", "") or "").strip()
    if (
        bool(scene.get("_dm_paused"))
        and paused_at
        and _minutes_since(paused_at) <= PAUSE_RESUME_TRIGGER_WINDOW_MINUTES
    ):
        return {"kind": "pause", "key": "pause:" + _short_text(paused_at, 80)}
    if resumed_at and _minutes_since(resumed_at) <= PAUSE_RESUME_TRIGGER_WINDOW_MINUTES:
        return {"kind": "resume", "key": "resume:" + _short_text(resumed_at, 80)}
    explicit = str(scene.get("pause_state", "") or scene.get("story_pause_state", "") or "").strip().lower()
    if explicit in {"paused", "pause", "暂停"}:
        return {"kind": "pause", "key": "pause:" + _short_text(str(scene.get("summary", "") or "paused"), 80)}
    if explicit in {"resumed", "resume", "恢复", "继续"}:
        return {"kind": "resume", "key": "resume:" + _short_text(str(scene.get("summary", "") or "resumed"), 80)}
    return {}


def _pause_resume_trigger_result(
    session: Any,
    state: dict[str, Any],
    frequency: str,
    *,
    strict: bool,
) -> dict[str, Any]:
    event = _pause_resume_event(session)
    pause_resume_key = event.get("key", "")
    kind = event.get("kind", "")
    if not pause_resume_key:
        return {}
    if kind not in {"pause", "resume"}:
        return {}
    last_key_field = f"last_{kind}_key"
    last_at_field = f"last_{kind}_generated_at"
    if state.get(last_key_field) == pause_resume_key:
        if not strict:
            return {}
        return {
            "ok": False,
            "available": False,
            "reason": "ambient_image_pause_resume_already_generated",
            "pause_resume_kind": kind,
            "pause_resume_key": pause_resume_key,
        }
    cooldown_elapsed = _minutes_since(str(state.get(last_at_field, "") or ""))
    if cooldown_elapsed >= 120:
        return {
            "ok": True,
            "trigger": "pause_resume",
            "pause_resume_kind": kind,
            "pause_resume_key": pause_resume_key,
            "frequency": frequency,
        }
    return {
        "ok": False,
        "available": False,
        "reason": "ambient_image_pause_resume_cooldown",
        "pause_resume_kind": kind,
        "pause_resume_key": pause_resume_key,
        "cooldown_minutes_elapsed": cooldown_elapsed,
        "cooldown_minutes_required": 120,
    }


def _looks_like_direct_ambient_image_request(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    direct_terms = (
        "生图",
        "出图",
        "画图",
        "配图",
        "生成图片",
        "生成一张图",
        "画一张图",
        "画张图",
        "发张图",
        "来张图",
        "氛围图",
        "generate image",
        "draw image",
        "make an image",
    )
    if any(term in normalized for term in direct_terms):
        return True
    action_terms = ("生成", "画", "绘制", "发", "来一张", "来个", "做一张")
    image_terms = ("图片", "插图", "image", "illustration", "picture")
    return any(term in normalized for term in action_terms) and any(term in normalized for term in image_terms)


def _looks_like_setup_only(text: str) -> bool:
    terms = ("角色卡", "创建角色", "绑定角色", "规则", "token", "备份", "重开", "恢复")
    return any(term in text for term in terms)


def _ambient_story_key(session: Any) -> str:
    scene = getattr(session, "scene", {}) or {}
    started_at = str(scene.get("_game_started_at", "") or "").strip()
    if started_at:
        return "started:" + _short_text(started_at, 120)
    opening_intro = str(scene.get("_opening_intro", "") or "").strip()
    if opening_intro:
        return "opening:" + _short_text(opening_intro, 120)
    title = str(getattr(session, "title", "") or "").strip()
    summary = str(scene.get("summary", "") or "").strip()
    if title or summary:
        return "story:" + _short_text(f"{title}|{summary}", 160)
    return "session:" + _short_text(str(getattr(session, "session_id", "") or ""), 120)


def _remember_ambient_style(session: Any, description: str) -> None:
    style = _short_text(description, 500)
    if not style:
        return
    scene = getattr(session, "scene", {}) or {}
    story_key = _ambient_story_key(session)
    existing = dict(scene.get("ambient_image_style") or {}) if isinstance(scene.get("ambient_image_style"), dict) else {}
    if existing.get("story_key") == story_key and existing.get("description"):
        return
    scene["ambient_image_style"] = {
        "story_key": story_key,
        "description": style,
        "updated_at": utc_now_iso(),
    }


def _ambient_style_seed(session: Any) -> str:
    existing_style = _ambient_existing_story_style(session)
    if existing_style:
        return existing_style
    scene = getattr(session, "scene", {}) or {}
    world_tags = getattr(session, "world_tags", {}) or {}
    seeds = [
        str(world_tags.get("genre", "") or ""),
        str(world_tags.get("tone", "") or ""),
        str(world_tags.get("visual_style", "") or ""),
        str(scene.get("style", "") or ""),
    ]
    joined = "；".join(item for item in seeds if item.strip())
    return joined or "统一的电影感奇幻 TRPG 氛围，横向 1.5k（1536x1024），克制构图，重视光影和场景叙事。"


def _ambient_existing_story_style(session: Any) -> str:
    scene = getattr(session, "scene", {}) or {}
    existing = dict(scene.get("ambient_image_style") or {}) if isinstance(scene.get("ambient_image_style"), dict) else {}
    if existing.get("story_key") == _ambient_story_key(session) and existing.get("description"):
        return str(existing["description"])[:500]
    return ""


def _remember_recent_player_message(
    scene: dict[str, Any],
    actor: dict[str, str],
    player_message: str,
    created_at: str,
) -> None:
    message = _short_text(player_message, 500)
    if not message:
        return
    history = scene.get("ambient_image_recent_player_messages")
    items = [dict(item) for item in history if isinstance(item, dict)] if isinstance(history, list) else []
    items.append(
        {
            "created_at": created_at,
            "player_id": _short_text(actor.get("player_id", ""), 120),
            "display_name": _short_text(actor.get("display_name", ""), 120),
            "message": message,
        }
    )
    scene["ambient_image_recent_player_messages"] = items[-RECENT_PLAYER_MESSAGES_LIMIT:]


def _recent_player_messages(
    session: Any,
    *,
    window_minutes: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    scene = getattr(session, "scene", {}) or {}
    history = scene.get("ambient_image_recent_player_messages")
    if not isinstance(history, list):
        return []
    items: list[dict[str, Any]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        message = str(item.get("message") or "").strip()
        if not message:
            continue
        if window_minutes is not None:
            created_at = str(item.get("created_at") or "")
            if _minutes_since(created_at) > max(0, int(window_minutes)):
                continue
        items.append(dict(item))
    if limit is not None:
        return items[-max(0, int(limit)):]
    return items


def _select_alternate_player_message(
    session: Any,
    *,
    initial_player_id: str,
    initial_message: str,
) -> dict[str, Any]:
    recent = _recent_player_messages(session, limit=RECENT_PLAYER_MESSAGES_LIMIT)
    initial_player = str(initial_player_id or "").strip()
    initial_text = str(initial_message or "").strip()
    candidates = [
        item
        for item in reversed(recent)
        if _is_suitable_ambient_source_message(str(item.get("message") or ""))
        and str(item.get("message") or "").strip() != initial_text
    ]
    for item in candidates:
        player_id = str(item.get("player_id") or "").strip()
        if player_id and player_id != initial_player:
            return item
    return candidates[0] if candidates else {}


def _is_suitable_ambient_source_message(message: str) -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    lowered = text.lower()
    low_signal = {
        "好",
        "好的",
        "可以",
        "同意",
        "继续",
        "嗯",
        "ok",
        "okay",
        "yes",
        "y",
        "go on",
        "continue",
    }
    if lowered in low_signal:
        return False
    if _looks_like_direct_ambient_image_request(text):
        return False
    return len(text) >= 4


def _recent_ambient_prompts(session: Any, *, limit: int) -> list[dict[str, Any]]:
    scene = getattr(session, "scene", {}) or {}
    history = scene.get("ambient_image_prompts")
    if not isinstance(history, list):
        return []
    items = [dict(item) for item in history if isinstance(item, dict)]
    return items[-limit:]


def _prompt_too_similar_result(similarity: dict[str, Any], *, retry_attempted: bool) -> dict[str, Any]:
    return {
        "ok": False,
        "available": False,
        "error": "ambient_image_prompt_too_similar",
        "similarity": similarity.get("score", 0.0),
        "similarity_threshold": similarity.get("similarity_threshold", 0.82),
        "similar_to_recent_count": similarity.get("similar_to_recent_count", 0),
        "matched_recent_index": similarity.get("matched_recent_index", -1),
        "reason": similarity.get("reason", ""),
        "retry_attempted": retry_attempted,
    }


def _minutes_since(iso_text: str) -> int:
    if not iso_text:
        return 10**9
    try:
        parsed = datetime.fromisoformat(iso_text.replace("Z", "+00:00"))
    except ValueError:
        return 10**9
    now = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, int((now - parsed).total_seconds() // 60))


def _extract_json_object(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return ""
    return stripped[start : end + 1]


def _short_text(text: object, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) > limit:
        return value[:limit] + "...[truncated]"
    return value


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp_int(value: object, minimum: int, maximum: int) -> int:
    return min(max(_safe_int(value, minimum), minimum), maximum)


def _clamp_float(value: object, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = minimum
    return min(max(parsed, minimum), maximum)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
