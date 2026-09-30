"""Infrastructure: concrete implementations of things the ports need.

Logging today. Configuration loading lives in its own top-level package rather
than here, because it constructs a domain type and a wrong import direction would
be a genuine cycle.
"""
