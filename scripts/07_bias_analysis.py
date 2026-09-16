import os
import sys
import numpy as np
import pandas as pd
import importlib.util
import itertools

# Load data extraction and embedding functions
sys.path.append('/opt/protein_pipeline')
spec = importlib.util.spec_from_file_location("loto_module", "scripts/04_cath_loto_validation.py")
loto_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loto_module)

from sklearn.ensemble import RandomForestRegressor

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

def run_bias_analysis(df, embeddings):
    targets = df['target_id'].unique()
    print(f"Found {len(targets)} targets. Analyzing Generation Bias...")
    
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
        
        if len(df_t) < 60:
            continue
            
        plddt_actual = df_t['plddt_score'].values
        seqs_all = df_t['sequence'].tolist()
        
        # Split: 30 for Training (Oracle), Rest for Candidate Pool
        indices = np.arange(len(df_t))
        np.random.seed(42)
        np.random.shuffle(indices)

        train_idx = indices[:N_TRAIN]
        pool_idx = indices[N_TRAIN:]

        X_train = emb_t[train_idx]
        y_train = plddt_actual[train_idx]
        X_pool = emb_t[pool_idx]
        y_pool = plddt_actual[pool_idx]
        
        # Identify the best sequence in training
        train_best_idx = train_idx[np.argmax(y_train)]
        train_best_seq = [seqs_all[train_best_idx]]
        
        # Modeling
        rf_plddt = RandomForestRegressor(n_estimators=100, random_state=42)
        rf_plddt.fit(X_train, y_train)
        pred_pool = rf_plddt.predict(X_pool)
        
        # Selections
        surrogate_top_idx = pool_idx[np.argsort(pred_pool)[-TOP_K:]]
        optimal_top_idx = pool_idx[np.argsort(y_pool)[-TOP_K:]]
        
        # Get sequences
        seqs_surr = [seqs_all[i] for i in surrogate_top_idx]
        seqs_opt = [seqs_all[i] for i in optimal_top_idx]
        
        # Random top 10 from pool
        np.random.seed(123)
        random_top_idx = np.random.choice(pool_idx, TOP_K, replace=False)
        seqs_rand = [seqs_all[i] for i in random_top_idx]
        
        # Calculate identities to the best training sequence
        id_surr_to_best_train = avg_cross_identity(seqs_surr, train_best_seq)
        id_opt_to_best_train = avg_cross_identity(seqs_opt, train_best_seq)
        id_rand_to_best_train = avg_cross_identity(seqs_rand, train_best_seq)
        
        # Calculate similarities among the selections
        sim_surr = avg_pairwise_identity(seqs_surr)
        sim_opt = avg_pairwise_identity(seqs_opt)
        sim_pool = avg_pairwise_identity([seqs_all[i] for i in pool_idx])
        
        results.append({
            'target': target,
            'id_surr_to_best_train': id_surr_to_best_train,
            'id_opt_to_best_train': id_opt_to_best_train,
            'id_rand_to_best_train': id_rand_to_best_train,
            'sim_surr': sim_surr,
            'sim_opt': sim_opt,
            'sim_pool': sim_pool
        })
        
        print(f"Target: {target}")
        print(f"  Surrogate vs Train Best : {id_surr_to_best_train:.4f}")
        print(f"  Optimal vs Train Best   : {id_opt_to_best_train:.4f}")
        print(f"  Random vs Train Best    : {id_rand_to_best_train:.4f}")
        print(f"  Pool Background Sim     : {sim_pool:.4f}")

    # Aggregate
    res_df = pd.DataFrame(results)
    print("\n=== AGGREGATE RESULTS ===")
    print(res_df.mean(numeric_only=True))

if __name__ == "__main__":
    data_path = "cath_outputs"
    df = loto_module.extract_cath_batch_data(data_path)
    embeddings = loto_module.get_esm_embeddings(df)
    run_bias_analysis(df, embeddings)
