"""Single-purpose tools used by the agents.

Each module here does one job well — parse a request, judge semantic
compatibility, query the web for candidates, explain a verdict, draft copy. They
hold no phase-level decision logic; that belongs to the agents in the package
above, which decide *when* and *how often* to use them.
"""
