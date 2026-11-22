#!/usr/bin/env python3
"""Render ${VAR} placeholders in a manifest from the environment; fails on any missing variable.

  SITE=a DB_STORAGE_CLASS=longhorn python3 tools/render.py k8s/site-primary/cluster.yaml | kubectl apply -f -
"""
import os
import re
import sys

text = open(sys.argv[1]).read()
missing = sorted({m for m in re.findall(r"\$\{([A-Z0-9_]+)\}", text) if m not in os.environ})
if missing:
    sys.exit(f"{sys.argv[1]}: set {', '.join(missing)}")
sys.stdout.write(re.sub(r"\$\{([A-Z0-9_]+)\}", lambda m: os.environ[m.group(1)], text))
