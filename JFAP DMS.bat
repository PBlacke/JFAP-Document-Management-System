@echo off
title JFAP Document Management System
echo Starting JFAP Document Management System...
call venv\Scripts\activate
echo Server is running at http://127.0.0.1:5000
start /B python app.py
timeout /t 2 /nobreak >nul
start http://127.0.0.1:5000
echo Browser opened. Server is running. 
echo To stop the server, press Ctrl+C in this window.