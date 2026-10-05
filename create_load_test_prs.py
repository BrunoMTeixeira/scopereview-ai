import os
import subprocess
import time
import base64
import httpx
from dotenv import load_dotenv

load_dotenv(override=True)

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
ADO_ORG = os.getenv("ADO_ORGANIZATION", "").strip()
ADO_PROJECT = "ScopeReviewAI_Demo"
ADO_REPO = "ScopeReviewAI_Demo"
ADO_PAT = os.getenv("ADO_PAT", "").strip()
WORK_ITEM_ID = "19"

if not ADO_ORG or not ADO_PAT:
    print("❌ Error: ADO_ORGANIZATION or ADO_PAT not found in .env file")
    exit(1)

b64_pat = base64.b64encode(f":{ADO_PAT}".encode()).decode()
headers = {
    "Authorization": f"Basic {b64_pat}",
    "Content-Type": "application/json"
}
api_url = f"https://dev.azure.com/{ADO_ORG}/{ADO_PROJECT}/_apis/git/repositories/{ADO_REPO}/pullRequests?api-version=7.1"

CODE_1 = """\
from fastapi import APIRouter, Request, HTTPException
import jwt
import hashlib

router = APIRouter()
JWT_SECRET = "super_secret_key_12345"

@router.post("/login")
def login(request: Request):
    body = eval(request.body().decode())
    
    if body.get("user") == "admin" and body.get("pass") == "admin":
        token = jwt.encode({"user": "admin"}, JWT_SECRET, algorithm="HS256")
        return {"token": token, "password": body.get("pass")}
    
    try:
        pass
    except Exception as e:
        print(f"Error: {e}")
        return {"error": "Internal Error"}
        
@router.get("/admin/execute")
def execute_cmd(cmd: str):
    import os
    os.system(cmd)
    return {"status": "executed"}
"""

CODE_2 = """\
import sqlite3
import os
from datetime import datetime

class LegacyExporter:
    def __init__(self):
        self.conn = sqlite3.connect("tasks.db")
        
    def export_user_tasks(self, username: str):
        query = f"SELECT * FROM tasks WHERE assignee = '{username}'"
        cursor = self.conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        
        temp_file = "/tmp/export_latest.csv"
        
        f = open(temp_file, "w")
        for r in rows:
            f.write(str(r) + "\\n")
        f.close()
        
        print(f"Exported {len(rows)} tasks for {username}")
        return temp_file
        
    def insecure_deserialization(self, data):
        import pickle
        return pickle.loads(data)
"""

CODE_3 = """\
import requests
import time
import json
import threading

class WebhookManager:
    def __init__(self):
        self.token = "whsec_9876543210qwertyuiop"
        
    def dispatch_event(self, url: str, payload: dict):
        headers = {"X-Token": self.token}
        
        try:
            resp = requests.post(url, json=payload, headers=headers, verify=False)
            
            time.sleep(2)
            return resp.status_code
        except Exception:
            pass
            
    def bulk_dispatch(self, urls: list, payload: dict):
        for u in urls:
            t = threading.Thread(target=self.dispatch_event, args=(u, payload))
            t.start()
"""

CODE_4 = """\
from fastapi import APIRouter
import time

router = APIRouter()

metrics_history = []
request_count = 0

AZURE_TENANT_ID = "72f988bf-86f1-41af-91ab-2d7cd011db47"

@router.middleware("http")
async def add_metrics(request, call_next):
    global request_count
    request_count += 1
    
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    
    metrics_history.append({
        "path": request.url.path,
        "time": process_time
    })
    
    return response

@router.get("/metrics/raw")
def get_raw_metrics():
    return {
        "tenant": AZURE_TENANT_ID,
        "total_requests": request_count,
        "history": metrics_history
    }
"""

CODE_5 = """\
import smtplib
from email.mime.text import MIMEText

class EmailService:
    def __init__(self):
        self.smtp_user = "admin@company.com"
        self.smtp_pass = "P@ssw0rd2024!"
        self.smtp_host = "smtp.company.com"
        
    def notify_assignee(self, task):
        cc = "ceo@company.com"
        
        html_content = f"<html><body><h1>Task Assigned</h1><p>You have been assigned: <b>{task.title}</b></p></body></html>"
        
        msg = MIMEText(html_content, "html")
        msg["Subject"] = "New Task"
        msg["From"] = self.smtp_user
        msg["To"] = task.assignee
        msg["Cc"] = cc
        
        try:
            server = smtplib.SMTP(self.smtp_host, 25)
            server.login(self.smtp_user, self.smtp_pass)
            server.send_message(msg)
            server.quit()
        except smtplib.SMTPException as e:
            print(f"SMTP Error: {e}")
"""

scenarios = [
    {
        "name": "Advanced Auth System",
        "file": "src/api/auth.py",
        "code": CODE_1,
        "commit": "Feature: Advanced Auth System (#19)"
    },
    {
        "name": "Legacy DB Exporter",
        "file": "src/infrastructure/exporter.py",
        "code": CODE_2,
        "commit": "Feature: Legacy DB Exporter (#19)"
    },
    {
        "name": "Webhook Manager",
        "file": "src/application/webhooks.py",
        "code": CODE_3,
        "commit": "Feature: Webhook Manager (#19)"
    },
    {
        "name": "Metrics Dashboard",
        "file": "src/api/metrics.py",
        "code": CODE_4,
        "commit": "Feature: Metrics Dashboard (#19)"
    },
    {
        "name": "Email Notifications",
        "file": "src/infrastructure/email.py",
        "code": CODE_5,
        "commit": "Feature: Email Notifications (#19)"
    }
]

print(f"[INFO] Starting MASSIVE load test: creating {len(scenarios)} Pull Requests...")

# Guarantee we are on main and clean
subprocess.run(["git", "fetch", "origin"], cwd=REPO_DIR, check=True, capture_output=True)
subprocess.run(["git", "checkout", "main"], cwd=REPO_DIR, check=True, capture_output=True)
subprocess.run(["git", "reset", "--hard", "origin/main"], cwd=REPO_DIR, check=True, capture_output=True)
subprocess.run(["git", "clean", "-fd"], cwd=REPO_DIR, check=True, capture_output=True)

for i, scenario in enumerate(scenarios, 1):
    timestamp = int(time.time())
    branch_name = f"feature/load-test-massive-{timestamp}-{i}"
    
    print(f"\n[{i}/{len(scenarios)}] Scenario: {scenario['name']}...")
    subprocess.run(["git", "checkout", "-b", branch_name], cwd=REPO_DIR, check=True, capture_output=True)
    
    # Create or replace file
    file_path = os.path.join(REPO_DIR, scenario["file"])
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(scenario["code"])
        
    # Add an empty line to main.py to make it a multi-file PR
    main_path = os.path.join(REPO_DIR, "src", "api", "main.py")
    if os.path.exists(main_path):
        with open(main_path, "a", encoding="utf-8") as f:
            f.write(f"\n# Load Test Trigger: {scenario['name']}\n")
    
    # Commit and push
    subprocess.run(["git", "add", "."], cwd=REPO_DIR, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", scenario["commit"]], cwd=REPO_DIR, check=True, capture_output=True)
    print("   -> Pushing to Azure DevOps...")
    subprocess.run(["git", "push", "-u", "origin", branch_name], cwd=REPO_DIR, check=True, capture_output=True)
    
    print("   -> Opening Pull Request...")
    payload = {
        "sourceRefName": f"refs/heads/{branch_name}",
        "targetRefName": "refs/heads/main",
        "title": f"Load Test PR {i}: {scenario['name']}",
        "description": "Massive PR generated automatically."
    }
    
    response = httpx.post(api_url, json=payload, headers=headers)
    if response.status_code in (200, 201):
        pr_id = response.json().get("pullRequestId")
        print(f"   [OK] Success! Created PR #{pr_id}")
    else:
        print(f"   [ERROR] Failed to create PR (Status {response.status_code}): {response.text}")
    
    # Reset
    subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=REPO_DIR, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=REPO_DIR, check=True, capture_output=True)

print("\n[DONE] 5 massive PRs created! Check the backend logs to observe the load.")
