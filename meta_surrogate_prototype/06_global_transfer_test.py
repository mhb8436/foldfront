import pandas as pd
import numpy as np
from sklearn.neural_network import MLPRegressor
from sklearn.metrics import mean_squared_error
import os

def run_transfer_test():
    input_csv = "meta_surrogate_prototype/extracted_data_full.csv"
    input_npy = "meta_surrogate_prototype/embeddings.npy"
    
    df = pd.read_csv(input_csv)
    embeddings = np.load(input_npy)
    
    target_run_id = "admin_full_pipeline_260413"
    
    # 1. Split Data into "Historical (Global)" and "Current (Target)"
    global_mask = df['run_id'] != target_run_id
    target_mask = df['run_id'] == target_run_id
    
    X_global = embeddings[global_mask]
    y_global_solu = df[global_mask]['soluprot'].values
    
    X_target = embeddings[target_mask]
    y_target_solu = df[target_mask]['soluprot'].values
    
    # Simulate Relax scores for both based on sequence (consistent logic)
    def get_relax(seqs):
        relax = []
        for seq in seqs:
            hydro = sum(1 for c in seq if c in "VILMFWC") / len(seq)
            relax.append(-(hydro * 10 + np.random.normal(0, 0.5)))
        return np.array(relax)
    
    y_global_relax = get_relax(df[global_mask]['sequence'].values)
    y_target_relax = get_relax(df[target_mask]['sequence'].values)

    def min_max_norm(arr):
        arr = np.array(arr)
        if arr.max() == arr.min(): return arr * 0.0
        return (arr - arr.min()) / (arr.max() - arr.min() + 1e-9)

    # 2. Train GLOBAL MODELS (Pre-training on ~3600 samples)
    print(f"--- Pre-training Global Meta-Surrogate on {len(X_global)} historical samples ---")
    mlp_global_solu = MLPRegressor(hidden_layer_sizes=(256, 128), max_iter=500, random_state=42)
    mlp_global_solu.fit(X_global, y_global_solu)
    
    mlp_global_relax = MLPRegressor(hidden_layer_sizes=(256, 128), max_iter=500, random_state=42)
    mlp_global_relax.fit(X_global, y_global_relax)
    
    # 3. Evaluation on Target (120 seqs)
    gt_target_combined = (min_max_norm(y_target_solu) * 0.5) + (min_max_norm(y_target_relax) * 0.5)
    
    print(f"\nTarget Pool Statistics:")
    print(f"Max Combined Score: {np.max(gt_target_combined):.4f}")
    print(f"Pool Average: {np.mean(gt_target_combined):.4f}")

    # SCENARIO 1: Zero-Shot (Use Global model directly)
    pred_solu_0 = mlp_global_solu.predict(X_target)
    pred_relax_0 = mlp_global_relax.predict(X_target)
    acq_0 = (min_max_norm(pred_solu_0) * 0.5) + (min_max_norm(pred_relax_0) * 0.5)
    
    # SCENARIO 2: From Scratch 10-Shot (What we failed at before)
    np.random.seed(42)
    t_idx = np.arange(len(X_target))
    np.random.shuffle(t_idx)
    train_idx = t_idx[:10]
    pool_idx = t_idx[10:]
    
    mlp_scratch_solu = MLPRegressor(hidden_layer_sizes=(64,), max_iter=1000, random_state=42)
    mlp_scratch_solu.fit(X_target[train_idx], y_target_solu[train_idx])
    mlp_scratch_relax = MLPRegressor(hidden_layer_sizes=(64,), max_iter=1000, random_state=42)
    mlp_scratch_relax.fit(X_target[train_idx], y_target_relax[train_idx])
    
    pred_solu_scratch = mlp_scratch_solu.predict(X_target[pool_idx])
    pred_relax_scratch = mlp_scratch_relax.predict(X_target[pool_idx])
    acq_scratch = (min_max_norm(pred_solu_scratch) * 0.5) + (min_max_norm(pred_relax_scratch) * 0.5)

    # SCENARIO 3: Few-Shot Fine-tuning (Global + 10 samples)
    # For prototype simplicity, we emulate fine-tuning by averaging Global and Scratch predictions
    # or we could actually continue training. Let's do a weighted average (Prior-Informed).
    acq_fewshot = (acq_0[pool_idx] * 0.7) + (acq_scratch * 0.3)

    # Results Table
    def get_top_avg(acq, gt, n=11):
        top_idx = np.argsort(acq)[::-1][:n]
        return np.mean(gt[top_idx]), np.max(gt[top_idx])

    avg_0, max_0 = get_top_avg(acq_0, gt_target_combined)
    avg_scratch, max_scratch = get_top_avg(acq_scratch, gt_target_combined[pool_idx])
    avg_few, max_few = get_top_avg(acq_fewshot, gt_target_combined[pool_idx])
    
    # Random Baseline
    random_gt = gt_target_combined[np.random.choice(len(gt_target_combined), 11, replace=False)]
    avg_rand = np.mean(random_gt)

    print("\nResults (Top 10% Selection Performance):")
    print(f"{'Method':<25} | {'Avg Score':<10} | {'Max Found':<10} | {'Improvement'}")
    print("-" * 70)
    print(f"{'Random Search':<25} | {avg_rand:<10.4f} | {np.max(random_gt):<10.4f} | -")
    print(f"{'Scratch (10-shot)':<25} | {avg_scratch:<10.4f} | {max_scratch:<10.4f} | {((avg_scratch/avg_rand)-1)*100:>+5.1f}%")
    print(f"{'Global Model (Zero-shot)':<25} | {avg_0:<10.4f} | {max_0:<10.4f} | {((avg_0/avg_rand)-1)*100:>+5.1f}%")
    print(f"{'Fine-tuned (10-shot)':<25} | {avg_few:<10.4f} | {max_few:<10.4f} | {((avg_few/avg_rand)-1)*100:>+5.1f}%")

if __name__ == "__main__":
    run_transfer_test()
