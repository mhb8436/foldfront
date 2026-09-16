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

def run_per_target_analysis(df, embeddings):
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
        
        has_soluprot = df_t['soluprot_score'].isna().sum() < len(df_t) // 2
        if has_soluprot:
            df_t['soluprot_score'] = df_t['soluprot_score'].fillna(df_t['soluprot_score'].mean())
            soluprot_actual = df_t['soluprot_score'].values
        else:
            soluprot_actual = np.zeros(len(df_t))

        if len(df_t) < 60: continue

        plddt_actual = df_t['plddt_score'].values
        seqs_all = df_t['sequence'].tolist()
        indices = np.arange(len(df_t))

        np.random.seed(123)
        pure_rand_idx = np.random.choice(indices, TOP_K, replace=False)
        rand_plddt = np.mean(plddt_actual[pure_rand_idx])
        rand_soluprot = np.mean(soluprot_actual[pure_rand_idx])

        np.random.seed(42)
        shuffled = np.random.permutation(indices)
        train_idx_rnd = shuffled[:N_TRAIN]
        pool_idx_rnd = shuffled[N_TRAIN:]

        rf_plddt_rnd = RandomForestRegressor(n_estimators=100, random_state=42)
        rf_plddt_rnd.fit(emb_t[train_idx_rnd], plddt_actual[train_idx_rnd])
        pred_plddt_rnd = rf_plddt_rnd.predict(emb_t[pool_idx_rnd])
        surr_top_rnd = pool_idx_rnd[np.argsort(pred_plddt_rnd)[-TOP_K:]]
        
        if has_soluprot:
            rf_solu_rnd = RandomForestRegressor(n_estimators=100, random_state=42)
            rf_solu_rnd.fit(emb_t[train_idx_rnd], soluprot_actual[train_idx_rnd])
            pred_solu_rnd = rf_solu_rnd.predict(emb_t[pool_idx_rnd])
            surr_top_rnd_solu = pool_idx_rnd[np.argsort(pred_solu_rnd)[-TOP_K:]]
            surr_soluprot = np.mean(soluprot_actual[surr_top_rnd_solu])
        else:
            surr_soluprot = 0.0

        seqs_surr_rnd = [seqs_all[i] for i in surr_top_rnd]
        best_train_rnd = [seqs_all[train_idx_rnd[np.argmax(plddt_actual[train_idx_rnd])]]]
        
        surr_plddt = np.mean(plddt_actual[surr_top_rnd])
        surr_id_best = avg_cross_identity(seqs_surr_rnd, best_train_rnd)
        surr_int_sim = avg_pairwise_identity(seqs_surr_rnd)

        kmeans = KMeans(n_clusters=N_TRAIN, random_state=42, n_init=10)
        kmeans.fit(emb_t)
        train_idx_km, _ = pairwise_distances_argmin_min(kmeans.cluster_centers_, emb_t)
        pool_idx_km = np.setdiff1d(indices, train_idx_km)

        rf_plddt_km = RandomForestRegressor(n_estimators=100, random_state=42)
        rf_plddt_km.fit(emb_t[train_idx_km], plddt_actual[train_idx_km])
        pred_plddt_km = rf_plddt_km.predict(emb_t[pool_idx_km])
        surr_top_km = pool_idx_km[np.argsort(pred_plddt_km)[-TOP_K:]]
        
        if has_soluprot:
            rf_solu_km = RandomForestRegressor(n_estimators=100, random_state=42)
            rf_solu_km.fit(emb_t[train_idx_km], soluprot_actual[train_idx_km])
            pred_solu_km = rf_solu_km.predict(emb_t[pool_idx_km])
            surr_top_km_solu = pool_idx_km[np.argsort(pred_solu_km)[-TOP_K:]]
            km_soluprot = np.mean(soluprot_actual[surr_top_km_solu])
        else:
            km_soluprot = 0.0

        seqs_surr_km = [seqs_all[i] for i in surr_top_km]
        best_train_km = [seqs_all[train_idx_km[np.argmax(plddt_actual[train_idx_km])]]]

        km_plddt = np.mean(plddt_actual[surr_top_km])
        km_id_best = avg_cross_identity(seqs_surr_km, best_train_km)
        km_int_sim = avg_pairwise_identity(seqs_surr_km)

        results.append({
            'Target': target.replace('cath_test_', ''),
            'Rand_pLDDT': f"{rand_plddt:.1f}",
            'Surr_pLDDT': f"{surr_plddt:.1f}",
            'KM_pLDDT': f"{km_plddt:.1f}",
            'Rand_Solu': f"{rand_soluprot:.3f}",
            'Surr_Solu': f"{surr_soluprot:.3f}",
            'KM_Solu': f"{km_soluprot:.3f}",
            'Surr_IntSim': f"{surr_int_sim:.3f}",
            'KM_IntSim': f"{km_int_sim:.3f}",
            'Surr_IdBest': f"{surr_id_best:.3f}",
            'KM_IdBest': f"{km_id_best:.3f}"
        })

    df_res = pd.DataFrame(results)
    
    print("| 타겟 ID | 무작위 샘플<br>(pLDDT / Solu) | 무작위 학습 모델<br>(pLDDT / Solu) | K-Means 모델<br>(pLDDT / Solu) | 모델의 과적합율<br>(학습 1등 서열과의 일치도)<br>Random / K-Means | 모델의 다양성 상실<br>(선택된 10개 간 유사도)<br>Random / K-Means |")
    print("| :--- | :---: | :---: | :---: | :---: | :---: |")
    for _, row in df_res.iterrows():
        print(f"| `{row['Target']}` | {row['Rand_pLDDT']} / {row['Rand_Solu']} | {row['Surr_pLDDT']} / {row['Surr_Solu']} | **{row['KM_pLDDT']}** / **{row['KM_Solu']}** | {row['Surr_IdBest']} / {row['KM_IdBest']} | {row['Surr_IntSim']} / {row['KM_IntSim']} |")

if __name__ == "__main__":
    df = loto_module.extract_cath_batch_data("cath_outputs")
    embeddings = loto_module.get_esm_embeddings(df)
    run_per_target_analysis(df, embeddings)