from __future__ import annotations

import pytest

from kb.domain.ranking import fuse_ids, rank_of, reciprocal_rank_fusion


def test_a_single_ranking_comes_back_in_its_own_order():
    # Covers: FR-SRCH-01
    assert fuse_ids([["a", "b", "c"]]) == ["a", "b", "c"]


def test_an_entry_found_by_both_queries_outranks_one_found_by_either():
    # Covers: FR-SRCH-01
    # The English and Persian phrasings of one question both hit "b"; "a" is
    # top of one list only. Agreement across queries is the signal RRF exists
    # to reward.
    fused = fuse_ids([["a", "b"], ["c", "b"]])
    assert fused[0] == "b"


def test_ties_break_by_first_appearance_so_results_are_deterministic():
    # Covers: FR-SRCH-02
    # "a" and "c" score identically; the order must not depend on dict
    # iteration or two identical searches would look inconsistent.
    assert fuse_ids([["a"], ["c"]]) == ["a", "c"]
    assert fuse_ids([["c"], ["a"]]) == ["c", "a"]


def test_weights_let_one_query_count_for_more():
    # Covers: FR-SRCH-01
    unweighted = fuse_ids([["a"], ["b"]])
    assert unweighted[0] == "a"
    weighted = reciprocal_rank_fusion([["a"], ["b"]], weights=[1.0, 5.0])
    assert weighted[0][0] == "b"


def test_mismatched_weights_are_a_programming_error():
    # Covers: FR-SRCH-01
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([["a"], ["b"]], weights=[1.0])


def test_limit_trims_the_fused_list():
    # Covers: FR-SRCH-03
    assert fuse_ids([["a", "b", "c"]], limit=2) == ["a", "b"]


def test_empty_input_is_an_empty_result():
    # Covers: FR-SRCH-02
    assert fuse_ids([]) == []
    assert fuse_ids([[], []]) == []


def test_rank_of_reports_positions():
    # Covers: FR-SRCH-02
    assert rank_of(reciprocal_rank_fusion([["a", "b"]])) == {"a": 0, "b": 1}
