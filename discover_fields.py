"""
discover_fields.py - list the custom fields on your Asana project so you can
populate ASANA_FIELD_MAP. Run locally; reads ASANA_PAT and ASANA_PROJECT_GID
from the environment.

    export ASANA_PAT=...           # your Asana personal access token
    export ASANA_PROJECT_GID=...   # the project the form should write to
    python discover_fields.py

For the four code values, create TEXT custom fields; for amount, a NUMBER field.
Text fields take the string directly, so you avoid per-option GID lookups.
"""

import os
import sys
import httpx

PAT = os.environ.get("ASANA_PAT", "")
PROJECT = os.environ.get("ASANA_PROJECT_GID", "")
if not (PAT and PROJECT):
    sys.exit("Set ASANA_PAT and ASANA_PROJECT_GID first.")

url = f"https://app.asana.com/api/1.0/projects/{PROJECT}/custom_field_settings"
r = httpx.get(
    url,
    headers={"Authorization": f"Bearer {PAT}"},
    params={"opt_fields": "custom_field.name,custom_field.resource_subtype,custom_field.gid"},
    timeout=20,
)
r.raise_for_status()
fields = r.json().get("data", [])

if not fields:
    print("No custom fields are attached to this project yet.")
    print("Add Department, Program, GL Code, Spend Category (text) and Amount (number) in the UI, then rerun.")
    sys.exit(0)

print(f"Custom fields on project {PROJECT}:\n")
for f in fields:
    cf = f.get("custom_field", {})
    print(f"  {cf.get('name','?'):28} type={cf.get('resource_subtype','?'):10} gid={cf.get('gid')}")

print("\nExample ASANA_FIELD_MAP value to paste into your environment:")
print('  {"department":"<gid>","program":"<gid>","gl_code":"<gid>","spend_category":"<gid>","amount":"<gid>"}')
