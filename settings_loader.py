"""Простой config.env: KEY=value. Не выполняет shell-код и не печатает секреты."""
import os
from pathlib import Path

def load_config():
    root=Path(__file__).resolve().parent
    for path in (root/'config.local.env',root/'config.env'):
        if not path.exists():continue
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line=line.strip()
            if not line or line.startswith('#') or '=' not in line:continue
            key,value=line.split('=',1)
            key=key.strip();value=value.strip()
            if len(value)>=2 and value[0]==value[-1] and value[0] in ('"',"'"):value=value[1:-1]
            if key.isidentifier():os.environ.setdefault(key,value)
