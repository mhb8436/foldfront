import json
import time
import requests
import numpy as np
from pathlib import Path
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import OrdinalEncoder

# This logic will eventually be placed directly inside the MCP server 
# to run natively instead of over HTTP when called internally.
