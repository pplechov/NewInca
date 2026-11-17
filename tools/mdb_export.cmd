@echo off
rem Export an INCA "XPP Standards.mdb" (Access 97 / Jet 3) into xpp_standards.csv.
rem
rem   mdb_export.cmd "D:\...\XPP Standards.mdb" "..\calibration\profiles\my-lab\xpp_standards.csv"
rem
rem Only the 32-bit Jet 4.0 provider can read that file format, so the work is
rem done by 32-bit PowerShell (mdb_export.ps1).
setlocal
if "%~2"=="" (
    echo usage: mdb_export.cmd "^<XPP Standards.mdb^>" "^<output.csv^>"
    exit /b 1
)
"%SystemRoot%\SysWOW64\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0mdb_export.ps1" -Mdb %1 -Out %2
exit /b %ERRORLEVEL%
