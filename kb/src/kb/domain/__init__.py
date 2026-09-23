"""Pure rules: no I/O, no third-party imports, nothing that can fail slowly.

Everything here is a function of its arguments, so the behavior that decides
what an entry *is*, what matches a query, and what counts as stale can be
tested without a database, a network, or a model endpoint.
"""
