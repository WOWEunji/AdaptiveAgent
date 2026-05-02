"""Role-specific agents for the AdaptiveAgent core."""

from adaptive_agent.agents.base import AgentResult, AgentRole, BaseRoleAgent
from adaptive_agent.agents.coder import CoderAgent
from adaptive_agent.agents.critic import CriticAgent
from adaptive_agent.agents.executor import ExecutorAgent
from adaptive_agent.agents.librarian import LibrarianAgent
from adaptive_agent.agents.plan import PlanAgent

__all__ = [
    "AgentResult",
    "AgentRole",
    "BaseRoleAgent",
    "CoderAgent",
    "CriticAgent",
    "ExecutorAgent",
    "LibrarianAgent",
    "PlanAgent",
]
