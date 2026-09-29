$ErrorActionPreference = "Stop"

Get-Process -Name streamlit -ErrorAction SilentlyContinue | Stop-Process -Force
& .\.venv\Scripts\python.exe scripts\generate_lan_certificate.py
& .\.venv\Scripts\streamlit.exe run app.py --server.headless true --server.port 8510 --server.address 0.0.0.0 --server.sslCertFile certs\lan-cert.pem --server.sslKeyFile certs\lan-key.pem
