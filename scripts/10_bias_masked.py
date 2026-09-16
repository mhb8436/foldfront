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

def get_wt_sequence(target_id):
    fasta_path = f"cath_outputs/{target_id}/target.fasta"
    if not os.path.exists(fasta_path):
        return None
    with open(fasta_path, 'r') as f:
        lines = f.readlines()
        seq = "".join([l.strip() for l in lines if not l.startswith(">")])
        return seq

def seq_identity_masked(seq1, seq2, mutable_idx):
    if len(mutable_idx) == 0:
        return 1.0
    matches = sum(seq1[i] == seq2[i] for i in mutable_idx if i < len(seq1) and i < len(seq2))
    return matches / len(mutable_idx)

def avg_pairwise_identity_masked(seqs, mutable_idx):
    if len(seqs) < 2: return 1.0
    pairs = list(itertools.combinations(seqs, 2))
    return sum(seq_identity_masked(s1, s2, mutable_idx) for s1, s2 in pairs) / len(pairs)

def avg_cross_identity_masked(seqs1, seqs2, mutable_idx):
    if not seqs1 or not seqs2: return 0.0
    total = sum(seq_identity_masked(s1, s2, mutable_idx) for s1 in seqs1 for s2 in seqs2)
    return total / (len(seqs1) * len(seqs2))

def run_per_target_analysis_masked(df, embeddings):
    targets = df['target_id'].unique()
    N_TRAIN = 30
    TOP_K = 10
    
    results = []

    for target in targets:
        wt_seq = get_wt_sequence(target)
        if not wt_seq: continue
        
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
        
        mutable_idx = []
        seq_len = min(len(wt_seq), min(len(s) for s in seqs_all))
        for i in range(seq_len):
            if any(s[i] != wt_seq[i] for s in seqs_all):
                mutable_idx.append(i)
                
        mut_ratio = len(mutable_idx) / seq_len
        print(f"{target}: Sequence length {seq_len}, Mutable positions {len(mutable_idx)} ({mut_ratio*100:.1f}%)")

        np.random.seed(42)
        shuffled = np.random.permutation(indices)
        train_idx_rnd = shuffled[:N_TRAIN]
        pool_idx_rnd = shuffled[N_TRAIN:]

        rf_plddt_rnd = RandomForestRegressor(n_estimators=100, random_state=42)
        rf_plddt_rnd.fit(emb_t[train_idx_rnd], plddt_actual[train_idx_rnd])
        pred_plddt_rnd = rf_plddt_rnd.predict(emb_t[pool_idx_rnd])
        surr_top_rnd = pool_idx_rnd[np.argsort(pred_plddt_rnd)[-TOP_K:]]
        
        seqs_surr_rnd = [seqs_all[i] for i in surr_top_rnd]
        best_train_rnd = [seqs_all[train_idx_rnd[np.argmax(plddt_actual[train_idx_rnd])]]]
        
        surr_id_best = avg_cross_identity_masked(seqs_surr_rnd, best_train_rnd, mutable_idx)
        surr_int_sim = avg_pairwise_identity_masked(seqs_surr_rnd, mutable_idx)

        kmeans = KMeans(n_clusters=N_TRAIN, random_state=42, n_init=10)
        kmeans.fit(emb_t)
        train_idx_km, _ = pairwise_distances_argmin_min(kmeans.cluster_centers_, emb_t)
        pool_idx_km = np.setdiff1d(indices, train_idx_km)

        rf_plddt_km = RandomForestRegressor(n_estimators=100, random_state=42)
        rf_plddt_km.fit(emb_t[train_idx_km], plddt_actual[train_idx_km])
        pred_plddt_km = rf_plddt_km.predict(emb_t[pool_idx_km])
        surr_top_km = pool_idx_km[np.argsort(pred_plddt_km)[-TOP_K:]]
        
        seqs_surr_km = [seqs_all[i] for i in surr_top_km]
        best_train_km = [seqs_all[train_idx_km[np.argmax(plddt_actual[train_idx_km])]]]

        km_id_best = avg_cross_identity_masked(seqs_surr_km, best_train_km, mutable_idx)
        km_int_sim = avg_pairwise_identity_masked(seqs_surr_km, mutable_idx)
        
        pool_int_sim = avg_pairwise_identity_masked(seqs_all, mutable_idx)

        results.append({
            'Target': target.replace('cath_test_', ''),
            'Mut_Ratio': f"{mut_ratio*100:.1f}%",
            'Pool_IntSim': f"{pool_int_sim:.3f}",
            'Surr_IntSim': f"{surr_int_sim:.3f}",
            'KM_IntSim': f"{km_int_sim:.3f}",
            'Surr_IdBest': f"{surr_id_best:.3f}",
            'KM_IdBest': f"{km_id_best:.3f}"
        })

    df_res = pd.DataFrame(results)
    
    print("\n| 타겟 ID | 변이 허용 영역<br>(전체 서열 대비 %) | 풀 전체 변이영역 유사도<br>(기본 배경) | 모델 다양성 상실<br>(선택된 10개 간 변이영역 유사도)<br>Random / K-Means | 모델 과적합율<br>(학습 1등 서열과의 변이영역 일치도)<br>Random / K-Means |")
    print("| :--- | :---: | :---: | :---: | :---: |")
    for _, row in df_res.iterrows():
        print(f"| `{row['Target']}` | {row['Mut_Ratio']} | {row['Pool_IntSim']} | {row['Surr_IntSim']} / {row['KM_IntSim']} | {row['Surr_IdBest']} / {row['KM_IdBest']} |")

if __name__ == "__main__":
    df = loto_module.extract_cath_batch_data("cath_outputs")
    embeddings = loto_module.get_esm_embeddings(df)
    run_per_target_analysis_masked(df, embeddings)