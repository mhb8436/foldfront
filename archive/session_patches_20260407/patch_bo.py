with open("bo_orchestrator.py", "r") as f:
    code = f.read()

# Change initial samples from 5 to 10
code = code.replace("min(5, len(seq_texts))", "min(10, len(seq_texts))")
code = code.replace("Random 5 samples", "Random 10 samples")

# Change samples per round from 3 to 5
code = code.replace("samples_per_round = 3", "samples_per_round = 5")

# Rename some AF2 references to ColabFold for clarity
code = code.replace("Evaluating {sid} with AF2...", "Evaluating {sid} with ColabFold...")
code = code.replace("Initial AF2 Evaluation", "Initial ColabFold Evaluation")
code = code.replace("Actual AF2 평가", "실제 ColabFold 평가")
code = code.replace("Total Sequences Evaluated with AF2:", "Total Sequences Evaluated with ColabFold:")

with open("bo_orchestrator.py", "w") as f:
    f.write(code)
