import requests
import json
import time
import numpy as np
from pathlib import Path
import os
import shutil
import base64

# ==========================================
# BO Surrogate Orchestrator for Protein Design
# ==========================================
# Requirement: pip install scikit-learn numpy requests
# Run: python3 bo_orchestrator.py

try:
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.preprocessing import OrdinalEncoder
except ImportError:
    print("Please install scikit-learn and numpy: pip install scikit-learn numpy")
    exit(1)

SERVER_URL = "http://127.0.0.1:18080/tools/call"
TARGET_PDB = "1LVM_no_neg.pdb"  # <- 타겟 PDB 경로를 여기에 입력하세요.

def call_tool(name, args):
    """MCP HTTP 서버에 툴 실행을 요청합니다."""
    resp = requests.post(SERVER_URL, json={"name": name, "arguments": args})
    resp.raise_for_status()
    data = resp.json()
    if "error" in data and data["error"]:
        raise RuntimeError(f"Tool Error: {data['error']}")
    return data["result"]

def wait_for_run(run_id, poll_interval=5):
    """파이프라인이 끝날 때까지 폴링하며 대기합니다."""
    print(f"Waiting for run {run_id} to complete...", end="", flush=True)
    while True:
        res = call_tool("pipeline.status", {"run_id": run_id})
        state = res.get("status", {}).get("state")
        if state != "running":
            print(f" Done! (State: {state})")
            return res
        print(".", end="", flush=True)
        time.sleep(poll_interval)

def read_fasta(fasta_path):
    """FASTA 파일을 읽어 ID와 시퀀스를 딕셔너리로 반환합니다."""
    seqs = {}
    curr_id = None
    with open(fasta_path, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith(">"):
                curr_id = line[1:]
                seqs[curr_id] = ""
            elif curr_id:
                seqs[curr_id] += line
    return seqs

def encode_sequences(seq_list):
    """단백질 서열을 머신러닝이 이해할 수 있는 숫자 배열로 변환합니다."""
    chars = [list(s) for s in seq_list]
    encoder = OrdinalEncoder(handle_unknown='use_encoded_value', unknown_value=-1)
    return encoder.fit_transform(chars), encoder

def calculate_acquisition(mean_pred, std_pred, kappa=2.0):
    """UCB (Upper Confidence Bound) 기반 획득 함수 (탐색 + 활용)"""
    return mean_pred + kappa * std_pred

def main():
    if not os.path.exists(TARGET_PDB):
        print(f"Error: {TARGET_PDB} not found. Please provide a valid target PDB.")
        return

    with open(TARGET_PDB, "r") as f:
        pdb_content = f.read()

    # ==========================================
    # 1. MPNN으로 서열 대량 생성 (Round 0)
    # ==========================================
    print("\n[Phase 1] Generating large sequence pool via ProteinMPNN...")
    pool_run_id = "bo_init_pool_001"
    
    # AF2를 제외하고 SoluProt까지만 돌려서 대량의 후보군을 확보합니다.
    # num_seq_per_tier=100 으로 설정하면 많은 서열이 나옵니다.
    call_tool("pipeline.run", {
        "run_id": pool_run_id,
        "target_pdb": pdb_content,
        "target_fasta": "",
        "stop_after": "soluprot",
        "num_seq_per_tier": 50,
        "conservation_tiers": [0.3], # 한 가지 티어만 생성 (빠른 테스트용)
        "rfd3_mode": "local_diversify" # RFD3 뼈대 생성 포함
    })
    
    wait_for_run(pool_run_id)
    
    # 생성된 FASTA 읽기
    fasta_file = Path(f"outputs/{pool_run_id}/tiers/30/designs_filtered.fasta")
    if not fasta_file.exists():
        fasta_file = Path(f"outputs/{pool_run_id}/tiers/30/designs.fasta")
        if not fasta_file.exists():
            print("Error: Failed to generate sequence pool.")
            return

    pool_seqs = read_fasta(fasta_file)
    seq_ids = list(pool_seqs.keys())
    seq_texts = [pool_seqs[sid] for sid in seq_ids]
    
    if len(seq_texts) < 5:
        print("Not enough sequences generated. Aborting.")
        return
        
    print(f"Successfully generated {len(seq_texts)} sequences.")
    
    # 인코딩 (문자열 -> 숫자)
    X_encoded, encoder = encode_sequences(seq_texts)

    # ==========================================
    # 2. 초기 5개 샘플 랜덤 선택 및 AF2 실제 평가
    # ==========================================
    print("\n[Phase 2] Initial ColabFold Evaluation (Random 10 samples)")
    np.random.seed(42)
    evaluated_indices = list(np.random.choice(len(seq_texts), min(10, len(seq_texts)), replace=False))
    untested_indices = list(set(range(len(seq_texts))) - set(evaluated_indices))
    
    y_true = []
    
    def evaluate_af2(indices):
        """선택된 인덱스의 서열을 AF2로 검증하고 보정된 점수를 반환합니다."""
        scores = []
        for idx in indices:
            sid = seq_ids[idx]
            seq = seq_texts[idx]
            print(f"  Evaluating {sid} with ColabFold...")
            
            eval_run_id = f"bo_eval_{sid.replace(':', '_')}"
            fasta_payload = f">{sid}\n{seq}\n"
            
            # AF2 개별 예측 도구 호출
            call_tool("pipeline.af2_predict", {
                "run_id": eval_run_id,
                "target_fasta": fasta_payload,
                "target_pdb": pdb_content,
                "af2_provider": "colabfold"
            })
            
            wait_for_run(eval_run_id)
            
            # 예측 결과 읽기 (AF2 완료 시 result.json이나 summary에 저장됨)
            # 여기서는 AF2 output 폴더의 JSON을 읽습니다.
            try:
                import glob
                af2_out_dir = f"outputs/{eval_run_id}/af2"
                json_files = glob.glob(f"{af2_out_dir}/*/*.json")
                if json_files:
                    with open(json_files[0], "r") as f:
                        af2_res = json.load(f)
                        plddt = af2_res.get("best_plddt", 0)
                        # pLDDT를 점수로 사용
                        scores.append(plddt)
                        print(f"    -> pLDDT: {plddt:.2f}")
                else:
                    scores.append(0.0)
            except Exception as e:
                print(f"    -> Evaluation failed: {e}")
                scores.append(0.0)
                
        return np.array(scores)

    initial_scores = evaluate_af2(evaluated_indices)
    y_true.extend(initial_scores)

    # ==========================================
    # 3. 베이지안 최적화 루프 (Active Learning)
    # ==========================================
    rounds = 3
    samples_per_round = 5

    for r in range(rounds):
        if not untested_indices:
            break
            
        print(f"\n--- BO Round {r+1}/{rounds} ---")
        X_train = X_encoded[evaluated_indices]
        y_train = np.array(y_true)
        
        # 랜덤 포레스트 대리 모델 훈련
        model = RandomForestRegressor(n_estimators=100, random_state=42)
        model.fit(X_train, y_train)
        
        # 아직 테스트 안 한 서열들 예측
        X_untested = X_encoded[untested_indices]
        
        # 각 트리의 예측값을 모아 평균과 분산(불확실성) 계산
        preds = np.array([tree.predict(X_untested) for tree in model.estimators_])
        mean_pred = preds.mean(axis=0)
        std_pred = preds.std(axis=0)
        
        # 획득 함수 (UCB)
        acq_scores = calculate_acquisition(mean_pred, std_pred, kappa=1.5)
        
        # 획득 점수가 가장 높은 상위 N개 선택
        best_local_indices = np.argsort(acq_scores)[::-1][:samples_per_round]
        selected_global_indices = [untested_indices[i] for i in best_local_indices]
        
        print(f"Surrogate model selected {len(selected_global_indices)} new sequences to test.")
        print(f"Expected scores: {mean_pred[best_local_indices].round(1)}")
        
        # 실제 AF2 평가
        new_scores = evaluate_af2(selected_global_indices)
        
        # 상태 업데이트
        evaluated_indices.extend(selected_global_indices)
        y_true.extend(new_scores)
        
        # 역순으로 제거해야 인덱스가 꼬이지 않음
        for i in sorted(best_local_indices, reverse=True):
            untested_indices.pop(i)

    print("\n==========================================")
    print("         Optimization Complete!           ")
    print("==========================================")
    best_idx_in_eval = np.argmax(y_true)
    best_global_idx = evaluated_indices[best_idx_in_eval]
    
    print(f"Total Sequences Evaluated with ColabFold: {len(evaluated_indices)} / {len(seq_texts)}")
    print(f"Best Sequence ID: {seq_ids[best_global_idx]}")
    print(f"Best Sequence:    {seq_texts[best_global_idx]}")
    print(f"Best Score:       {y_true[best_idx_in_eval]:.2f} (pLDDT)")
    print("==========================================")

if __name__ == "__main__":
    main()
