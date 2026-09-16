import os
import sys
import numpy as np
import pandas as pd
import importlib.util
import itertools
from sklearn.ensemble import RandomForestRegressor
from sklearn.cluster import KMeans
from sklearn.metrics import pairwise_distances_argmin_min

sys.path.append('/opt/protein_pipeline')
spec = importlib.util.spec_from_file_location("loto_module", "scripts/04_cath_loto_validation.py")
loto_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loto_module)

def seq_identity(seq1, seq2):
    matches = sum(c1 == c2 for c1, c2 in zip(seq1, seq2))
    return matches / max(len(seq1), len(seq2))

def avg_pairwise_identity(seqs):
    if len(seqs) < 2: return 1.0
    pairs = list(itertools.combinations(seqs, 2))
    return sum(seq_identity(s1, s2) for s1, s2 in pairs) / len(pairs)

def avg_cross_identity(seqs1, seqs2):
    if not seqs1 or not seqs2: return 0.0
    total = sum(seq_identity(s1, s2) for s1 in seqs1 for s2 in seqs2)
    return total / (len(seqs1) * len(seqs2))

def run_comparison(df, embeddings):
    targets = df['target_id'].unique()
    N_TRAIN = 30
    TOP_K = 10
    results = []

    for target in targets:
        mask = df['target_id'] == target
        df_t = df[mask].reset_index(drop=True)
        emb_t = embeddings[mask]

        valid_mask = ~df_t['plddt_score'].isna()
        df_t = df_t[valid_mask].reset_index(drop=True)
        emb_t = emb_t[valid_mask]

        if len(df_t) < 60: continue

        plddt_actual = df_t['plddt_score'].values
        seqs_all = df_t['sequence'].tolist()
        indices = np.arange(len(df_t))

        np.random.seed(42)
        shuffled = np.random.permutation(indices)
        train_idx_rnd = shuffled[:N_TRAIN]
        pool_idx_rnd = shuffled[N_TRAIN:]

        rf_rnd = RandomForestRegressor(n_estimators=100, random_state=42)
        rf_rnd.fit(emb_t[train_idx_rnd], plddt_actual[train_idx_rnd])
        pred_rnd = rf_rnd.predict(emb_t[pool_idx_rnd])

        surr_top_rnd = pool_idx_rnd[np.argsort(pred_rnd)[-TOP_K:]]
        seqs_surr_rnd = [seqs_all[i] for i in surr_top_rnd]
        best_train_rnd = [seqs_all[train_idx_rnd[np.argmax(plddt_actual[train_idx_rnd])]]]

        kmeans = KMeans(n_clusters=N_TRAIN, random_state=42, n_init=10)
        kmeans.fit(emb_t)
        train_idx_km, _ = pairwise_distances_argmin_min(kmeans.cluster_centers_, emb_t)
        pool_idx_km = np.setdiff1d(indices, train_idx_km)

        rf_km = RandomForestRegressor(n_estimators=100, random_state=42)
        rf_km.fit(emb_t[train_idx_km], plddt_actual[train_idx_km])
        pred_km = rf_km.predict(emb_t[pool_idx_km])

        surr_top_km = pool_idx_km[np.argsort(pred_km)[-TOP_K:]]
        seqs_surr_km = [seqs_all[i] for i in surr_top_km]
        best_train_km = [seqs_all[train_idx_km[np.argmax(plddt_actual[train_idx_km])]]]

        optimal_top = indices[np.argsort(plddt_actual)[-TOP_K:]]
        seqs_opt = [seqs_all[i] for i in optimal_top]

        results.append({
            'target': target,
            'rnd_id_to_best_train': avg_cross_identity(seqs_surr_rnd, best_train_rnd),
            'rnd_internal_sim': avg_pairwise_identity(seqs_surr_rnd),
            'rnd_plddt_mean': np.mean(plddt_actual[surr_top_rnd]),
            'km_id_to_best_train': avg_cross_identity(seqs_surr_km, best_train_km),
            'km_internal_sim': avg_pairwise_identity(seqs_surr_km),
            'km_plddt_mean': np.mean(plddt_actual[surr_top_km]),
            'opt_plddt_mean': np.mean(plddt_actual[optimal_top]),
            'opt_internal_sim': avg_pairwise_identity(seqs_opt)
        })

    res_df = pd.DataFrame(results)
    print("=== COMPARISON AGGREGATE ===")
    print(res_df.mean(numeric_only=True))

if __name__ == "__main__":
    df = loto_module.extract_cath_batch_data("cath_outputs")
    embeddings = loto_module.get_esm_embeddings(df)
    run_comparison(df, embeddings)