from __future__ import annotations

from .models import CycleState, GameSession


class CycleStateMachine:
    _ALLOWED_TRANSITIONS = {
        CycleState.CYCLE_ACTIVE: {CycleState.CYCLE_RESOLVING},
        CycleState.CYCLE_RESOLVING: {CycleState.CYCLE_TRANSITION, CycleState.CYCLE_ACTIVE},
        CycleState.CYCLE_TRANSITION: {CycleState.CYCLE_ACTIVE},
    }

    def can_transition(self, session: GameSession, target: CycleState) -> bool:
        current = session.cycle_state
        if current == target:
            return True
        return target in self._ALLOWED_TRANSITIONS.get(current, set())

    def transition(self, session: GameSession, target: CycleState) -> CycleState:
        if not self.can_transition(session, target):
            raise ValueError(f"invalid_cycle_transition:{session.cycle_state.value}->{target.value}")
        session.cycle_state = target
        return session.cycle_state

    def begin_resolving(self, session: GameSession) -> CycleState:
        return self.transition(session, CycleState.CYCLE_RESOLVING)

    def begin_transition(self, session: GameSession) -> CycleState:
        return self.transition(session, CycleState.CYCLE_TRANSITION)

    def activate(self, session: GameSession) -> CycleState:
        return self.transition(session, CycleState.CYCLE_ACTIVE)
