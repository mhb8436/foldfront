import mlflow
import os
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv('pipeline-mcp/.env')

def check_auth():
    mlflow.set_tracking_uri("https://mlflow.k-biofoundrycopilot.duckdns.org/")
    
    try:
        from mlflow.tracking import MlflowClient
        client = MlflowClient()
        experiments = client.search_experiments()
        print(f"✅ Successfully authenticated! Found {len(experiments)} experiments.")
        for exp in experiments:
            print(f" - [{exp.experiment_id}] {exp.name}")
            
        # 간단한 테스트 실행 기록 하나 남기기
        mlflow.set_experiment("Agent_Operations")
        with mlflow.start_run(run_name="Agent_Auth_Test"):
            mlflow.log_param("test_status", "success")
            mlflow.log_param("agent", "Gemini")
        print("\n✅ Successfully created a test run in 'Agent_Operations' experiment!")
        return True
    except Exception as e:
        print(f"❌ Authentication failed or other error:\n{e}")
        return False

if __name__ == "__main__":
    check_auth()
