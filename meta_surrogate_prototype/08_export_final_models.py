import pandas as pd
import numpy as np
from sklearn.neural_network import MLPRegressor
import pickle
import os

def export_global_models():
    input_csv = "meta_surrogate_prototype/extracted_data_full.csv"
    input_npy = "meta_surrogate_prototype/embeddings.npy"
    model_dir = "pipeline-mcp/models"
    
    if not os.path.exists(input_csv):
        print("Historical data not found. Please run extraction first.")
        return

    df = pd.read_csv(input_csv)
    embeddings = np.load(input_npy)
    
    # 1. Train GLOBAL SOLUPROT MODEL
    print(f"Training Global SoluProt Model on {len(df)} samples...")
    mlp_solu = MLPRegressor(hidden_layer_sizes=(256, 128), max_iter=500, random_state=42)
    mlp_solu.fit(embeddings, df['soluprot'].values)
    
    with open(f"{model_dir}/global_soluprot_v1.pkl", 'wb') as f:
        pickle.dump(mlp_solu, f)
    
    # 2. Train GLOBAL PLDDT MODEL
    df_plddt = df.dropna(subset=['plddt'])
    embeddings_plddt = embeddings[df['plddt'].notna()]
    
    print(f"Training Global pLDDT Model on {len(df_plddt)} samples...")
    mlp_plddt = MLPRegressor(hidden_layer_sizes=(128, 64), max_iter=500, random_state=42)
    mlp_plddt.fit(embeddings_plddt, df_plddt['plddt'].values)
    
    with open(f"{model_dir}/global_plddt_v1.pkl", 'wb') as f:
        pickle.dump(mlp_plddt, f)
        
    print("\n✅ Successfully exported Global Surrogate Models to pipeline-mcp/models/")

if __name__ == "__main__":
    export_global_models()