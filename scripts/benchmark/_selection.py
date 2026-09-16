"""
Training-set selection strategies shared by Exp1 / Exp2 / Exp3.

Two strategies:
    "random"  - uniform shuffle then take the first N indices
    "kmeans"  - K-Means clustering on X (k=N), pick the embedding closest to
                each centroid; pads with random fill-ins if fewer than N
                unique indices come back (degenerate clusters).

The K-Means path mirrors evolution.py:
    KMeans(n_clusters=N_TRAIN, random_state=seed, n_init=10)
    pairwise_distances_argmin_min(centers, X) -> closest training point per cluster
This keeps the benchmark faithful to the production active-learning loop and
matches the diversity-aware sampling justified in
docs/2026-04-24-meta-surrogate-bias-analysis-ko.md.
"""

from __future__ import annotations

import warnings

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import pairwise_distances_argmin_min


def select_train_indices(
    X: np.ndarray,
    n_train: int,
    seed: int,
    strategy: str,
) -> np.ndarray:
    n_avail = X.shape[0]
    if n_train >= n_avail:
        return np.arange(n_avail)

    rng = np.random.default_rng(seed)
    perm = rng.permutation(n_avail)

    if strategy == "random":
        return perm[:n_train]

    if strategy == "kmeans":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            km = KMeans(n_clusters=n_train, random_state=seed, n_init=10)
            km.fit(X)
            closest_idx, _ = pairwise_distances_argmin_min(km.cluster_centers_, X)
        unique = np.unique(closest_idx)
        if unique.size >= n_train:
            return unique[:n_train]
        remaining = np.setdiff1d(np.arange(n_avail), unique)
        rng.shuffle(remaining)
        pad = remaining[: n_train - unique.size]
        return np.concatenate([unique, pad])

    raise ValueError(f"unknown selection strategy: {strategy}")
