import os
import pandas as pd
import numpy as np
import mlflow
import mlflow.sklearn
from sklearn.neural_network import MLPRegressor
from sklearn.metrics import mean_squared_error
import torch
from transformers import EsmModel, AutoTokenizer

def load_cath_data(csv_path: str) -> pd.DataFrame:
    """Loads CATH batch data and filters valid rows."""
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Data file not found: {csv_path}")
    
    df = pd.read_csv(csv_path)
    required_cols = ['target_id', 'sequence', 'soluprot_score', 'plddt_score']
    missing_cols = [c for c in required_cols if c not in df.columns]
    
    if missing_cols:
        raise ValueError(f"Missing required columns: {missing_cols}")
        
    df = df.dropna(subset=['target_id', 'sequence'])
    return df

def get_esm_embeddings(df: pd.DataFrame, cache_path: str = "cath_embeddings.npy") -> np.ndarray:
    """Generates or loads 320D ESM-2 embeddings for the sequences."""
    if os.path.exists(cache_path):
        print(f"Loading cached embeddings from {cache_path}")
        return np.load(cache_path)
        
    print("Generating ESM-2 embeddings... This may take a few minutes.")
    model_name = "facebook/esm2_t6_8M_UR50D"
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = EsmModel.from_pretrained(model_name)
    model.eval()
    
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = model.to(device)
    
    embeddings = []
    sequences = df['sequence'].tolist()
    
    with torch.no_grad():
        for i in range(0, len(sequences), 16): # Batch size 16
            batch_seqs = sequences[i:i+16]
            inputs = tokenizer(batch_seqs, return_tensors="pt", padding=True, truncation=True, max_length=1024)
            inputs = {k: v.to(device) for k, v in inputs.items()}
            outputs = model(**inputs)
            
            # Mean pooling over sequence length, ignoring padding tokens
            attention_mask = inputs['attention_mask'].unsqueeze(-1)
            sum_embeddings = torch.sum(outputs.last_hidden_state * attention_mask, dim=1)
            sum_mask = torch.clamp(attention_mask.sum(dim=1), min=1e-9)
            mean_pooled = sum_embeddings / sum_mask
            
            embeddings.append(mean_pooled.cpu().numpy())
            print(f"Processed {min(i+16, len(sequences))}/{len(sequences)}")
            
    final_embeddings = np.vstack(embeddings)
    np.save(cache_path, final_embeddings)
    return final_embeddings

import time

def run_loto_validation(df: pd.DataFrame, embeddings: np.ndarray):
    targets = df['target_id'].unique()
    print(f"Found {len(targets)} unique targets for LOTO.")
    
    mlflow.set_tracking_uri("http://127.0.0.1:18050")
    mlflow.set_experiment("CATH_LOTO_Validation")
    
    mlp_params = {"hidden_layer_sizes": (256, 128), "max_iter": 500, "random_state": 42}
    
    solu_mses = []
    plddt_mses = []
    
    with mlflow.start_run(run_name=f"LOTO_Summary_{int(time.time())}"):
        mlflow.log_param("num_targets", len(targets))
        mlflow.log_param("total_sequences", len(df))
        mlflow.log_params({f"mlp_{k}": v for k, v in mlp_params.items()})
        
        for target in targets:
            print(f"\n--- Processing Target: {target} ---")
            with mlflow.start_run(run_name=f"Fold_{target}", nested=True):
                # Split logic
                test_mask = df['target_id'] == target
                train_mask = ~test_mask
                
                X_train = embeddings[train_mask]
                X_test = embeddings[test_mask]
                
                mlflow.log_param("target_id", target)
                mlflow.log_param("train_size", len(X_train))
                mlflow.log_param("test_size", len(X_test))
                
                # SoluProt Training
                y_solu_train = df.loc[train_mask, 'soluprot_score'].values
                y_solu_test = df.loc[test_mask, 'soluprot_score'].values
                
                valid_solu_train_mask = ~np.isnan(y_solu_train)
                valid_solu_test_mask = ~np.isnan(y_solu_test)
                
                if valid_solu_train_mask.sum() > 0 and valid_solu_test_mask.sum() > 0:
                    mlp_solu = MLPRegressor(**mlp_params)
                    mlp_solu.fit(X_train[valid_solu_train_mask], y_solu_train[valid_solu_train_mask])
                    pred_s = mlp_solu.predict(X_test[valid_solu_test_mask])
                    mse_s = mean_squared_error(y_solu_test[valid_solu_test_mask], pred_s)
                    solu_mses.append(mse_s)
                    mlflow.log_metric("soluprot_mse", mse_s)
                    print(f"SoluProt MSE: {mse_s:.4f}")
                
                # pLDDT Training
                y_plddt_train = df.loc[train_mask, 'plddt_score'].values
                y_plddt_test = df.loc[test_mask, 'plddt_score'].values
                
                valid_plddt_train_mask = ~np.isnan(y_plddt_train)
                valid_plddt_test_mask = ~np.isnan(y_plddt_test)
                
                if valid_plddt_train_mask.sum() > 0 and valid_plddt_test_mask.sum() > 0:
                    mlp_plddt = MLPRegressor(**mlp_params)
                    mlp_plddt.fit(X_train[valid_plddt_train_mask], y_plddt_train[valid_plddt_train_mask])
                    pred_p = mlp_plddt.predict(X_test[valid_plddt_test_mask])
                    mse_p = mean_squared_error(y_plddt_test[valid_plddt_test_mask], pred_p)
                    plddt_mses.append(mse_p)
                    mlflow.log_metric("plddt_mse", mse_p)
                    print(f"pLDDT MSE: {mse_p:.4f}")

        # Log aggregate metrics to parent
        if solu_mses:
            avg_solu = np.mean(solu_mses)
            mlflow.log_metric("avg_loto_soluprot_mse", avg_solu)
            print(f"\n=> Average SoluProt LOTO MSE: {avg_solu:.4f}")
        if plddt_mses:
            avg_plddt = np.mean(plddt_mses)
            mlflow.log_metric("avg_loto_plddt_mse", avg_plddt)
            print(f"=> Average pLDDT LOTO MSE: {avg_plddt:.4f}")

import glob
import json
import sys

def extract_cath_batch_data(outputs_dir: str) -> pd.DataFrame:
    """Extracts target, sequence, soluprot, plddt, and relax from pipeline summary JSONs."""
    records = []
    run_dirs = glob.glob(os.path.join(outputs_dir, "*"))
    
    def _norm_scores(raw_dict):
        """Converts 'target:1' style keys to 'target_1' to match ID convention."""
        return {str(k).replace(":", "_"): v for k, v in raw_dict.items()}

    for run_dir in run_dirs:
        if not os.path.isdir(run_dir):
            continue
        target_id = os.path.basename(run_dir)
        
        # Iterate through tiers (30, 50, 70)
        tier_dirs = glob.glob(os.path.join(run_dir, "tiers", "*"))
        for tier_dir in tier_dirs:
            solu_scores = {}
            af2_scores = {}
            relax_scores = {}
            
            try:
                with open(os.path.join(tier_dir, "soluprot.json")) as f:
                    solu_scores = _norm_scores(json.load(f).get("scores", {}))
            except Exception: pass

            try:
                with open(os.path.join(tier_dir, "af2_scores.json")) as f:
                    af2_scores = _norm_scores(json.load(f).get("scores", {}))
            except Exception: pass

            try:
                with open(os.path.join(tier_dir, "relax_scores.json")) as f:
                    relax_scores = _norm_scores(json.load(f).get("scores", {}))
            except Exception: pass
            
            # 2. Get sequences from FASTA
            fasta_path = os.path.join(tier_dir, "designs_filtered.fasta")
            sequences_by_id = {}
            if os.path.exists(fasta_path):
                try:
                    with open(fasta_path) as f:
                        lines = f.readlines()
                        curr_id = None
                        curr_seq = []
                        for line in lines:
                            if line.startswith(">"):
                                if curr_id: sequences_by_id[curr_id] = "".join(curr_seq)
                                raw_id = line[1:].strip().split()[0]
                                curr_id = raw_id.replace(":", "_") if ":" in raw_id else raw_id
                                curr_seq = []
                            else:
                                curr_seq.append(line.strip())
                        if curr_id: sequences_by_id[curr_id] = "".join(curr_seq)
                except Exception: pass

            # 3. Merge by normalized sequence ID
            for seq_id, plddt in af2_scores.items():
                if seq_id in sequences_by_id:
                    records.append({
                        "target_id": target_id,
                        "seq_id": seq_id,
                        "sequence": sequences_by_id[seq_id],
                        "soluprot_score": float(solu_scores.get(seq_id, np.nan)),
                        "plddt_score": float(plddt),
                        "relax_score": float(relax_scores.get(seq_id, np.nan))
                    })
    
    df = pd.DataFrame(records)
    if not df.empty:
        df = df.drop_duplicates(subset=['target_id', 'sequence'])
    return df

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/04_cath_loto_validation.py <path_to_outputs_dir_or_csv>")
        sys.exit(1)
        
    data_path = sys.argv[1]
    
    if data_path.endswith('.csv'):
        df = load_cath_data(data_path)
    else:
        print(f"Extracting data from directory: {data_path}")
        df = extract_cath_batch_data(data_path)

    if df.empty or 'sequence' not in df.columns:
        print("Error: No valid data extracted (ensure 'sequence' column is present).")
        return

    print(f"Loaded {len(df)} records from {len(df['target_id'].unique())} targets.")
    embeddings = get_esm_embeddings(df)
    run_loto_validation(df, embeddings)

if __name__ == "__main__":
    main()
