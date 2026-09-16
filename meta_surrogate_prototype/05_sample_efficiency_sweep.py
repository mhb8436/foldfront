import pandas as pd
import numpy as np
from sklearn.neural_network import MLPRegressor
from sklearn.metrics import mean_squared_error
import os

def run_sweep():
    input_csv = "meta_surrogate_prototype/extracted_data_full.csv"
    input_npy = "meta_surrogate_prototype/embeddings.npy"
    
    if not os.path.exists(input_csv) or not os.path.exists(input_npy):
        print("Data files not found.")
        return

    df = pd.read_csv(input_csv)
    embeddings = np.load(input_npy)
    
    target_run_id = "admin_full_pipeline_260413"
    target_mask = df['run_id'] == target_run_id
    target_df = df[target_mask].copy()
    target_embeddings = embeddings[target_mask]
    
    if len(target_df) < 120:
        target_df = target_df
        target_embeddings = target_embeddings
    else:
        target_df = target_df.iloc[:120]
        target_embeddings = target_embeddings[:120]
        
    y_solu_actual = target_df['soluprot'].values
    seqs_target = target_df['sequence'].values
    relax_actual = []
    for seq in seqs_target:
        hydro = sum(1 for c in seq if c in "VILMFWC") / len(seq)
        relax_actual.append(-(hydro * 10 + np.random.normal(0, 0.5)))
    relax_actual = np.array(relax_actual)
    
    def min_max_norm(arr):
        arr = np.array(arr)
        return (arr - arr.min()) / (arr.max() - arr.min() + 1e-9)
    
    gt_combined = (min_max_norm(y_solu_actual) * 0.5) + (min_max_norm(relax_actual) * 0.5)
    
    sample_sizes = [5, 10, 20, 30, 40, 50, 60]
    results = []

    print(f"{'Samples':<10} | {'MLP Avg':<10} | {'Random Avg':<10} | {'Max Found':<10} | {'Status'}")
    print("-" * 65)

    for n in sample_sizes:
        np.random.seed(42)
        total_idx = np.arange(len(target_df))
        np.random.shuffle(total_idx)
        
        train_idx = total_idx[:n]
        pool_idx = total_idx[n:]
        
        X_train = target_embeddings[train_idx]
        y_train_solu = y_solu_actual[train_idx]
        y_train_relax = relax_actual[train_idx]
        
        X_pool = target_embeddings[pool_idx]
        y_pool_combined = gt_combined[pool_idx]
        
        # Train MLP from scratch
        # We adjust layer size slightly based on N to be fair
        hidden = (64,) if n < 30 else (128, 64)
        mlp_solu = MLPRegressor(hidden_layer_sizes=hidden, max_iter=2000, random_state=42)
        mlp_solu.fit(X_train, y_train_solu)
        
        mlp_relax = MLPRegressor(hidden_layer_sizes=hidden, max_iter=2000, random_state=42)
        mlp_relax.fit(X_train, y_train_relax)
        
        pred_solu = mlp_solu.predict(X_pool)
        pred_relax = mlp_relax.predict(X_pool)
        
        acq_combined = (min_max_norm(pred_solu) * 0.5) + (min_max_norm(pred_relax) * 0.5)
        
        # Select top 10% of the pool
        n_select = max(1, int(len(pool_idx) * 0.1))
        top_pred_idx = np.argsort(acq_combined)[::-1][:n_select]
        mlp_avg = np.mean(y_pool_combined[top_pred_idx])
        max_found = np.max(y_pool_combined[top_pred_idx])
        
        random_idx = np.random.choice(len(y_pool_combined), n_select, replace=False)
        random_avg = np.mean(y_pool_combined[random_idx])
        
        status = "✅ WIN" if mlp_avg > random_avg else "❌ FAIL"
        print(f"{n:<10} | {mlp_avg:<10.4f} | {random_avg:<10.4f} | {max_found:<10.4f} | {status}")
        
        results.append({
            "samples": n,
            "mlp_avg": mlp_avg,
            "random_avg": random_avg,
            "max_found": max_found
        })

    print("\nTarget Dataset Max Score:", round(np.max(gt_combined), 4))

if __name__ == "__main__":
    run_sweep()
