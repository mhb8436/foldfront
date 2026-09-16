import urllib.request
import random
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

CATH_DOMAIN_LIST_URL = "https://download.cathdb.info/cath/releases/latest-release/cath-classification-data/cath-domain-list.txt"

# Destination directories (Train: 80%, Val: 10%, Test: 10%)
TRAIN_DIR = Path("/opt/protein_pipeline/cath_train")
VAL_DIR = Path("/opt/protein_pipeline/cath_val")
TEST_DIR = Path("/opt/protein_pipeline/cath_test")

def main():
    print(f"Downloading CATH domain list to map exact Topologies from {CATH_DOMAIN_LIST_URL}...")
    req = urllib.request.Request(CATH_DOMAIN_LIST_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        lines = response.read().decode('utf-8').splitlines()
    
    # 1. Topology(C.A.T) 기준으로 그룹화하여 각각의 도메인들을 리스트화
    topology_groups = {}
    for line in lines:
        if line.startswith('#') or not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 4:
            domain_id = parts[0]
            cat_id = f"{parts[1]}.{parts[2]}.{parts[3]}" # C.A.T (Topology level)
            if cat_id not in topology_groups:
                topology_groups[cat_id] = []
            topology_groups[cat_id].append(domain_id)
            
    topologies = list(topology_groups.keys())
    print(f"Total unique Topologies found: {len(topologies)}")
    
    # 2. Topology를 80:10:10 분할 (Seed 고정)
    random.seed(42)
    random.shuffle(topologies)
    
    train_split = int(len(topologies) * 0.8)
    val_split = int(len(topologies) * 0.9)
    
    splits = {
        "Train": (topologies[:train_split], TRAIN_DIR),
        "Val": (topologies[train_split:val_split], VAL_DIR),
        "Test": (topologies[val_split:], TEST_DIR)
    }
    
    for _, path in splits.values():
        path.mkdir(parents=True, exist_ok=True)
    
    # 3. 각 그룹에서 대표 도메인을 1개만 샘플링하여 총 1,472개의 다운로드 작업(Target) 목록 생성
    download_tasks = []
    for split_name, (topo_list, dest_dir) in splits.items():
        for topo in topo_list:
            domain_id = random.choice(topology_groups[topo])
            download_tasks.append((domain_id, dest_dir))
            
    print(f"Total domains to download: {len(download_tasks)} (Train: {train_split}, Val: {val_split-train_split}, Test: {len(topologies)-val_split})")
    
    # 4. PDB 파일 병렬 다운로드
    def download_pdb(task):
        domain_id, dest_dir = task
        out_file = dest_dir / f"{domain_id}.pdb"
        if out_file.exists():
            return True
            
        pdb_id = domain_id[:4]
        urls = [
            f"https://www.cathdb.info/version/latest/api/rest/id/{domain_id}.pdb",
            f"https://files.rcsb.org/download/{pdb_id}.pdb"
        ]
        
        for url in urls:
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=10) as response:
                    content = response.read()
                    if len(content) > 100:
                        out_file.write_bytes(content)
                        return True
            except Exception:
                continue
        return False
        
    print("Starting downloads...")
    success_count = 0
    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = {executor.submit(download_pdb, task): task for task in download_tasks}
        for i, future in enumerate(as_completed(futures), 1):
            if future.result():
                success_count += 1
            if i % 100 == 0:
                print(f"Progress: {i}/{len(download_tasks)} downloaded... (Success: {success_count})")
                
    print(f"Done! Successfully downloaded and split {success_count} unique Topology PDBs.")

if __name__ == "__main__":
    main()
