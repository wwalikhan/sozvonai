@echo off
cd /d "%~dp0"
title SozvonAI - backend

start "SozvonAI backend" cmd /k .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

timeout /t 4 /nobreak >nul
start "" http://127.0.0.1:8000/
