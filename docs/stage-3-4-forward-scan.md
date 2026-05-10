# Stage 3/4 Forward Scan

This report closes the stage-4 control-authority work by auditing it against
the completed stage-3 map/state/rendering work. It is an acceptance report, not
a new feature design.

## Goal

Stage 4 allows explicit player delegation and explicit system hosting for a
character. That permission must remain:

- explicit;
- auditable;
- revocable;
- bounded by duration and risk;
- separate from silence, offline status, speaker identity, map focus, and
  another player's narration.

The scan checks whether those rules still hold across MapCore, strict-grid
movement, turn tools, deterministic rendering, delivery routing, projection
views, and legacy compatibility fields.

## Audit Inputs

The audit includes:

- stage-3 MapCore work from 03.1.01 onward;
- role projection and consumer matrix work from 03.1.06 and 03.1.06.01;
- deterministic strict-grid and overview-topology renderer work from 03.1.07;
- delivery cadence and legacy SVG migration work from 03.1.07.04;
- stage-3 final cleanup and map final-sweep evidence from 03.1.08 and
  03.1.08.01;
- visual map request hard routing and text-map guard from PR #24;
- stage-4 control authority, hosted action policy, transfer, relinquish, and
  reclaim behavior from 04.1 through 04.3.

No landed routed-input preprocessing fix corresponding to a future 03.9 stage
was found during this scan. If such a change lands later, it should be audited
as an input-routing surface, not as a blocker for the current stage-4
completion.

## Acceptance Summary

The scan found no stage-3/4 blocker for the current implementation.

Current behavior preserves the intended identity split:

| Concept | Acceptance result |
| --- | --- |
| `owner` | Long-term character owner remains the default controller for old saves and unhosted records. |
| `active_controller` | Current action authority is stored in `control_authority` and checked by turn/spatial tools. |
| `speaker` | Message source does not grant control by itself. |
| `actor` | Tool-call identity must match the active controller for controlled mutations. |
| `turn_actor` | Current combat entity anchors turn context, not player authority. |
| map focus / stale mirror | Active map focus and legacy `battle.grid` mirrors do not grant ownership or controller rights. |
| no-character participant | A participant without the active character cannot gain map-affecting authority from focus state. |

Owner reclaim remains forward-only. It changes future authorization and does
not rewrite previous tool results, map state, turn logs, or audit events.

## Stage-3 Impact Matrix

| Stage surface | Relevant state or tool boundary | Stage-4 authority expectation | Audit result |
| --- | --- | --- | --- |
| 03.1.01 MapCore store and projections | `GameSession.maps`, role views, render refs, hidden facts | Control authority must remain owner/control-owned state outside MapCore. | Accepted. `control_authority` is adjacent state, not a map record. Map tags may help compatibility lookup but do not grant control. |
| 03.1.02 legacy battle-grid adapter | `battle.grid` as migration source and mirror | A stale mirror must not override MapStore authority or grant controller rights. | Accepted. Forward-scan tests include stale `battle.grid` ownership data while MapStore remains authoritative. |
| 03.1.03 `create_grid` MapStore write path | Strict-grid writes and compatibility mirror writes | New grid writes may mirror old state, but authority checks must use code-owned character control. | Accepted. Spatial tools resolve active controller before map-affecting character movement. |
| 03.1.04 MapCalculator routing | Movement, distance, line of sight, range, blockers | Delegation/hosting must not bypass map legality. | Accepted. Hosted and delegated movement still goes through strict-grid movement validation. |
| 03.1.05 strict map lifecycle | Active strict map versus combat link | Combat or map lifecycle state must not imply player authority. | Accepted. The current turn entity does not replace owner/controller checks. |
| 03.1.06 state ownership boundary | map-owned, battle-owned, character-owned, owner/control-owned fields | Owner/controller state must be projected separately and safely. | Accepted. Prompt and RA consumers receive projected status, not raw consent or private audit payloads. |
| 03.1.06.01 projection consumer matrix | DM, RA, renderer, diagnostics, tools, legacy fields | Each consumer should see only the control/map fields needed for its role. | Accepted. Existing docs and tests cover role projection; this report adds the cross-stage acceptance record. |
| 03.1.07 strict/overview deterministic renderers | `player_view` render envelopes and visual-only render refs | Rendering must not become authority selection or state mutation. | Accepted. Renderer request behavior stays actor-neutral and visual-only. |
| 03.1.07.04 delivery cadence / legacy SVG migration | renderer tool exposure, pending outputs, `generate_map_svg` fallback | Normal visual map requests must not bypass deterministic renderer attempts through text maps. | Accepted. PR #24 guard is included in the scan and remains independent of actor/controller identity. |
| 03.1.08 cleanup and map final sweep | safe battle snapshots, MapStore-first readers, compatibility mirrors | Legacy fields must remain compatibility-only and must not reintroduce ownership ambiguity. | Accepted with residual compatibility debt documented below. |

## Stage-4 Impact Matrix

| Stage surface | Implemented role | Cross-boundary check |
| --- | --- | --- |
| 04.1 control authority model | Adds owner/active-controller resolution, projections, and turn/spatial guard integration. | No-character actors, speakers, turn actors, stale map tags, and map focus do not become controllers. |
| 04.2 hosted action policy | Allows explicit system hosting only from `hosted_by_system` / `system_host` records. | Silence, absence, vague departure wording, and other-player narration do not create hosting. Hosted action risk is checked before turn records are written. |
| 04.3 transfer/relinquish/reclaim | Adds explicit delegation, system relinquish, owner reclaim, and bounded audit events. | Delegates cannot redelegate in v1. Owner reclaim denies old delegates for future actions without rewriting history. |
| 04.4 forward scan | Adds executable cross-stage regression tests and this report. | Confirms stage-3 map/render/projection surfaces do not weaken stage-4 authorization. |

## Map-Affecting Authority Cases

The forward-scan regression tests cover the high-risk map/control crossings:

| Case | Expected behavior | Evidence |
| --- | --- | --- |
| No-character participant with stale mirror ownership | Deny movement with `character_control_denied`; keep MapStore state unchanged. | `tests/test_stage_3_4_forward_scan.py` |
| Owner action before delegation | Allow normal map-affecting movement. | `tests/test_stage_3_4_forward_scan.py` |
| Owner delegates to another player | New delegate can move the character; owner is denied while delegate is active. | `tests/test_stage_3_4_forward_scan.py` |
| Owner reclaims | Old delegate is denied for future movement; previous map effects remain. | `tests/test_stage_3_4_forward_scan.py` |
| Owner relinquishes to system host | Hosted policy recognizes explicit system hosting; invalid moves remain blocked by strict-grid rules. | `tests/test_stage_3_4_forward_scan.py` |
| Visual map request from any actor class | Renderer-attempt guard remains actor-neutral; explicit text-only override remains allowed. | `tests/test_stage_3_4_forward_scan.py`, `tests/test_map_request_guard.py` |

## Projection And Privacy Boundaries

The accepted projection boundary is:

- ordinary DM prompts may see projected owner/controller status, risk ceiling,
  status, and duration summary;
- RA authority views may see structured audit refs when needed for state
  tracking;
- player-facing map output consumes player-safe map views and visual-only render
  refs;
- diagnostics may inspect broader state only through diagnostic surfaces;
- raw consent text, private hosting instructions, raw strict grids, hidden map
  facts, raw SVG, local paths, provider URLs, and diagnostic-only audit payloads
  must not enter ordinary prompt or player-facing output.

Prompt wording can ask for confirmation and explain policy, but authorization,
projection, map legality, risk downgrade, persistence, and audit structure stay
code-owned.

## Map Request Guard Check

PR #24 introduced code-owned visual-map request hard routing and text-map guard
behavior. The 04.4 scan treats it as an audit input:

- normal visual-map requests require a deterministic renderer attempt when a
  renderer is exposed;
- explicit text-only map requests remain allowed and do not require renderer
  use;
- `generate_map_svg` remains explicit fallback, style, or migration behavior,
  not the normal map route;
- map request guarding is not a character-control grant and does not depend on
  owner, delegate, system host, or no-character actor identity.

This preserves the separation between "asking to see a map" and "being
authorized to mutate a character on that map."

## Residual Risks And Follow-Ups

These are not blockers for stage-4 completion, but they should remain visible
for later work:

| Residual | Current handling | Future requirement |
| --- | --- | --- |
| 03.9 routed-input preprocessing | No landed implementation was found in this scan. | If it lands, verify it routes explicit control intents to confirmation/tooling without inferring authority from wording alone. |
| Duration expiration scheduler | Duration metadata is recorded, and explicit reclaim is available. | Lifecycle-driven expiration can consume the existing duration fields later. |
| Compatibility `battle.grid` mirror writers | Mirrors remain for old callers and saved-session compatibility. | Keep them MapStore-after-write and compatibility-only; do not restore mirror authority. |
| Diagnostic/full snapshot escape hatches | Diagnostic surfaces can inspect broader state. | Keep them out of ordinary DM/player/RA context unless passed through projection. |
| Future meta-rule fields | `rule_scale` exists in renderer/calculator paths; broader `strictness`, source policy, canon policy, map policy, and dice policy fields are not fully landed as durable downstream contracts. | Future preparation/meta-rule work should add explicit structured fields and code readers. Prompt-only inference must not become the runtime contract. |
| High-risk hosted actions | Current v1 policy downgrades or denies above the recorded ceiling before writing action records. | Any future richer resource, PvP, secret, or irreversible-action policy should remain code-owned and auditable. |

## Validation Evidence

The acceptance checks for this stage were:

```powershell
python -m pytest -q tests/test_stage_3_4_forward_scan.py -p no:cacheprovider
python -m pytest -q tests/test_stage_3_4_forward_scan.py tests/test_map_request_guard.py tests/test_map_final_sweep.py tests/test_spatial_tools.py tests/test_turn_tools.py tests/test_control_tools.py tests/test_control_transfer.py tests/test_control_authority.py tests/test_hosted_action_policy.py -p no:cacheprovider
```

Results:

- `tests/test_stage_3_4_forward_scan.py`: 4 passed;
- adjacent stage-3/stage-4 target set: 59 passed;
- no-pycache AST parse over plugin and tests: 107 Python files parsed;
- `git diff --check`: passed.

The local Windows checkout can fail `compileall` when it attempts to write
`__pycache__` directories under restricted ACLs. For this acceptance pass, the
no-pycache AST parse was used as the syntax check.

## Completion Decision

Stage 4 is implementation-complete when this report and the forward-scan tests
are present with passing targeted validation. Remaining items are future
extension points, not blockers:

- automatic duration expiry;
- richer meta-rule fields;
- future routed-input preprocessing;
- additional hosted-risk templates beyond the current conservative v1 policy.

The accepted v1 contract is that explicit owner-confirmed delegation,
system-hosting, and reclaim work with current stage-3 map/rendering/projection
boundaries without granting authority through silence, speaker identity,
turn actor, map focus, or stale compatibility state.
