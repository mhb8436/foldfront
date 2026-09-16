import json
import os
import pandas as pd
from pathlib import Path

def extract_data():
    output_dir = Path("outputs")
    dataset = []

    print(f"Scanning {output_dir} for summary.json files...")
    
    for summary_file in output_dir.rglob("summary.json"):
        try:
            with open(summary_file, 'r') as f:
                data = json.load(f)
        except Exception as e:
            print(f"Error reading {summary_file}: {e}")
            continue

        run_id = data.get("run_id", "unknown")
        
        for tier in data.get("tiers", []):
            samples = tier.get("proteinmpnn_samples", [])
            solu_dict = tier.get("soluprot_scores") or {}
            af2_dict = tier.get("af2") or {}
            
            for s in samples:
                sid = s.get("id")
                seq = s.get("sequence")
                if not sid or not seq: 
                    continue
                
                solu = solu_dict.get(sid)
                
                plddt = None
                if af2_dict and sid in af2_dict:
                    plddt = af2_dict[sid].get("best_plddt")
                
                # Extract relax score if available
                relax = None
                if af2_dict and sid in af2_dict:
                    # In admin_full_pipeline_260413, relax is stored under 'relax_scores' at the tier level 
                    # OR we can just use the fact that if it went through the pipeline, we can simulate it 
                    # accurately for the test. Let's look for relax_scores at the tier level.
                    relax_dict = tier.get("relax_scores", {})
                    if relax_dict and sid in relax_dict:
                         relax = relax_dict[sid]
                
                if solu is not None or plddt is not None or relax is not None:
                    dataset.append({
                        "run_id": run_id,
                        "id": sid,
                        "sequence": seq,
                        "soluprot": solu,
                        "plddt": plddt,
                        "relax": relax
                    })

    df = pd.DataFrame(dataset)
    print(f"Extracted {len(df)} total sequences with metrics.")
    
    if len(df) > 0:
        print("\nDataset completeness:")
        print(df.notnull().sum())
        
        output_csv = "meta_surrogate_prototype/extracted_data_full.csv"
        df.to_csv(output_csv, index=False)
        print(f"\nSaved extracted data to {output_csv}")
    else:
        print("No valid sequences found.")

if __name__ == "__main__":
    extract_data()
