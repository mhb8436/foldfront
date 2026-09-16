import os
import requests
from dotenv import load_dotenv

load_dotenv('pipeline-mcp/.env')

def get_token():
    token_url = "https://sso.k-biofoundrycopilot.duckdns.org/realms/kbf/protocol/openid-connect/token"
    
    username = os.getenv('MLFLOW_TRACKING_USERNAME')
    password = os.getenv('MLFLOW_TRACKING_PASSWORD')
    
    # Let's try the frontend client ID if we can find it, or generic ones
    client_ids = ['frontend', 'kbf-frontend', 'kbf-client', 'protein-pipeline']
    
    for client_id in client_ids:
        print(f"Trying client_id: {client_id}...")
        data = {
            'grant_type': 'password',
            'client_id': client_id,
            'username': username,
            'password': password
        }
        
        response = requests.post(token_url, data=data)
        if response.status_code == 200:
            print(f"✅ Successfully acquired token using client_id: {client_id}")
            return response.json().get('access_token')
        elif response.status_code != 401:
             print(f"  Got status {response.status_code}: {response.text}")
             
    print("❌ All generic client_ids failed.")
    return None

if __name__ == "__main__":
    get_token()
