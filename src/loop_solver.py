"""
loop_solver.py
==============
Exact (Held–Karp style bitmask DP) solvers for the two delivery-loop
problems used by the routing study:

1. `solve_producer_loop`  — a producer must visit a FIXED, REQUIRED set of
   stops (every eligible consumer for one Produkt) and return home.
   This is the classic Traveling-Salesman-Path problem: minimise the total
   time of a Hamiltonian path that starts and ends at the producer and
   visits every required stop exactly once along the way.

2. `solve_consumer_loop`  — a consumer needs a set of Products. Candidate
   producers are POIs that supply at least one needed Product. The loop
   does NOT need to visit every candidate — it needs to visit *enough*
   candidates so the union of Products available at the visited stops
   covers every needed Product, while minimising total time. This is a
   "set-cover + ordering" problem solved jointly via DP over
   (visited_mask, covered_mask, current_node).

Both solvers are exact (no greedy/NN shortcuts), operate purely on a
precomputed pairwise time matrix (already routed beforehand via igraph in
connections.py), and are intentionally dependency-free (only numpy) so they
can be unit-tested without the real street graph.

Complexity:
  - producer loop:  O(2^n * n^2)   — n = number of required stops
  - consumer loop:  O(2^n * n^2 * 2^p) collapsed to O(2^n * n^2) by tracking
    only the covered-products BITMASK fused into the visited-state DP — see
    `solve_consumer_loop` docstring for the exact state definition.

n is expected to be small in practice (a handful of stops per
producer/consumer within the delivery radius), so exact DP is tractable.
For safety, a hard cap (`max_stops`) raises a clear error rather than
silently taking minutes — callers should pre-filter further upstream if
that ever fires.
"""

from __future__ import annotations

from itertools import combinations
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

import numpy as np

INF = float("inf")


class LoopSolverError(ValueError):
    pass


def _check_size(n: int, max_stops: int, label: str) -> None:
    if n > max_stops:
        raise LoopSolverError(
            f"{label}: {n} candidate stops exceeds max_stops={max_stops}. "
            "Exact DP is exponential in stop count; pre-filter the candidate "
            "set (e.g. tighten max_delivery_distance_km) before calling the solver."
        )


# ──────────────────────────────────────────────────────────────────────────
# PRODUCER LOOP — must visit every required stop exactly once
# ──────────────────────────────────────────────────────────────────────────

def solve_producer_loop(
    time_matrix: np.ndarray,
    max_stops: int = 15,
) -> Tuple[List[int], float]:
    """Exact shortest loop visiting every stop.

    Parameters
    ----------
    time_matrix : (n+1, n+1) ndarray
        time_matrix[0] is the producer (home / start+end node).
        time_matrix[1..n] are the required consumer stops, in any order.
        time_matrix[i][j] = travel time from stop i to stop j. Use np.inf
        for unreachable pairs.
    max_stops : int
        Safety cap on n (excludes the producer itself).

    Returns
    -------
    order : list[int]
        Visiting order as indices into time_matrix, starting and ending
        with 0 (the producer), e.g. [0, 2, 1, 3, 0].
    total_time : float
        Sum of travel times along that order. np.inf if infeasible.
    """
    m = time_matrix.shape[0]
    n = m - 1  # required stops, excluding producer at index 0
    _check_size(n, max_stops, "solve_producer_loop")

    if n == 0:
        return [0, 0], 0.0
    if n == 1:
        t = time_matrix[0][1] + time_matrix[1][0]
        return [0, 1, 0], t

    # Held-Karp DP over subsets of {1..n}.
    # dp[mask][j] = min time to start at 0, visit exactly the stops in mask,
    # ending at stop j (j in mask).
    # mask bit i (0-indexed within stops, i.e. stop i+1) is set if stop i+1 visited.
    full_mask = (1 << n) - 1
    dp: Dict[Tuple[int, int], float] = {}
    parent: Dict[Tuple[int, int], int] = {}

    for j in range(1, n + 1):
        mask = 1 << (j - 1)
        dp[(mask, j)] = time_matrix[0][j]
        parent[(mask, j)] = 0

    for subset_size in range(2, n + 1):
        for stops in combinations(range(1, n + 1), subset_size):
            mask = 0
            for s in stops:
                mask |= 1 << (s - 1)
            for j in stops:
                prev_mask = mask & ~(1 << (j - 1))
                best_t = INF
                best_k = -1
                for k in stops:
                    if k == j:
                        continue
                    key = (prev_mask, k)
                    if key not in dp:
                        continue
                    cand = dp[key] + time_matrix[k][j]
                    if cand < best_t:
                        best_t = cand
                        best_k = k
                if best_k != -1:
                    dp[(mask, j)] = best_t
                    parent[(mask, j)] = best_k

    best_total = INF
    best_last = -1
    for j in range(1, n + 1):
        key = (full_mask, j)
        if key not in dp:
            continue
        cand = dp[key] + time_matrix[j][0]
        if cand < best_total:
            best_total = cand
            best_last = j

    if best_last == -1:
        return [], INF

    # Reconstruct path
    order_rev = [best_last]
    mask = full_mask
    cur = best_last
    while True:
        prev = parent[(mask, cur)]
        if prev == 0:
            break
        order_rev.append(prev)
        mask &= ~(1 << (cur - 1))
        cur = prev

    order = [0] + list(reversed(order_rev)) + [0]
    return order, best_total


# ──────────────────────────────────────────────────────────────────────────
# CONSUMER LOOP — visit enough candidates to cover all needed Products
# ──────────────────────────────────────────────────────────────────────────

def solve_consumer_loop(
    time_matrix: np.ndarray,
    stop_product_masks: Sequence[int],
    needed_mask: int,
    max_stops: int = 15,
) -> Tuple[List[int], float, int]:
    """Exact shortest loop from the consumer that covers all needed Products.

    The loop is not required to visit every candidate producer — only a
    subset whose combined Product coverage satisfies `needed_mask`. Ordering
    and stop-selection are solved jointly: among all (subset, order) pairs
    that achieve full coverage, the one with minimum total time wins.

    Parameters
    ----------
    time_matrix : (n+1, n+1) ndarray
        time_matrix[0] is the consumer (home / start+end node).
        time_matrix[1..n] are the candidate producer stops.
    stop_product_masks : sequence of int, length n
        stop_product_masks[i] (0-indexed, corresponds to stop i+1) is a
        bitmask of which needed Products that producer supplies (bits are
        only set for Products the consumer actually needs — irrelevant
        Products the producer also makes don't matter here).
    needed_mask : int
        Bitmask of all Products the consumer needs. The loop must visit a
        stop set whose OR of stop_product_masks equals needed_mask.
    max_stops : int
        Safety cap on n.

    Returns
    -------
    order : list[int]
        Visiting order starting/ending at 0, e.g. [0, 3, 1, 0].
    total_time : float
        Total travel time of that order. np.inf if no covering subset exists.
    covered_mask : int
        Bitmask actually covered (== needed_mask on success).
    """
    m = time_matrix.shape[0]
    n = m - 1
    _check_size(n, max_stops, "solve_consumer_loop")
    assert len(stop_product_masks) == n, "stop_product_masks length must equal number of candidate stops"

    if needed_mask == 0:
        return [0, 0], 0.0, 0

    # Quick feasibility check: union of all candidate masks must cover needed_mask.
    union_all = 0
    for pm in stop_product_masks:
        union_all |= pm
    if (union_all & needed_mask) != needed_mask:
        return [], INF, union_all & needed_mask

    if n == 0:
        return [], INF, 0

    # DP state: (visited_mask, current_stop) -> (best_time, covered_mask_at_best_time)
    def mask_coverage(visited_mask: int) -> int:
        cov = 0
        mm = visited_mask
        i = 0
        while mm:
            if mm & 1:
                cov |= stop_product_masks[i]
            mm >>= 1
            i += 1
        return cov

    dp: Dict[Tuple[int, int], float] = {}
    parent: Dict[Tuple[int, int], int] = {}

    for j in range(1, n + 1):
        mask = 1 << (j - 1)
        dp[(mask, j)] = time_matrix[0][j]
        parent[(mask, j)] = 0

    for subset_size in range(2, n + 1):
        for stops in combinations(range(1, n + 1), subset_size):
            mask = 0
            for s in stops:
                mask |= 1 << (s - 1)
            for j in stops:
                prev_mask = mask & ~(1 << (j - 1))
                best_t = INF
                best_k = -1
                for k in stops:
                    if k == j:
                        continue
                    key = (prev_mask, k)
                    if key not in dp:
                        continue
                    cand = dp[key] + time_matrix[k][j]
                    if cand < best_t:
                        best_t = cand
                        best_k = k
                if best_k != -1:
                    dp[(mask, j)] = best_t
                    parent[(mask, j)] = best_k

    best_total = INF
    best_mask = -1
    best_last = -1
    for (mask, j), t in dp.items():
        cov = mask_coverage(mask)
        if (cov & needed_mask) != needed_mask:
            continue
        cand = t + time_matrix[j][0]
        if cand < best_total:
            best_total = t + time_matrix[j][0]
            best_mask = mask
            best_last = j

    if best_last == -1:
        return [], INF, 0

    order_rev = [best_last]
    mask = best_mask
    cur = best_last
    while True:
        prev = parent[(mask, cur)]
        if prev == 0:
            break
        order_rev.append(prev)
        mask &= ~(1 << (cur - 1))
        cur = prev

    order = [0] + list(reversed(order_rev)) + [0]
    return order, best_total, mask_coverage(best_mask)
