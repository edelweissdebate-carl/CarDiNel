"""Credential-free live check of a documented public Swarm orbit product."""
from io import BytesIO
import hashlib
import zipfile
import requests

for letter in "ABC":
    filename = f"SW_OPER_SP3{letter}COM_2__20260901T235942_20260902T235942_0203.ZIP"
    path = f"swarm/Level2daily/Latest_baselines/POD/RD/Sat_{letter}/{filename}"
    response = requests.get("https://swarm-diss.eo.esa.int/",
                            params={"do": "download", "file": path}, timeout=90)
    response.raise_for_status()
    print(letter, response.status_code, len(response.content), hashlib.sha256(response.content).hexdigest())
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        print("members", archive.namelist())
        for name in archive.namelist():
            text = archive.read(name).decode("utf-8")
            if name.upper().endswith(".HDR"):
                print(text[:6500])
            else:
                print("\n".join(text.splitlines()[:28]))
                print("last", "\n".join(text.splitlines()[-4:]))
