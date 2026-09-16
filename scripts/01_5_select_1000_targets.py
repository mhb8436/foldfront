import urllib.request
import random
import shutil
from pathlib import Path

CATH_DOMAIN_LIST_URL = "https://download.cathdb.info/cath/releases/latest-release/cath-classification-data/cath-domain-list.txt"
SOURCE_DIR = Path("/opt/protein_pipeline/cath_targets")
TARGET_DIR = Path("/opt/protein_pipeline/cath_targets_1000")
TARGET_COUNT = 1000

def main():
    print("Downloading CATH domain list to determine Topologies...")
    req = urllib.request.Request(CATH_DOMAIN_LIST_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        lines = response.read().decode('utf-8').splitlines()
    
    # Group by C.A.T (Class, Architecture, Topology)
    topology_groups = {}
    for line in lines:
        if line.startswith('#') or not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 4:
            domain_id = parts[0]
            cat_id = f"{parts[1]}.{parts[2]}.{parts[3]}" # Topology level
            if cat_id not in topology_groups:
                topology_groups[cat_id] = []
            topology_groups[cat_id].append(domain_id)
            
    print(f"Total unique Topologies found: {len(topology_groups)}")
    
    # Select diverse domains
    selected_domains = []
    keys = list(topology_groups.keys())
    random.shuffle(keys)
    
    for key in keys:
        if len(selected_domains) >= TARGET_COUNT:
            break
        # Pick one random domain from this specific Topology
        candidate = random.choice(topology_groups[key])
        selected_domains.append(candidate)
        
    print(f"Selected {len(selected_domains)} structurally diverse domains.")
    
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    
    copied_count = 0
    missing_count = 0
    for domain_id in selected_domains:
        src_file = SOURCE_DIR / f"{domain_id}.pdb"
        dst_file = TARGET_DIR / f"{domain_id}.pdb"
        
        if src_file.exists():
            shutil.copy2(src_file, dst_file)
            copied_count += 1
        else:
            missing_count += 1
            
    print(f"Done! Copied {copied_count} PDBs to {TARGET_DIR}")
    if missing_count > 0:
        print(f"Note: {missing_count} selected domains were not found in {SOURCE_DIR}.")
        print("Wait for the background download to finish, then run this script again to get exactly 1000.")

if __name__ == "__main__":
    main()
