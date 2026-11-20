"""
Entry point for Streamlit Community Cloud. It runs the same app as ui/app.py, so the
two can never drift apart (this file used to be a full copy of app.py).
"""
import os
import runpy

runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py"), run_name="__main__")
