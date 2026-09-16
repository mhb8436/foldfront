import mlflow
import os
import time

mlflow.set_tracking_uri("https://mlflow.k-biofoundrycopilot.duckdns.org/")
experiment_name = "Agent_Operations"
mlflow.set_experiment(experiment_name)

with mlflow.start_run(run_name="Initial_Agent_Setup"):
    mlflow.log_param("agent_type", "Gemini_CLI")
    mlflow.log_param("status", "Integration_Active")
    mlflow.log_metric("setup_step", 1)
    
    # Create a dummy artifact
    with open("agent_config.txt", "w") as f:
        f.write("Agent is configured to log to this MLflow instance.")
    mlflow.log_artifact("agent_config.txt")
    
print(f"Successfully logged to MLflow experiment: {experiment_name}")
