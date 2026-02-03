"""
Main entry point for Streamlit Cloud deployment.
Streamlit Cloud looks for either streamlit_app.py or app.py in the root directory.
"""

import sys
from pathlib import Path

# Add src directory to Python path
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

# Import and run the dashboard
from dashboard import *
