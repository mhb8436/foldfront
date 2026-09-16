import os
import sys
import numpy as np
import pandas as pd
import mlflow
from sklearn.ensemble import RandomForestRegressor
import importlib.util
import itertools

# Load data extraction and embedding functions from script 04
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

def evaluate_metric(metric_name, actual_scores, pred_scores, pool_idx, top_k, is_higher_better=True):
    """Helper to evaluate a single metric."""
    if is_higher_better:
        surrogate_top_idx = np.argsort(pred_scores)[-top_k:]
        optimal_top_idx = np.argsort(actual_scores)[-top_k:]
    else:
        surrogate_top_idx = np.argsort(pred_scores)[:top_k]
        optimal_top_idx = np.argsort(actual_scores)[:top_k]
        
    surrogate_score = np.mean(actual_scores[surrogate_top_idx])
    random_trials = [np.mean(actual_scores[np.random.choice(len(pool_idx), top_k, replace=False)]) for _ in range(100)]
    random_score = np.mean(random_trials)
    optimal_score = np.mean(actual_scores[optimal_top_idx])
    
    return random_score, surrogate_score, optimal_score

def run_active_learning_sim(df, embeddings):
    targets = df['target_id'].unique()
    print(f"Found {len(targets)} targets. Simulating Local Active Learning...")
    
    mlflow.set_tracking_uri("http://127.0.0.1:18050")
    mlflow.set_experiment("Active_Learning_Simulation")
    
    N_TRAIN = 30 # Oracle Budget
    TOP_K = 10   # How many to select from the pool
    
    metrics_tracker = {
        'plddt': {'surrogate': [], 'random': [], 'optimal': []},
        'soluprot': {'surrogate': [], 'random': [], 'optimal': []},
        'relax': {'surrogate': [], 'random': [], 'optimal': []}
    }
    
    with mlflow.start_run(run_name=f"Local_Surrogate_N{N_TRAIN}_Top{TOP_K}"):
        mlflow.log_param("oracle_budget", N_TRAIN)
        mlflow.log_param("selection_top_k", TOP_K)
        mlflow.log_param("model", "RandomForestRegressor")
        mlflow.set_tag("targets_analyzed", ", ".join(targets))
        
        for target in targets:
            mask = df['target_id'] == target
            df_t = df[mask].reset_index(drop=True)
            emb_t = embeddings[mask]
            
            # Remove NaNs ONLY for plddt to keep sample size high, others will be handled
            valid_mask = ~df_t['plddt_score'].isna()
            df_t = df_t[valid_mask].reset_index(drop=True)
            emb_t = emb_t[valid_mask]
            
            if len(df_t) < 60:
                print(f"Skipping {target}: Only {len(df_t)} valid samples (need at least 60).")
                continue

            with mlflow.start_run(run_name=f"Fold_{target}", nested=True):
                # Data prep
                plddt_actual = df_t['plddt_score'].values
                
                has_soluprot = df_t['soluprot_score'].isna().sum() < len(df_t) // 2
                if has_soluprot:
                    df_t['soluprot_score'] = df_t['soluprot_score'].fillna(df_t['soluprot_score'].mean())
                soluprot_actual = df_t['soluprot_score'].values

                has_relax = df_t['relax_score'].isna().sum() < len(df_t) // 2
                # If Relax is missing, we simply won't use it. No simulation.
                if has_relax:
                    df_t['relax_score'] = df_t['relax_score'].fillna(df_t['relax_score'].mean())
                    relax_actual = df_t['relax_score'].values

                # Split: 30 for Training (Oracle), Rest for Candidate Pool
                indices = np.arange(len(df_t))
                np.random.seed(42)
                np.random.shuffle(indices)

                train_idx = indices[:N_TRAIN]
                pool_idx = indices[N_TRAIN:]

                # Logging Tags & Params
                train_seq_ids = df_t.loc[train_idx, 'seq_id'].tolist()
                mlflow.set_tag("train_sample_ids", ", ".join(train_seq_ids))
                mlflow.log_param("target_id", target)
                mlflow.log_param("train_samples_count", len(train_idx))
                mlflow.log_param("candidate_pool_count", len(pool_idx))

                X_train = emb_t[train_idx]
                X_pool = emb_t[pool_idx]
                
                # --- 1. pLDDT Modeling & Evaluation ---
                rf_plddt = RandomForestRegressor(n_estimators=100, random_state=42)
                rf_plddt.fit(X_train, plddt_actual[train_idx])
                pred_plddt = rf_plddt.predict(X_pool)
                
                # Evaluation indices
                surrogate_top_idx = np.argsort(pred_plddt)[-TOP_K:]
                optimal_top_idx = np.argsort(plddt_actual[pool_idx].flatten())[-TOP_K:]
                
                # --- Diversity & Bias Analysis ---
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
                
                print(f"  [Diversity] Pool Sim: {pool_sim:.2f} | Surr Sim: {surr_sim:.2f} | Surr vs Opt: {cross_sim:.2f}")
                
                r_p, s_p, o_p = evaluate_metric('plddt', plddt_actual[pool_idx].flatten(), pred_plddt, np.arange(len(pool_idx)), TOP_K, True)
                metrics_tracker['plddt']['random'].append(r_p)
                metrics_tracker['plddt']['surrogate'].append(s_p)
                metrics_tracker['plddt']['optimal'].append(o_p)
                mlflow.log_metrics({"plddt_random": r_p, "plddt_surrogate": s_p, "plddt_optimal": o_p})

                # --- 2. SoluProt Modeling & Evaluation ---
                if has_soluprot:
                    rf_solu = RandomForestRegressor(n_estimators=100, random_state=42)
                    rf_solu.fit(X_train, soluprot_actual[train_idx])
                    pred_solu = rf_solu.predict(X_pool)
                    
                    r_s, s_s, o_s = evaluate_metric('soluprot', soluprot_actual[pool_idx], pred_solu, pool_idx, TOP_K, True)
                    metrics_tracker['soluprot']['random'].append(r_s)
                    metrics_tracker['soluprot']['surrogate'].append(s_s)
                    metrics_tracker['soluprot']['optimal'].append(o_s)
                    mlflow.log_metrics({"soluprot_random": r_s, "soluprot_surrogate": s_s, "soluprot_optimal": o_s})

                # --- 3. Relax Modeling & Evaluation ---
                if has_relax:
                    rf_relax = RandomForestRegressor(n_estimators=100, random_state=42)
                    rf_relax.fit(X_train, relax_actual[train_idx])
                    pred_relax = rf_relax.predict(X_pool)
                    
                    r_r, s_r, o_r = evaluate_metric('relax', relax_actual[pool_idx], pred_relax, pool_idx, TOP_K, False)
                    metrics_tracker['relax']['random'].append(r_r)
                    metrics_tracker['relax']['surrogate'].append(s_r)
                    metrics_tracker['relax']['optimal'].append(o_r)
                    mlflow.log_metrics({"relax_random": r_r, "relax_surrogate": s_r, "relax_optimal": o_r})
                
                # Format print statement dynamically based on available metrics
                print_str = f"Target {target:<15} | pLDDT Surr: {s_p:.1f}"
                if has_soluprot:
                    print_str += f" | Solu Surr: {s_s:.3f}"
                if has_relax:
                    print_str += f" | Relax Surr: {s_r:.1f}"
                print(print_str)
                    
        # Final Aggregation
        print("\n" + "="*50)
        print("FINAL RESULTS: INDIVIDUAL METRIC EFFICIENCY (N=30)")
        print("="*50)
        
        for m_name, vals in metrics_tracker.items():
            if not vals['random']: continue
            
            mean_r = np.mean(vals['random'])
            mean_s = np.mean(vals['surrogate'])
            mean_o = np.mean(vals['optimal'])
            
            # Efficiency calculation
            if m_name == 'relax':
                # For relax, smaller is better. Gap is Random - Optimal
                gap = mean_r - mean_o
                closed = mean_r - mean_s
            else:
                # For others, larger is better. Gap is Optimal - Random
                gap = mean_o - mean_r
                closed = mean_s - mean_r
                
            efficiency = (closed / gap * 100) if gap != 0 else 0.0
            
            print(f"--- Metric: {m_name.upper()} ---")
            print(f"Random  : {mean_r:.4f}")
            print(f"Surrogate: {mean_s:.4f}")
            print(f"Optimal : {mean_o:.4f}")
            print(f"Gap Closed (Efficiency): {efficiency:.1f}%\n")
            
            mlflow.log_metrics({
                f"global_mean_{m_name}_random": mean_r,
                f"global_mean_{m_name}_surrogate": mean_s,
                f"global_mean_{m_name}_optimal": mean_o,
                f"global_{m_name}_efficiency_percent": efficiency
            })

def main():
    data_path = "cath_outputs"
    print("Loading data...")
    df = loto_module.extract_cath_batch_data(data_path)
    embeddings = loto_module.get_esm_embeddings(df)
    run_active_learning_sim(df, embeddings)

if __name__ == "__main__":
    main()