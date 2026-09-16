import os
import requests
from dotenv import load_dotenv

load_dotenv('pipeline-mcp/.env')

def get_token():
    # Keycloak token endpoint
    token_url = "https://sso.k-biofoundrycopilot.duckdns.org/realms/kbf/protocol/openid-connect/token"
    
    username = os.getenv('MLFLOW_TRACKING_USERNAME')
    password = os.getenv('MLFLOW_TRACKING_PASSWORD')
    
    # We will try with a common client_id first. Often 'mlflow' or the frontend's client id.
    # Let's try 'mlflow' as it was present in the redirect URL from the error.
    data = {
        'grant_type': 'password',
        'client_id': 'mlflow',
        'username': username,
        'password': password
    }
    
    try:
        response = requests.post(token_url, data=data)
        if response.status_code == 200:
            print("✅ Successfully acquired token from Keycloak!")
            token = response.json().get('access_token')
            return token
        else:
            print(f"❌ Failed to get token. Status: {response.status_code}")
            print(f"Response: {response.text}")
            
            # If 'mlflow' client_id doesn't work, we might need a different client_id
            # that has 'Direct Access Grants' enabled.
            return None
    except Exception as e:
        print(f"Error connecting to Keycloak: {e}")
        return None

if __name__ == "__main__":
    get_token()
