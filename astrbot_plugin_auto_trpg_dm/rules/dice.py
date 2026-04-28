from __future__ import annotations

import random
import re
from dataclasses import asdict, dataclass


@dataclass
class RollRecord:
    expression: str
    rolls: list[int]
    modifier: int
    total: int


class DiceRoller:
    DICE_RE = re.compile(r"^\s*(?P<count>\d*)d(?P<sides>\d+)\s*(?P<mod>[+-]\s*\d+)?\s*$", re.I)

    def __init__(self):
        self.records: list[RollRecord] = []

    def roll(self, expression: str | int, count: int = 1, modifier: int = 0) -> int:
        if isinstance(expression, int):
            sides = expression
            count = int(count)
            modifier = int(modifier)
            expression_text = self._format_expression(count, sides, modifier)
        elif isinstance(expression, str):
            match = self.DICE_RE.match(expression)
            if not match:
                raise ValueError(f"unsupported dice expression: {expression}")
            count = int(match.group("count") or "1")
            sides = int(match.group("sides"))
            modifier_text = match.group("mod")
            modifier = int(modifier_text.replace(" ", "")) if modifier_text else 0
            expression_text = expression.strip()
        else:
            raise ValueError(f"unsupported dice expression type: {type(expression).__name__}")
        return self._roll_dice(expression_text, count, sides, modifier)

    def randint(self, low: int, high: int) -> int:
        low = int(low)
        high = int(high)
        if high < low:
            raise ValueError("randint high must be greater than or equal to low")
        span = high - low + 1
        if span < 2 or span > 1000:
            raise ValueError("randint range size must be between 2 and 1000")
        value = random.randint(low, high)
        self.records.append(RollRecord(f"randint({low},{high})", [value], 0, value))
        return value

    def _roll_dice(self, expression: str, count: int, sides: int, modifier: int) -> int:
        if count < 1 or count > 100:
            raise ValueError("dice count must be between 1 and 100")
        if sides < 2 or sides > 1000:
            raise ValueError("dice sides must be between 2 and 1000")
        rolls = [random.randint(1, sides) for _ in range(count)]
        total = sum(rolls) + modifier
        self.records.append(RollRecord(expression, rolls, modifier, total))
        return total

    @staticmethod
    def _format_expression(count: int, sides: int, modifier: int) -> str:
        expression = f"{count}d{sides}"
        if modifier > 0:
            return f"{expression}+{modifier}"
        if modifier < 0:
            return f"{expression}{modifier}"
        return expression

    def dump(self) -> list[dict]:
        return [asdict(record) for record in self.records]
