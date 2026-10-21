import os
import sys
from pathlib import Path

# Add root directory to sys.path so server.py can be imported on Vercel
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from server import OpportunityLensRequestHandler

# Vercel Python runtime looks for `handler` subclassing BaseHTTPRequestHandler
class handler(OpportunityLensRequestHandler):
    pass
