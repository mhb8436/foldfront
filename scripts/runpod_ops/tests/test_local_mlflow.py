import mlflow

def test_local_connection():
    # Attempt connecting via the internal port
    mlflow.set_tracking_uri("http://127.0.0.1:18050")
    
    experiment_name = "Agent_Operations"
    mlflow.set_experiment(experiment_name)
    
    try:
        with mlflow.start_run(run_name="Internal_Port_Test"):
            mlflow.log_param("auth_method", "internal_bypass")
            mlflow.log_param("status", "success")
            mlflow.log_metric("port_test", 1)
        print("✅ 성공적으로 내부 포트(127.0.0.1:18050)를 통해 MLflow에 기록되었습니다!")
        return True
    except Exception as e:
        print(f"❌ 내부 포트 연결 실패: {e}")
        return False

if __name__ == "__main__":
    test_local_connection()
