"""Combining several result lists into one.

`kb_search` takes more than one query string on purpose -- the agent sends the
user's question and the same terms in the other language, and each produces its
own ranking. Reciprocal rank fusion merges them without needing the scores to
be comparable, which matters because a trigram similarity from one query and a
vector distance from another (once embeddings exist) are not on the same scale.

RRF also needs no tuning: there is one constant, `k`, and the standard value
works. That is the reason to prefer it over a weighted score blend that would
have to be re-tuned every time a field weight changes.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

#: The standard damping constant from the original RRF paper. Larger values
#: flatten the contribution of top ranks; 60 is the widely used default and
#: there is no reason specific to this catalog to deviate.
DEFAULT_K = 60


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]],
    *,
    k: int = DEFAULT_K,
    weights: Sequence[float] | None = None,
) -> list[tuple[str, float]]:
    """Fuse ranked id lists into one list of (id, score), best first.

    `rankings` is one list per query, each already ordered best-first. Ties are
    broken by first appearance, so the output is deterministic for a given
    input -- without that, two runs of the same search could return the same
    entries in a different order and look broken.
    """
    if weights is not None and len(weights) != len(rankings):
        raise ValueError(f"got {len(weights)} weights for {len(rankings)} rankings")

    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    position = 0

    for index, ranking in enumerate(rankings):
        weight = 1.0 if weights is None else weights[index]
        for rank, entry_id in enumerate(ranking):
            scores[entry_id] = scores.get(entry_id, 0.0) + weight / (k + rank + 1)
            if entry_id not in first_seen:
                first_seen[entry_id] = position
                position += 1

    return sorted(scores.items(), key=lambda item: (-item[1], first_seen[item[0]]))


def fuse_ids(rankings: Iterable[Sequence[str]], *, k: int = DEFAULT_K, limit: int | None = None) -> list[str]:
    """`reciprocal_rank_fusion` when only the order matters."""
    fused = [entry_id for entry_id, _ in reciprocal_rank_fusion(list(rankings), k=k)]
    return fused if limit is None else fused[:limit]


def rank_of(fused: Sequence[tuple[str, float]]) -> Mapping[str, int]:
    """Position of each id in a fused result, for logging and tests."""
    return {entry_id: rank for rank, (entry_id, _) in enumerate(fused)}
