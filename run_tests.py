"""Runs tests/smoke_test.py in a throw-away folder, so your real config.json / data.json are never touched."""
import json, os, shutil, subprocess, sys, tempfile
root = os.path.dirname(os.path.abspath(__file__))
with tempfile.TemporaryDirectory() as tmp:
    shutil.copy(os.path.join(root, "forward_bot_multi.py"), tmp)
    shutil.copy(os.path.join(root, "tests", "smoke_test.py"), tmp)
    shutil.copytree(os.path.join(root, "tests", "stubs"), os.path.join(tmp, "stubs"))
    with open(os.path.join(tmp, "config.json"), "w") as f:
        json.dump({"API_ID": 1, "API_HASH": "x", "BOT_TOKEN": "t", "OWNER_ID": 1}, f)
    sys.exit(subprocess.call([sys.executable, "smoke_test.py"], cwd=tmp))
