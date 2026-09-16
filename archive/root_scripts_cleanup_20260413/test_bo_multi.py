import json
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import OrdinalEncoder

# 1. Load data
try:
    with open("outputs/admin_no_ensemble/summary.json", "r") as f:
        data = json.load(f)
except FileNotFoundError:
    print("Error: admin_no_ensemble/summary.json not found.")
    exit(1)

sequences = []
solu_scores = []
relax_scores = []

try:
    samples = data["tiers"][0]["proteinmpnn_samples"]
    soluprot_dict = data["tiers"][0].get("soluprot_scores", {})
    
    # We will fake relax scores for this test using a combination of length and sequence features
    # since admin_no_ensemble doesn't have actual relax scores. 
    # This is just to test the MULTI-OBJECTIVE capability of the model architecture.
    for s in samples:
        sid = s["id"]
        seq = s["sequence"]
        solu = soluprot_dict.get(sid, 0.0)
        
        # Fake a relax score: let's pretend lower is better, but we negate to maximize
        # Let's say relax score correlates slightly with hydrophobic content (just for variance)
        hydrophobic = sum(1 for c in seq if c in "VILMFWC") / len(seq)
        fake_relax = -(hydrophobic * 10 + np.random.normal(0, 0.5))
        
        sequences.append(seq)
        solu_scores.append(solu)
        relax_scores.append(fake_relax)
        
except (KeyError, IndexError):
    print("Could not parse sequences/scores from summary.")
    exit(1)

if len(sequences) < 30:
    print("Not enough sequences to run a meaningful test.")
    exit(1)

# Normalize scores to 0-1 range to weigh them equally in acquisition
def min_max_norm(arr):
    arr = np.array(arr)
    return (arr - arr.min()) / (arr.max() - arr.min() + 1e-9)

solu_norm = min_max_norm(solu_scores)
relax_norm = min_max_norm(relax_scores)

# Combine into a Multi-Objective "Ground Truth" Score (50% Soluprot, 50% Relax)
combined_scores = (solu_norm * 0.5) + (relax_norm * 0.5)

# 2. Encode sequences
def encode_sequences(seq_list):
    chars = [list(s) for s in seq_list]
    encoder = OrdinalEncoder(handle_unknown='use_encoded_value', unknown_value=-1)
    return encoder.fit_transform(chars)

X = encode_sequences(sequences)
y_multi = np.column_stack((solu_norm, relax_norm)) # Multi-target array
y_combined = combined_scores # Just for evaluation baseline

# 3. Simulate BO Setup
np.random.seed(42)
total_idx = np.arange(len(X))
np.random.shuffle(total_idx)

initial_samples = 20
train_idx = total_idx[:initial_samples]
pool_idx = total_idx[initial_samples:]

X_train = X[train_idx]
y_train_multi = y_multi[train_idx]

X_pool = X[pool_idx]
y_pool_multi = y_multi[pool_idx]
y_pool_combined = y_combined[pool_idx]

# 4. Train Multi-Output RandomForest BO Model
# RandomForest in sklearn natively supports multi-output regression
model = RandomForestRegressor(n_estimators=100, random_state=42)
model.fit(X_train, y_train_multi)

# Predict on untested pool
# For multi-output, tree.predict returns shape (n_samples, n_outputs)
preds_multi = np.array([tree.predict(X_pool) for tree in model.estimators_]) 
# shape: (100_trees, 30_samples, 2_outputs)

mean_pred_multi = preds_multi.mean(axis=0) # shape: (30, 2)
std_pred_multi = preds_multi.std(axis=0)   # shape: (30, 2)

# Acquisition: UCB per objective
acq_scores_multi = mean_pred_multi + 1.5 * std_pred_multi

# Calculate final combined Acquisition Score (50% / 50%)
final_acq_scores = (acq_scores_multi[:, 0] * 0.5) + (acq_scores_multi[:, 1] * 0.5)

# 5. Evaluate Multi-Objective BO Performance
top_5_pred_idx = np.argsort(final_acq_scores)[::-1][:5]
top_5_actual_combined = y_pool_combined[top_5_pred_idx]

print(f"\n--- Multi-Objective BO Evaluation (Round 1) ---")
print(f"Total dataset true max combined score: {np.max(y_combined):.4f}")
print(f"Average combined score in pool: {np.mean(y_pool_combined):.4f}")

print(f"\nModel selected 5 sequences balancing SoluProt and Relax.")
print(f"Actual combined scores of selected 5: {[round(s, 4) for s in top_5_actual_combined]}")
print(f"Average actual combined score of selected 5: {np.mean(top_5_actual_combined):.4f}")

random_5_idx = np.random.choice(len(y_pool_combined), 5, replace=False)
random_5_actual_combined = y_pool_combined[random_5_idx]
print(f"Average actual combined score if we picked 5 randomly: {np.mean(random_5_actual_combined):.4f}")

if np.mean(top_5_actual_combined) > np.mean(random_5_actual_combined):
    print("\n✅ SUCCESS: Multi-Objective BO successfully found a better Pareto balance than random chance!")
else:
    print("\n❌ FAILURE: Multi-Objective BO performed worse than random chance.")

