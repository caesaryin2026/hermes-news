#!/usr/bin/env python3
"""Create GitHub repo, push hermes-news.html, enable Pages."""
import subprocess, json, os, sys

TOKEN_FILE = r'D:\0.hermes\github_token.txt'
REPO_DIR = r'D:\0.hermes'

if not os.path.exists(TOKEN_FILE):
    print("ERROR: Token file not found")
    sys.exit(1)

with open(TOKEN_FILE) as f:
    token = f.read().strip()

if not token or len(token) < 30:
    print(f"ERROR: Invalid token (len={len(token)})")
    sys.exit(1)

print(f"Token OK ({len(token)} chars)")

# 1. Test token
r = subprocess.run(['curl', '-s', '-H', f'Authorization: token {token}',
    'https://api.github.com/user'], capture_output=True, text=True)
user = json.loads(r.stdout)
username = user.get('login', '')
print(f"Auth: {username}")

# 2. Create repo (ignore if exists)
r = subprocess.run(['curl', '-s', '-H', f'Authorization: token {token}',
    '-H', 'Accept: application/vnd.github.v3+json',
    '-H', 'Content-Type: application/json',
    'https://api.github.com/user/repos',
    '-d', '{"name":"hermes-news","description":"Hermes Agent 中文资讯日报"}'],
    capture_output=True, text=True)
d = json.loads(r.stdout)
if 'full_name' in d:
    print(f"Repo: {d['full_name']}")
elif d.get('message') == 'name already exists on this account':
    print("Repo already exists, using it")
else:
    print(f"Repo issue: {d.get('message','?')}")
    sys.exit(1)

# 3. Init git and push
os.chdir(REPO_DIR)
subprocess.run(['git', 'init'], capture_output=True)
subprocess.run(['git', 'checkout', '-b', 'main'], capture_output=True)
subprocess.run(['git', 'add', 'hermes-news.html', 'refresh_news.py', 'deploy_github.py'], capture_output=True)
subprocess.run(['git', 'commit', '-m', 'Hermes Agent news page'], capture_output=True)

# Set remote with token embedded
remote = f'https://{username}:{token}@github.com/{username}/hermes-news.git'
subprocess.run(['git', 'remote', 'add', 'origin', remote], capture_output=True)
subprocess.run(['git', 'remote', 'set-url', 'origin', remote], capture_output=True)
p = subprocess.run(['git', 'push', '-u', 'origin', 'main', '--force'], capture_output=True, text=True)
if p.returncode == 0:
    print("Push: OK")
else:
    print(f"Push: {p.stderr[:200]}")

# 4. Enable Pages
pages_r = subprocess.run(['curl', '-s', '-X', 'POST',
    '-H', f'Authorization: token {token}',
    '-H', 'Accept: application/vnd.github.v3+json',
    '-H', 'Content-Type: application/json',
    f'https://api.github.com/repos/{username}/hermes-news/pages',
    '-d', '{"source":{"branch":"main","path":"/"}}'],
    capture_output=True, text=True)
try:
    pd = json.loads(pages_r.stdout)
    print(f"Pages: {pd.get('html_url', pd.get('message','check manually'))}")
except:
    print(f"Pages response: {pages_r.stdout[:200]}")

print(f"\nDone! Your page will be at:")
print(f"https://{username}.github.io/hermes-news/")
print("(may take 1-2 minutes to deploy)")
