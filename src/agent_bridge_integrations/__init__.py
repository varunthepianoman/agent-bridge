"""Optional policy-system integrations layered above Bridge core."""

from .aiwk import AIWKExecutorAdapter, AIWKReference, AIWKRoleInvocation

__all__ = ["AIWKExecutorAdapter", "AIWKReference", "AIWKRoleInvocation"]
