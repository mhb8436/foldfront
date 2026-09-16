# MLflow Tracking Skill

Help log agent activities, prompt execution traces, and model training metrics to the central MLflow server.

Use this skill when you need to record the outcome of an agent's task, track the parameters of a run, or store generated artifacts (like JSON summaries or FASTA files) into MLflow.

## Setup & Configuration

1. **Endpoint**: `http://127.0.0.1:18050` (Internal server access, bypasses SSO)
2. **Authentication**: Not required when running on the same server via localhost port.
3. **Library**: Ensure `mlflow` is installed (`pip install mlflow`).

## Guidelines for Logging Agent Operations

When logging an agent's execution or a specific AI task (like PDF Extraction or Agent Panel interactions):

1. **Set Experiment**: Use a distinct experiment name based on the task type.
   ```python
   mlflow.set_experiment("Agent_Operations")
   # OR
   mlflow.set_experiment("PDF_Extractions")
   ```
2. **Start Run**: Give the run a descriptive name.
   ```python
   with mlflow.start_run(run_name="PDF_Constraint_Extraction_20260422"):
   ```
3. **Log Parameters**: Log the LLM model used, the main prompt length, and specific tool parameters.
   ```python
   mlflow.log_param("agent_type", "Gemini_CLI")
   mlflow.log_param("task", "Extract Constraints")
   ```
4. **Log Artifacts**: Save the final response or extracted JSON to a file and log it.
   ```python
   mlflow.log_artifact("extracted_constraints.json")
   ```

## Guidelines for Logging Model Training

When modifying training scripts (like `03_train_mlp.py`):
1. Use `mlflow.set_experiment("Surrogate_Model_Training")`.
2. Log all hyperparameters (`hidden_layers`, `max_iter`, etc.) using `mlflow.log_params()`.
3. Log validation metrics (MSE, Accuracy) using `mlflow.log_metric()`.
4. Log the final model using `mlflow.sklearn.log_model()` or the appropriate flavor.
