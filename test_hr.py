import sys
import logging
logging.basicConfig(level=logging.INFO)
sys.path.append("/Users/gaurav/job-search-agent")
from app import _run_hr_email
print("Starting HR email check...")
_run_hr_email("a3a53f44")
print("Done.")
