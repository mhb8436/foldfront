import pandas as pd
import numpy as np
from sklearn.neural_network import MLPRegressor
import os

def strict_loso_validation():
    input_csv = "meta_surrogate_prototype/extracted_data_full.csv"
    input_npy = "meta_surrogate_prototype/embeddings.npy"
    
    if not os.path.exists(input_csv) or not os.path.exists(input_npy):
        print("Data files not found.")
        return

    df = pd.read_csv(input_csv)
    embeddings = np.load(input_npy)
    
    run_counts = df['run_id'].value_counts()
    major_runs = run_counts[run_counts >= 100].index.tolist()
    
    def min_max_norm(arr):
        arr = np.array(arr)
        if arr.max() == arr.min(): return arr * 0.0
        return (arr - arr.min()) / (arr.max() - arr.min() + 1e-9)
        
    def get_relax(seqs):
        relax = []
        for seq in seqs:
            hydro = sum(1 for c in seq if c in "VILMFWC") / len(seq)
            relax.append(-(hydro * 10 + np.random.normal(0, 0.5)))
        return np.array(relax)

    results = []
    print(f"\n{'='*95}")
    print(f"{'Target':<20} | {'Rand':<8} | {'Scratch':<8} | {'Zero-Shot':<10} | {'Fine-tuned':<10} | {'Max Found'}")
    print(f"{'':<20} | {'(Base)':<8} | {'(10-shot)':<8} | {'(Global)':<10} | {'(10-shot)':<10} |")
    print(f"{'='*95}")

    for test_run_id in major_runs:
        train_mask = df['run_id'] != test_run_id
        test_mask = df['run_id'] == test_run_id
        
        X_train = embeddings[train_mask]
        y_train_solu = df[train_mask]['soluprot'].values
        y_train_relax = get_relax(df[train_mask]['sequence'].values)
        
        X_test = embeddings[test_mask]
        y_test_solu = df[test_mask]['soluprot'].values
        y_test_relax = get_relax(df[test_mask]['sequence'].values)
        
        # 1. Train Global Surrogate (Zero-Shot)
        mlp_solu = MLPRegressor(hidden_layer_sizes=(128, 64), max_iter=500, random_state=42)
        mlp_solu.fit(X_train, y_train_solu)
        
        mlp_relax = MLPRegressor(hidden_layer_sizes=(128, 64), max_iter=500, random_state=42)
        mlp_relax.fit(X_train, y_train_relax)
        
        gt_combined = (min_max_norm(y_test_solu) * 0.5) + (min_max_norm(y_test_relax) * 0.5)
        
        pred_solu_zs = mlp_solu.predict(X_test)
        pred_relax_zs = mlp_relax.predict(X_test)
        acq_zs = (min_max_norm(pred_solu_zs) * 0.5) + (min_max_norm(pred_relax_zs) * 0.5)
        
        n_select = max(1, int(len(X_test) * 0.1))
        
        # 2. Random Selection (Baseline)
        np.random.seed(42)
        random_idx = np.random.choice(len(gt_combined), n_select, replace=False)
        rand_avg = np.mean(gt_combined[random_idx])
        
        # Split Target Data for Few-Shot Scenarios
        train_scratch_idx = np.arange(len(X_test))[:10]
        test_scratch_idx = np.arange(len(X_test))[10:]
        
        # 3. Scratch (10-shot)
        if len(test_scratch_idx) > n_select:
            mlp_scratch_solu = MLPRegressor(hidden_layer_sizes=(64,), max_iter=1000, random_state=42)
            mlp_scratch_solu.fit(X_test[train_scratch_idx], y_test_solu[train_scratch_idx])
            mlp_scratch_relax = MLPRegressor(hidden_layer_sizes=(64,), max_iter=1000, random_state=42)
            mlp_scratch_relax.fit(X_test[train_scratch_idx], y_test_relax[train_scratch_idx])
            
            p_s_scratch = mlp_scratch_solu.predict(X_test[test_scratch_idx])
            p_r_scratch = mlp_scratch_relax.predict(X_test[test_scratch_idx])
            acq_scratch = (min_max_norm(p_s_scratch) * 0.5) + (min_max_norm(p_r_scratch) * 0.5)
            
            top_s_idx = np.argsort(acq_scratch)[::-1][:n_select]
            scratch_avg = np.mean(gt_combined[test_scratch_idx][top_s_idx])
            
            # 4. Fine-Tuned (10-shot): Ensemble of Global and Scratch 
            # Evaluated on the same untested test_scratch_idx
            acq_zs_test_subset = acq_zs[test_scratch_idx]
            acq_ft = (acq_zs_test_subset * 0.7) + (acq_scratch * 0.3)
            
            top_ft_idx = np.argsort(acq_ft)[::-1][:n_select]
            ft_avg = np.mean(gt_combined[test_scratch_idx][top_ft_idx])
            ft_max = np.max(gt_combined[test_scratch_idx][top_ft_idx])
            
            # Recalculate Zero-Shot average on the same test subset for fair comparison
            top_zs_idx = np.argsort(acq_zs_test_subset)[::-1][:n_select]
            zs_avg = np.mean(gt_combined[test_scratch_idx][top_zs_idx])
            zs_max = np.max(gt_combined[test_scratch_idx][top_zs_idx])
            
        else:
            scratch_avg, ft_avg, zs_avg, zs_max, ft_max = 0.0, 0.0, 0.0, 0.0, 0.0

        max_found_val = max(zs_max, ft_max)
        print(f"{test_run_id[:20]:<20} | {rand_avg:<8.4f} | {scratch_avg:<8.4f} | {zs_avg:<10.4f} | {ft_avg:<10.4f} | {max_found_val:.4f}")
        
        results.append({
            "target": test_run_id,
            "rand_avg": rand_avg,
            "scratch_avg": scratch_avg,
            "zs_avg": zs_avg,
            "ft_avg": ft_avg
        })

if __name__ == "__main__":
    strict_loso_validation()
