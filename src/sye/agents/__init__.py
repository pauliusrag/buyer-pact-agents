"""The agents.

One agent per phase of real autonomous work. Each owns its decisions, its tools and
its audit trail, and each can be run on its own:

| Agent | Owns | Decides |
| --- | --- | --- |
| :class:`IntentAgent` | ingestion | what a person actually meant; hard vs soft; what to assume |
| :class:`MarketResearchAgent` | grouping + research | who can share a product; what to search for; whether to search again; which products fit |
| :class:`SourcingAgent` | supply | who could plausibly fulfil this; what terms to request |
| :class:`NegotiationAgent` | commercial | what to counter with; when a deal is good enough |
| :class:`CampaignAgent` | publication | which offer wins; whether there is a deal worth publishing |

The LangGraph pipeline wires them together and owns the renegotiation cycle; it does
not contain their reasoning. Single-purpose helpers live in :mod:`sye.agents.tools`.
"""

from sye.agents.base import Agent, AgentContext, AgentError, AgentResult
from sye.agents.campaign_agent import CampaignAgent, CampaignResult
from sye.agents.intent_agent import IntentAgent, IntentAgentResult
from sye.agents.market_research_agent import MarketResearchAgent, MarketResearchResult
from sye.agents.negotiation_agent import NegotiationAgent, NegotiationResult
from sye.agents.sourcing_agent import SourcingAgent, SourcingResult

__all__ = [
    "Agent",
    "AgentContext",
    "AgentError",
    "AgentResult",
    "CampaignAgent",
    "CampaignResult",
    "IntentAgent",
    "IntentAgentResult",
    "MarketResearchAgent",
    "MarketResearchResult",
    "NegotiationAgent",
    "NegotiationResult",
    "SourcingAgent",
    "SourcingResult",
]
