# Per-run verification settings (DECISIONS.md section 5). Each benchmark task passes its own.
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OrderRule:
    rule_id: str        # short name used in reports, e.g. "approve_before_send"
    dsl: str            # AgentProof temporal DSL, e.g. "tool:deploy -> F tool:approve"


@dataclass(frozen=True)
class VerifyConfig:
    require_human: bool = False                            # the workflow must contain a human step
    sensitive_tools: frozenset[str] = field(default_factory=frozenset)  # tool names that need a human step before them
    order_rules: tuple[OrderRule, ...] = ()
    inputs: frozenset[str] = field(default_factory=frozenset)  # values available at the start (e.g. "email")
    max_states: int = 10_000                               # soundness search bound (DECISIONS.md section 4, suggestion 2)
    timeout_s: float = 2.0                                 # soundness search time bound, in seconds
