# Character Control Authority And Hosting

This document describes how the plugin separates long-term character ownership
from temporary control, system hosting, and reclaim behavior.

## Goal

Temporary delegation should keep a multiplayer table moving without silently
taking agency away from a player.

Control authority must be explicit, auditable, revocable, bounded by duration
and risk ceiling, and separate from speaker, current tool actor, turn actor, and
map focus.

The system never infers hosting or delegation from silence, offline status,
vague departure wording, or another player's narration.

## Core Concepts

| Concept | Meaning | Not The Same As |
| --- | --- | --- |
| `owner` | Long-term character owner, stored through character/player binding. | Current speaker or temporary controller. |
| `active_controller` | Current authority allowed to act for the character. | Permanent owner. |
| `speaker` | Message source. | Automatic controller. |
| `actor` | Normalized tool-call identity. | Automatic owner/controller. |
| `turn_actor` | Current combat entity or turn anchor. | Player ownership or delegation authority. |

Old saves without a `control_authority` record remain owner-controlled through
the existing character owner fields.

## Supported Control Changes

The `control_authority` tool supports these actions:

| Action | Effect |
| --- | --- |
| `delegate_to_player` | Owner temporarily grants another player control. |
| `relinquish_to_system` | Owner temporarily grants system-host control. |
| `reclaim` | Owner returns future control to themselves. |
| `status` | Read-only safe control summary. |

Only the owner can perform mutating actions. A delegate cannot redelegate in v1.
True permanent ownership transfer is out of scope; long handoffs are modeled as
temporary delegation, usually `until_revoked`.

## Confirmation And Duration

The model may understand a player's intent, but it must ask for explicit
confirmation before calling a mutating control tool. A safe confirmation should
name the character, target controller, duration, risk ceiling, and whether the
player is authorizing system hosting or player delegation. When the owner gives
an explicit hosting strategy, such as "follow Kade and attack whatever Kade is
fighting", store a short `standing_order` summary with the control record.
Strict turn order still applies: the standing order is executed only when that
character becomes the current turn actor.

If no duration is provided, the default is `until_revoked`.

Supported duration types are:

- `until_next_turn`;
- `until_combat_end`;
- `until_scene_end`;
- `until_time`;
- `until_revoked`.

The current implementation records duration metadata. Explicit reclaim/revoke is
available now; lifecycle-driven expiration can consume the same fields later.

## Hosted Action Policy

System-hosted action is only active when the character has an explicit
`hosted_by_system` / `system_host` control record.

Risk ceilings are `low`, `medium`, and `high`; default is `low`.

- `low`: defend, take cover, follow the group, observe, avoid obvious danger.
- `medium`: ordinary attacks, normal skill checks, recoverable resource use.
- `high`: permanent harm, scarce or irreversible resource use, PvP, betrayal,
  secrets, contracts, surrendering key items, or other major commitments.

High-risk hosted actions require explicit pre-authorization. If the hosted
request exceeds the ceiling, code downgrades or denies it before the action is
recorded.

## Reclaim Is Forward-Only

Owner reclaim restores future control to the owner. It does not delete, rewrite,
or roll back actions, turn logs, map effects, or facts that were already
resolved before the reclaim.

If an action has not yet been committed to tools/state, reclaim can prevent
later processing. If an action has already been resolved, any correction must be
handled through an explicit future tool or table ruling, not silent history
mutation.

## Data And Projection Boundaries

Control changes are stored in `GameSession.control_authority`:

- `records[character_id]` stores the current owner/controller/status/risk and
  duration fields, plus an optional bounded `standing_order`.
- `events` stores bounded forward-only audit events.

Ordinary DM/player prompt snapshots may see safe projected control status, such
as owner id, active controller id, controller type, status, risk ceiling, and
duration summary.

They must not receive raw consent text, private hosting instructions, raw audit
payloads, local paths, or diagnostic-only fields. RA authority views may receive
structured audit refs when needed, but still not raw consent text.

Map and turn tools must resolve character authority through the code-owned
control resolver. Active map focus, battle focus, stale map entity tags, speaker
identity, and turn anchor do not grant control by themselves.

## Validation

Relevant regression coverage includes:

- `tests/test_control_authority.py`;
- `tests/test_control_transfer.py`;
- `tests/test_control_tools.py`;
- `tests/test_hosted_action_policy.py`;
- `tests/test_turn_tools.py`;
- `tests/test_spatial_tools.py`;
- `tests/test_tool_registry.py`;
- `tests/test_prompts.py`.
