import pandas as pd
import numpy as np
from sklearn.neural_network import MLPRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error
import os

def test_few_shot_capability():
    input_csv = "meta_surrogate_prototype/extracted_data_full.csv"
    input_npy = "meta_surrogate_prototype/embeddings.npy"
    
    if not os.path.exists(input_csv) or not os.path.exists(input_npy):
        print("Data files not found.")
        return

    df = pd.read_csv(input_csv)
    embeddings = np.load(input_npy)
    
    # 1. Filter out sequences from admin_full_pipeline_260413
    # We will pretend the other ~3,600 sequences DO NOT EXIST to test the "Zero-Knowledge Few-Shot" scenario.
    # We only use the 120 sequences from the admin_full_pipeline_260413 run.
    target_run_id = "admin_full_pipeline_260413"
    target_mask = df['run_id'] == target_run_id
    
    target_df = df[target_mask].copy()
    target_embeddings = embeddings[target_mask]
    
    if len(target_df) < 120:
        print(f"Only found {len(target_df)} sequences for {target_run_id}, using all available.")
    else:
        # Take exactly 120 for the test if there are more
        target_df = target_df.iloc[:120]
        target_embeddings = target_embeddings[:120]
        
    print(f"Total pool size for this target: {len(target_df)} sequences")
    
    # 2. Prepare Ground Truth for the 120 sequences
    y_solu_actual = target_df['soluprot'].values
    
    # Simulate Relax scores for the target based on hydrophobicity (same as before)
    seqs_target = target_df['sequence'].values
    relax_actual = []
    for seq in seqs_target:
        hydro = sum(1 for c in seq if c in "VILMFWC") / len(seq)
        relax_actual.append(-(hydro * 10 + np.random.normal(0, 0.5)))
    relax_actual = np.array(relax_actual)
    
    def min_max_norm(arr):
        arr = np.array(arr)
        return (arr - arr.min()) / (arr.max() - arr.min() + 1e-9)
    
    # Ground Truth Pareto Score (50% SoluProt, 50% Relax)
    gt_combined = (min_max_norm(y_solu_actual) * 0.5) + (min_max_norm(relax_actual) * 0.5)
    
    # 3. The "Few-Shot" Scenario: 
    # We randomly pick ONLY 10 sequences from this 120-pool to act as our AF2/Oracle calls.
    # We train the MLP completely from scratch on just these 10 data points.
    
    num_initial_samples = 10
    print(f"\n--- Testing Few-Shot Capability (Training on ONLY {num_initial_samples} random samples) ---")
    
    np.random.seed(42)
    total_idx = np.arange(len(target_df))
    np.random.shuffle(total_idx)
    
    train_idx = total_idx[:num_initial_samples]
    pool_idx = total_idx[num_initial_samples:] # The remaining 110 untested
    
    X_train = target_embeddings[train_idx]
    y_train_solu = y_solu_actual[train_idx]
    y_train_relax = relax_actual[train_idx]
    
    X_pool = target_embeddings[pool_idx]
    y_pool_combined = gt_combined[pool_idx]
    
    # 4. Train Models from Scratch on 10 samples
    # We use a very small MLP because we have almost no data to prevent severe overfitting
    mlp_solu = MLPRegressor(hidden_layer_sizes=(64,), max_iter=1000, random_state=42, early_stopping=False)
    mlp_solu.fit(X_train, y_train_solu)
    
    mlp_relax = MLPRegressor(hidden_layer_sizes=(64,), max_iter=1000, random_state=42, early_stopping=False)
    mlp_relax.fit(X_train, y_train_relax)
    
    # 5. Predict on the remaining 110 untested sequences
    pred_solu = mlp_solu.predict(X_pool)
    pred_relax = mlp_relax.predict(X_pool)
    
    acq_combined = (min_max_norm(pred_solu) * 0.5) + (min_max_norm(pred_relax) * 0.5)
    
    # 6. Evaluation: Select top 10% (~11 sequences) from the 110 pool
    n_select = 11
    top_pred_idx = np.argsort(acq_combined)[::-1][:n_select]
    top_actual_combined = y_pool_combined[top_pred_idx]
    
    print(f"True Max Combined Score in the unseen 110 pool: {np.max(y_pool_combined):.4f}")
    print(f"Average Combined Score in the unseen 110 pool: {np.mean(y_pool_combined):.4f}")
    
    print(f"\nMLP Meta-Surrogate (trained on {num_initial_samples} seqs) selected {n_select} sequences.")
    print(f"Average actual score of selected {n_select}: {np.mean(top_actual_combined):.4f}")
    
    random_idx = np.random.choice(len(y_pool_combined), n_select, replace=False)
    random_actual_combined = y_pool_combined[random_idx]
    print(f"Average actual score of Random {n_select}: {np.mean(random_actual_combined):.4f}")
    
    print(f"\nTop 3 Actual Scores Found by MLP: {[round(s, 4) for s in np.sort(top_actual_combined)[::-1][:3]]}")
    
    # For comparison, did the random selection find anything close to the max?
    print(f"Top 3 Actual Scores Found by Random: {[round(s, 4) for s in np.sort(random_actual_combined)[::-1][:3]]}")
    
    if np.mean(top_actual_combined) > np.mean(random_actual_combined):
        print(f"\n✅ SUCCESS: Even with ONLY {num_initial_samples} training points, ESM embeddings provide enough structure for the MLP to find the Pareto Front!")
    else:
        print(f"\n❌ FAILURE: {num_initial_samples} samples is not enough to train from scratch without a pre-trained backbone.")

if __name__ == "__main__":
    test_few_shot_capability()
