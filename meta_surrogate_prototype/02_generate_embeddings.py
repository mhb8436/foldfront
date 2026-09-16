import pandas as pd
import numpy as np
import torch
from transformers import AutoTokenizer, EsmModel
import os
from tqdm import tqdm

def generate_embeddings():
    input_csv = "meta_surrogate_prototype/extracted_data.csv"
    output_npy = "meta_surrogate_prototype/embeddings.npy"
    
    if not os.path.exists(input_csv):
        print(f"Error: {input_csv} not found.")
        return

    df = pd.read_csv(input_csv)
    sequences = df['sequence'].tolist()
    
    # We will use ESM-2 (8M parameters) for the prototype to ensure it runs quickly.
    # In production/paper, this can be swapped to esm2_t33_650M_UR50D or an ESM-3 API.
    model_name = "facebook/esm2_t6_8M_UR50D"
    print(f"Loading {model_name}...")
    
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = EsmModel.from_pretrained(model_name)
    
    # Move to GPU if available
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Using device: {device}")
    model.to(device)
    model.eval()

    embeddings = []
    
    print("Generating embeddings...")
    batch_size = 16
    
    with torch.no_grad():
        for i in tqdm(range(0, len(sequences), batch_size)):
            batch_seqs = sequences[i:i+batch_size]
            
            # Tokenize
            inputs = tokenizer(batch_seqs, return_tensors="pt", padding=True, truncation=True)
            inputs = {k: v.to(device) for k, v in inputs.items()}
            
            # Get model outputs
            outputs = model(**inputs)
            
            # Use the mean of the sequence representations (excluding [CLS] and [SEP])
            # For simplicity in prototype, we average the last hidden state across the sequence length
            # Note: A proper attention mask should be used for exact masking of padding
            attention_mask = inputs['attention_mask']
            last_hidden_states = outputs.last_hidden_state
            
            # Calculate masked mean
            # Expanded mask shape: [batch, seq_len, hidden_dim]
            expanded_mask = attention_mask.unsqueeze(-1).expand(last_hidden_states.size()).float()
            sum_embeddings = torch.sum(last_hidden_states * expanded_mask, dim=1)
            sum_mask = torch.clamp(expanded_mask.sum(dim=1), min=1e-9)
            mean_embeddings = sum_embeddings / sum_mask
            
            embeddings.append(mean_embeddings.cpu().numpy())

    # Concatenate all batches
    all_embeddings = np.vstack(embeddings)
    print(f"Generated embeddings shape: {all_embeddings.shape}")
    
    np.save(output_npy, all_embeddings)
    print(f"Saved embeddings to {output_npy}")

if __name__ == "__main__":
    generate_embeddings()