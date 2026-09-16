import os
import sys
import numpy as np
import pandas as pd
import mlflow
from sklearn.ensemble import RandomForestRegressor
from sklearn.cluster import KMeans
from sklearn.metrics import pairwise_distances_argmin_min
import importlib.util
import itertools

# Load data extraction and embedding functions
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

def evaluate_metric(actual_scores, pred_scores, pool_idx, top_k):
    """Helper to evaluate pLDDT or SoluProt (higher is better)."""
    surrogate_top_idx = np.argsort(pred_scores)[-top_k:]
    optimal_top_idx = np.argsort(actual_scores)[-top_k:]
        
    surrogate_score = np.mean(actual_scores[surrogate_top_idx])
    random_trials = [np.mean(actual_scores[np.random.choice(len(pool_idx), top_k, replace=False)]) for _ in range(100)]
    random_score = np.mean(random_trials)
    optimal_score = np.mean(actual_scores[optimal_top_idx])
    
    return random_score, surrogate_score, optimal_score

def run_kmeans_active_learning_sim(df, embeddings):
    targets = df['target_id'].unique()
    print(f"Found {len(targets)} targets. Simulating K-Means Active Learning...")
    
    mlflow.set_tracking_uri("http://127.0.0.1:18050")
    mlflow.set_experiment("Active_Learning_Simulation")
    
    N_TRAIN = 30 # Oracle Budget
    TOP_K = 10   # Candidates to select
    
    metrics_tracker = {
        'plddt': {'surrogate': [], 'random': [], 'optimal': []},
        'soluprot': {'surrogate': [], 'random': [], 'optimal': []}
    }
    
    with mlflow.start_run(run_name=f"KMeans_Surrogate_N{N_TRAIN}_Top{TOP_K}"):
        mlflow.log_param("oracle_budget", N_TRAIN)
        mlflow.log_param("selection_top_k", TOP_K)
        mlflow.log_param("model", "RandomForestRegressor")
        mlflow.log_param("sampling_strategy", "K-Means Diversity")
        mlflow.set_tag("targets_analyzed", ", ".join(targets))
        
        for target in targets:
            mask = df['target_id'] == target
            df_t = df[mask].reset_index(drop=True)
            emb_t = embeddings[mask]
            
            valid_mask = ~df_t['plddt_score'].isna()
            df_t = df_t[valid_mask].reset_index(drop=True)
            emb_t = emb_t[valid_mask]
            
            if len(df_t) < 60:
                print(f"Skipping {target}: Only {len(df_t)} valid samples.")
                continue

            with mlflow.start_run(run_name=f"Fold_{target}", nested=True):
                plddt_actual = df_t['plddt_score'].values
                
                has_soluprot = df_t['soluprot_score'].isna().sum() < len(df_t) // 2
                if has_soluprot:
                    df_t['soluprot_score'] = df_t['soluprot_score'].fillna(df_t['soluprot_score'].mean())
                soluprot_actual = df_t['soluprot_score'].values

                # --- Diversity-Aware Sampling (K-Means) ---
                kmeans = KMeans(n_clusters=N_TRAIN, random_state=42, n_init=10)
                kmeans.fit(emb_t)
                # Find the sequence closest to each cluster centroid
                closest_idx, _ = pairwise_distances_argmin_min(kmeans.cluster_centers_, emb_t)
                train_idx = np.unique(closest_idx)
                
                # If unique centroids < N_TRAIN, pad with random selection
                if len(train_idx) < N_TRAIN:
                    remaining = np.setdiff1d(np.arange(len(df_t)), train_idx)
                    padding = np.random.choice(remaining, N_TRAIN - len(train_idx), replace=False)
                    train_idx = np.concatenate([train_idx, padding])
                    
                pool_idx = np.setdiff1d(np.arange(len(df_t)), train_idx)

                # Logging Tags & Params
                train_seq_ids = df_t.loc[train_idx, 'seq_id'].tolist()
                mlflow.set_tag("train_sample_ids", ", ".join(train_seq_ids))
                mlflow.log_param("target_id", target)
                mlflow.log_param("has_soluprot", has_soluprot)

                X_train = emb_t[train_idx]
                X_pool = emb_t[pool_idx]
                
                # --- 1. pLDDT Modeling ---
                rf_plddt = RandomForestRegressor(n_estimators=100, random_state=42)
                rf_plddt.fit(X_train, plddt_actual[train_idx])
                pred_plddt = rf_plddt.predict(X_pool)
                
                surrogate_top_idx = np.argsort(pred_plddt)[-TOP_K:]
                optimal_top_idx = np.argsort(plddt_actual[pool_idx])[-TOP_K:]
                
                # Diversity tracking on the chosen sequences
                seqs_pool = df_t.loc[pool_idx, 'sequence'].tolist()
                seqs_surr = [seqs_pool[i] for i in surrogate_top_idx]
                seqs_opt = [seqs_pool[i] for i in optimal_top_idx]
                
                pool_sim = avg_pairwise_identity(seqs_pool)
                surr_sim = avg_pairwise_identity(seqs_surr)
                cross_sim = avg_cross_identity(seqs_surr, seqs_opt)
                
                mlflow.log_metrics({
                    "pool_similarity": pool_sim,
                    "surrogate_similarity": surr_sim,
                    "surrogate_vs_optimal_similarity": cross_sim
                })
                
                r_p, s_p, o_p = evaluate_metric(plddt_actual[pool_idx], pred_plddt, pool_idx, TOP_K)
                metrics_tracker['plddt']['random'].append(r_p)
                metrics_tracker['plddt']['surrogate'].append(s_p)
                metrics_tracker['plddt']['optimal'].append(o_p)
                mlflow.log_metrics({"plddt_random": r_p, "plddt_surrogate": s_p, "plddt_optimal": o_p})
                
                print(f"Target {target:<15} | pLDDT -> Rand: {r_p:.1f} | Surr(K-Means): {s_p:.1f} | Max: {o_p:.1f} | Sim: {surr_sim:.2f}")

                # --- 2. SoluProt Modeling ---
                if has_soluprot:
                    rf_solu = RandomForestRegressor(n_estimators=100, random_state=42)
                    rf_solu.fit(X_train, soluprot_actual[train_idx])
                    pred_solu = rf_solu.predict(X_pool)
                    
                    r_s, s_s, o_s = evaluate_metric(soluprot_actual[pool_idx], pred_solu, pool_idx, TOP_K)
                    metrics_tracker['soluprot']['random'].append(r_s)
                    metrics_tracker['soluprot']['surrogate'].append(s_s)
                    metrics_tracker['soluprot']['optimal'].append(o_s)
                    mlflow.log_metrics({"soluprot_random": r_s, "soluprot_surrogate": s_s, "soluprot_optimal": o_s})

        # Final Aggregation
        print("\n" + "="*50)
        print("FINAL RESULTS: K-MEANS ACTIVE LEARNING (N=30)")
        print("="*50)
        
        for m_name, vals in metrics_tracker.items():
            if not vals['random']: continue
            
            mean_r = np.mean(vals['random'])
            mean_s = np.mean(vals['surrogate'])
            mean_o = np.mean(vals['optimal'])
            
            gap = mean_o - mean_r
            closed = mean_s - mean_r
            efficiency = (closed / gap * 100) if gap != 0 else 0.0
            
            print(f"--- Metric: {m_name.upper()} ---")
            print(f"Random  : {mean_r:.4f}")
            print(f"Surrogate: {mean_s:.4f}")
            print(f"Optimal : {mean_o:.4f}")
            print(f"Efficiency: {efficiency:.1f}%\n")
            
            # Use concise, top-level metric names so they show up easily in MLflow UI
            mlflow.log_metrics({
                f"{m_name}_efficiency_percent": efficiency,
                f"{m_name}_mean_surrogate": mean_s
            })

def main():
    data_path = "cath_outputs"
    print("Loading data for K-Means Diversity Analysis...")
    df = loto_module.extract_cath_batch_data(data_path)
    embeddings = loto_module.get_esm_embeddings(df)
    run_kmeans_active_learning_sim(df, embeddings)

if __name__ == "__main__":
    main()
