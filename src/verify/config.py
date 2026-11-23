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
