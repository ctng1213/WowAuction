"""Tests for ahsync/engine/cost_basis.py: what you paid, account-wide (SPEC §3.2 cost_basis, T9-T13)."""
from __future__ import annotations

from ahsync.engine.cost_basis import Basis, Bought, Disenchanted, Made, cost_basis

T = 1_760_000_000_000
LINEN, BAR, ROD = 2589, 2840, 6217


def test_T9_found_units_count_at_0_next_to_bought_ones():
    basis = cost_basis([Bought("b", BAR, 10, 1000, T)], [], {BAR: 20})
    assert basis == {BAR: Basis(20, 10_000.0, 10)} and basis[BAR].paid_each == 500.0      # 100s ÷ 20 = 5s


def test_T10_a_craft_costs_what_its_mats_cost_at_their_average():
    made = Made("k", ROD, 1, ((BAR, 2),), T + 1)
    basis = cost_basis([Bought("b", BAR, 10, 1000, T)], [made], {BAR: 8, ROD: 1})
    assert (basis[ROD].total_cost, basis[BAR].qty, basis[BAR].total_cost) == (2000.0, 8, 8000.0)
    # Mats beyond what was bought cost 0: a rod from 2 found bars costs nothing.
    assert cost_basis([], [made], {ROD: 1}) == {ROD: Basis(1, 0.0, 0)}


def test_T11_looted_items_posted_get_no_basis():
    assert cost_basis([], [], {LINEN: 5}) == {}                  # shown as cost 0


def test_T12_an_item_held_before_tracking_gets_no_basis():
    assert cost_basis([Bought("b", BAR, 1, 10, T)], [], {LINEN: 3, BAR: 1}) == {BAR: Basis(1, 10.0, 0)}   # Linen: "before tracking"


def test_T13_mailing_between_your_characters_changes_nothing():
    bought = [Bought("b", LINEN, 10, 40, T)]
    assert cost_basis(bought, [], {LINEN: 10}) == cost_basis(bought, [], {LINEN: 10})   # held is account-wide


def test_units_that_left_take_their_average_cost_and_nothing_held_means_no_basis():
    bought = [Bought("a", LINEN, 10, 40, T), Bought("b", LINEN, 10, 60, T + 1)]
    assert cost_basis(bought, [], {LINEN: 5}) == {LINEN: Basis(5, 250.0, 0)}           # 5 × the 50c average
    assert cost_basis(bought, [], {}) == {} and Basis(0, 0.0, 0).paid_each == 0.0


BRACERS, ESSENCE, DUST = 4570, 10938, 10940


def test_T18_a_disenchant_passes_the_items_cost_to_its_results_by_units():
    de = Disenchanted("de", BRACERS, ((ESSENCE, 1), (DUST, 3)), T + 1)
    basis = cost_basis([Bought("b", BRACERS, 1, 3000, T)], [], {ESSENCE: 1, DUST: 3}, [de])
    # 30s over 4 units = 7s 50c each; the Bracers are gone
    assert basis == {ESSENCE: Basis(1, 750.0, 0), DUST: Basis(3, 2250.0, 0)}


def test_T18_an_untracked_item_disenchanted_passes_on_0():
    basis = cost_basis([], [], {DUST: 3}, [Disenchanted("de", BRACERS, ((DUST, 3),), T)])
    assert basis == {DUST: Basis(3, 0.0, 0)}
