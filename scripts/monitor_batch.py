import os, sys, json, time
from pathlib import Path

outputs_dir = Path("/opt/protein_pipeline/outputs")
csv_path = Path("/opt/protein_pipeline/batch_status.csv")

def get_target_status():
    targets = {}
    for d in outputs_dir.glob("cath_test_*"):
        if not d.is_dir(): continue
        run_id = d.name
        status_file = d / "status.json"
        
        # Check if fully completed
        if (d / "report.md").exists():
            targets[run_id] = ("DONE", "completed")
            continue
            
        # Read status.json
        if status_file.exists():
            try:
                data = json.loads(status_file.read_text())
                stage = data.get("stage", "unknown")
                state = data.get("state", "unknown")
                targets[run_id] = (stage, state)
            except:
                targets[run_id] = ("UNKNOWN", "error_reading")
        else:
            targets[run_id] = ("INIT", "waiting")
            
    # Check failed log
    failed_log = Path("/opt/protein_pipeline/batch_failed_test.csv")
    if failed_log.exists():
        lines = failed_log.read_text().splitlines()[1:]
        for line in lines:
            parts = line.split(",")
            if len(parts) >= 2:
                targets[parts[1]] = ("FAILED", parts[2][:30] if len(parts)>2 else "error")
                
    return targets

while True:
    targets = get_target_status()
    with open(csv_path, "w") as f:
        f.write(f"Updated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("Target_ID,Stage,State\n")
        for tid in sorted(targets.keys()):
            f.write(f"{tid},{targets[tid][0]},{targets[tid][1]}\n")
    time.sleep(10)
