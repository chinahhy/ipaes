#!/usr/bin/env python3
"""读取 /config/repo_path.json 中的 code 字段，做严格白名单校验后输出。
由 apply-repo-path.sh 调用。允许字符：A-Z a-z 0-9 _ -，长度 1..64。"""
import json, re, sys
from pathlib import Path

p = Path('/config/repo_path.json')
if not p.exists():
    sys.exit(0)
try:
    data = json.loads(p.read_text())
except Exception:
    sys.exit(0)
code = str(data.get('code', '')).strip()
if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', code):
    sys.exit(0)
print(code)
