"""打印 /api/config 响应里 admin 块:验证 password 没回显。"""
import json

with open(r"D:\code\AutoRouter\tmp_admin.json") as f:
    d = json.load(f)

print("admin block:")
for k, v in d["connection"]["admin"].items():
    print(f"  {k}: {v!r}")
print(f"password leaked into response: {'password' in d['connection']['admin']}")
