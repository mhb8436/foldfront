import urllib.request
import random
import shutil
from pathlib import Path

CATH_DOMAIN_LIST_URL = "https://download.cathdb.info/cath/releases/latest-release/cath-classification-data/cath-domain-list.txt"
SOURCE_DIR = Path("/opt/protein_pipeline/cath_targets")

# 분배될 폴더들
TRAIN_DIR = Path("/opt/protein_pipeline/cath_train")
VAL_DIR = Path("/opt/protein_pipeline/cath_val")
TEST_DIR = Path("/opt/protein_pipeline/cath_test")

def main():
    print("Downloading CATH domain list to map exact Topologies...")
    req = urllib.request.Request(CATH_DOMAIN_LIST_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        lines = response.read().decode('utf-8').splitlines()
    
    # 1. Topology(C.A.T) 기준으로 그룹화
    topology_groups = {}
    for line in lines:
        if line.startswith('#') or not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 4:
            domain_id = parts[0]
            cat_id = f"{parts[1]}.{parts[2]}.{parts[3]}"
            if cat_id not in topology_groups:
                topology_groups[cat_id] = []
            topology_groups[cat_id].append(domain_id)
            
    topologies = list(topology_groups.keys())
    print(f"Total unique Topologies found: {len(topologies)}")
    
    # 2. Topology 리스트 셔플 및 분할 (80:10:10)
    random.seed(42) # 재현성을 위한 시드 고정
    random.shuffle(topologies)
    
    train_split = int(len(topologies) * 0.8)
    val_split = int(len(topologies) * 0.9)
    
    train_topologies = topologies[:train_split]
    val_topologies = topologies[train_split:val_split]
    test_topologies = topologies[val_split:]
    
    print(f"Split sizes -> Train: {len(train_topologies)}, Val: {len(val_topologies)}, Test: {len(test_topologies)}")
    
    # 폴더 초기화
    for d in [TRAIN_DIR, VAL_DIR, TEST_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    
    # 3. 각 그룹에서 대표 도메인 1개씩 추출하여 복사
    def copy_representatives(topo_list, dest_dir):
        copied = 0
        for topo in topo_list:
            domain_id = random.choice(topology_groups[topo])
            src_file = SOURCE_DIR / f"{domain_id}.pdb"
            dst_file = dest_dir / f"{domain_id}.pdb"
            
            if src_file.exists():
                shutil.copy2(src_file, dst_file)
                copied += 1
        return copied

    print("\nCopying files...")
    train_copied = copy_representatives(train_topologies, TRAIN_DIR)
    val_copied = copy_representatives(val_topologies, VAL_DIR)
    test_copied = copy_representatives(test_topologies, TEST_DIR)
    
    print(f"✅ Success! Copied PDBs -> Train: {train_copied}, Val: {val_copied}, Test: {test_copied}")
    print("(Note: If numbers are lower than split sizes, the background download is still finishing. Run again later!)")

if __name__ == "__main__":
    main()
