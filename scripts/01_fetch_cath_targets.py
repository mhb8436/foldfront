import urllib.request
import random
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

CATH_DOMAIN_LIST_URL = "https://download.cathdb.info/cath/releases/latest-release/cath-classification-data/cath-domain-list.txt"
OUTPUT_DIR = Path("/opt/protein_pipeline/cath_targets")

def fetch_cath_domains():
    print(f"Downloading CATH domain list from {CATH_DOMAIN_LIST_URL}...")
    req = urllib.request.Request(CATH_DOMAIN_LIST_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        lines = response.read().decode('utf-8').splitlines()
    
    # Group by C.A.T.H (Homologous Superfamily) to get all 6,630 non-redundant folds
    superfamily_groups = {}
    for line in lines:
        if line.startswith('#') or not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 5:
            domain_id = parts[0]
            cath_id = f"{parts[1]}.{parts[2]}.{parts[3]}.{parts[4]}" # C.A.T.H
            if cath_id not in superfamily_groups:
                superfamily_groups[cath_id] = []
            superfamily_groups[cath_id].append(domain_id)
            
    print(f"Found {len(superfamily_groups)} unique CATH Homologous Superfamilies.")
    
    # Sample 1 representative from EVERY superfamily
    selected_domains = []
    for key, domains in superfamily_groups.items():
        selected_domains.append(random.choice(domains))
        
    return selected_domains

def download_pdb(domain_id):
    out_file = OUTPUT_DIR / f"{domain_id}.pdb"
    if out_file.exists():
        return domain_id, True
        
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
                    return domain_id, True
        except Exception:
            continue
            
    return domain_id, False

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    domains = fetch_cath_domains()
    print(f"Selected {len(domains)} representative domains. Starting downloads...")
    
    success_count = 0
    # Use 50 threads for fast downloading
    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = {executor.submit(download_pdb, d): d for d in domains}
        for i, future in enumerate(as_completed(futures), 1):
            domain_id, success = future.result()
            if success:
                success_count += 1
            if i % 100 == 0:
                print(f"Progress: {i}/{len(domains)} downloaded... (Success: {success_count})")
                
    print(f"Done! Successfully downloaded {success_count} PDBs to {OUTPUT_DIR}")

if __name__ == "__main__":
    main()
