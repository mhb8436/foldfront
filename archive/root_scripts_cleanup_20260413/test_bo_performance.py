import json
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import OrdinalEncoder

# 1. Load data from an existing run that has many scored sequences
# We will use proteinmpnn_samples from admin_no_ensemble as a proxy for "sequences" 
# and their MPNN score or SoluProt as the "Ground Truth" metric to predict, 
# just to test if the BO logic can find the top sequences efficiently.

try:
    with open("outputs/admin_no_ensemble/summary.json", "r") as f:
        data = json.load(f)
except FileNotFoundError:
    print("Error: admin_no_ensemble/summary.json not found.")
    exit(1)

# Extract sequences and their target metric (e.g., MPNN global_score or Soluprot)
sequences = []
scores = []

try:
    # Use MPNN global_score as our proxy metric for this test
    samples = data["tiers"][0]["proteinmpnn_samples"]
    for s in samples:
        sequences.append(s["sequence"])
        # We want to MAXIMIZE score in BO, but MPNN global_score is better when lower.
        # So we negate it to treat it as a maximization problem (like pLDDT)
        scores.append(-float(s["meta"]["global_score"]))
except (KeyError, IndexError):
    print("Could not parse sequences/scores from summary.")
    exit(1)

print(f"Loaded {len(sequences)} sequences for testing.")

if len(sequences) < 30:
    print("Not enough sequences to run a meaningful test.")
    exit(1)

# 2. Encode sequences
def encode_sequences(seq_list):
    chars = [list(s) for s in seq_list]
    encoder = OrdinalEncoder(handle_unknown='use_encoded_value', unknown_value=-1)
    return encoder.fit_transform(chars)

X = encode_sequences(sequences)
y = np.array(scores)

# 3. Simulate BO Setup
np.random.seed(42)
total_idx = np.arange(len(X))
np.random.shuffle(total_idx)

# Use first 20 as initial "evaluated" samples
initial_samples = 20
train_idx = total_idx[:initial_samples]
pool_idx = total_idx[initial_samples:] # The rest 80 are "untested"

X_train = X[train_idx]
y_train = y[train_idx]
X_pool = X[pool_idx]
y_pool = y[pool_idx]

print(f"\n--- Baseline Stats ---")
print(f"Total dataset true max score: {np.max(y):.4f}")
print(f"Average score in untested pool: {np.mean(y_pool):.4f}")
print(f"Max score found in initial random 20: {np.max(y_train):.4f}")

# 4. Train RandomForest BO Model
model = RandomForestRegressor(n_estimators=100, random_state=42)
model.fit(X_train, y_train)

# Predict on untested pool
preds = np.array([tree.predict(X_pool) for tree in model.estimators_])
mean_pred = preds.mean(axis=0)
std_pred = preds.std(axis=0)

# Acquisition: UCB (mean + 1.5 * std)
acq_scores = mean_pred + 1.5 * std_pred

# 5. Evaluate BO Performance
# Select top 5 sequences according to the model
top_5_pred_idx = np.argsort(acq_scores)[::-1][:5]
top_5_actual_scores = y_pool[top_5_pred_idx]

print(f"\n--- BO Evaluation (Round 1) ---")
print(f"Model selected 5 sequences from the pool.")
print(f"Actual scores of selected 5: {[round(s, 4) for s in top_5_actual_scores]}")
print(f"Average actual score of selected 5: {np.mean(top_5_actual_scores):.4f}")

# Compare to Random Selection
random_5_idx = np.random.choice(len(y_pool), 5, replace=False)
random_5_actual_scores = y_pool[random_5_idx]
print(f"Average actual score if we picked 5 randomly: {np.mean(random_5_actual_scores):.4f}")

if np.mean(top_5_actual_scores) > np.mean(random_5_actual_scores):
    print("\n✅ SUCCESS: BO model successfully identified sequences better than random chance!")
    if np.max(top_5_actual_scores) >= np.max(y) * 0.95: # Close to absolute max
        print("✅ It even found a near-optimal sequence in the very first round!")
else:
    print("\n❌ FAILURE: BO model performed worse than or equal to random chance. Encoding/Model needs work.")

