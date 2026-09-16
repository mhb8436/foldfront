import json
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import OrdinalEncoder

try:
    with open("outputs/admin_no_ensemble/summary.json", "r") as f:
        data = json.load(f)
except FileNotFoundError:
    print("Error: admin_no_ensemble/summary.json not found.")
    exit(1)

sequences = []
scores = []

try:
    samples = data["tiers"][0]["proteinmpnn_samples"]
    soluprot_dict = data["tiers"][0].get("soluprot_scores", {})
    
    for s in samples:
        sid = s["id"]
        seq = s["sequence"]
        solu = soluprot_dict.get(sid, 0.0)
        
        sequences.append(seq)
        scores.append(solu)
        
except (KeyError, IndexError):
    print("Could not parse sequences/scores from summary.")
    exit(1)

X = np.array([list(s) for s in sequences])
encoder = OrdinalEncoder(handle_unknown='use_encoded_value', unknown_value=-1)
X = encoder.fit_transform(X)
y = np.array(scores)

np.random.seed(42)
total_idx = np.arange(len(X))
np.random.shuffle(total_idx)

initial_samples = 20
train_idx = total_idx[:initial_samples]
pool_idx = total_idx[initial_samples:]

X_train, y_train = X[train_idx], y[train_idx]
X_pool, y_pool = X[pool_idx], y[pool_idx]

model = RandomForestRegressor(n_estimators=100, random_state=42)
model.fit(X_train, y_train)

preds = np.array([tree.predict(X_pool) for tree in model.estimators_])
mean_pred = preds.mean(axis=0)
std_pred = preds.std(axis=0)
acq_scores = mean_pred + 1.5 * std_pred

top_5_pred_idx = np.argsort(acq_scores)[::-1][:5]
top_5_actual_scores = y_pool[top_5_pred_idx]

random_5_idx = np.random.choice(len(y_pool), 5, replace=False)
random_5_actual_scores = y_pool[random_5_idx]

print(f"--- SoluProt Single Objective BO ---")
print(f"Average actual score in pool: {np.mean(y_pool):.4f}")
print(f"Average actual score of BO selected 5: {np.mean(top_5_actual_scores):.4f}")
print(f"Average actual score of Random 5: {np.mean(random_5_actual_scores):.4f}")

if np.mean(top_5_actual_scores) > np.mean(random_5_actual_scores):
    print("✅ SUCCESS")
else:
    print("❌ FAILURE")
