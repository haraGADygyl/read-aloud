@echo off
rem read-aloud for Command Prompt: runs bin\read-aloud, the bash script beside
rem this file, under Git Bash, which Claude Code needs on Windows anyway.
rem
rem   read-aloud.cmd install      first-time setup: venv and voice model
rem   read-aloud.cmd play         and every other command, as in --help
rem
rem Not plain "bash": in Command Prompt that is usually WSL's, whose Linux
rem side has its own Python and cannot play through winsound.
setlocal

set "GITBASH=%CLAUDE_CODE_GIT_BASH_PATH%"
if defined GITBASH if exist "%GITBASH%" goto found

rem Git for Windows puts git.exe in cmd\ (on PATH) or mingw64\bin\, and
rem bash.exe in bin\ under the same root.
for /f "delims=" %%G in ('where git 2^>nul') do (
  if exist "%%~dpG..\bin\bash.exe" (
    set "GITBASH=%%~dpG..\bin\bash.exe"
    goto found
  )
  if exist "%%~dpG..\..\bin\bash.exe" (
    set "GITBASH=%%~dpG..\..\bin\bash.exe"
    goto found
  )
)
for %%P in ("%ProgramFiles%\Git\bin\bash.exe" "%LOCALAPPDATA%\Programs\Git\bin\bash.exe") do (
  if exist "%%~P" (
    set "GITBASH=%%~P"
    goto found
  )
)

echo read-aloud: Git Bash not found. Install Git for Windows from https://git-scm.com,
echo or set CLAUDE_CODE_GIT_BASH_PATH to its bash.exe.
exit /b 1

:found
rem Forward slashes: bash's dirname does not split on a backslash, and the
rem script finds scripts\ from its own path.
set "SELF=%~dp0read-aloud"
set "SELF=%SELF:\=/%"

rem The script prints UTF-8, and Command Prompt's code page (437 or 1252)
rem garbles every dash in it. UTF-8 for the run, then back as it was. This
rem file itself must stay ASCII: cmd rereads it in the new code page.
set "CP="
for /f "tokens=2 delims=:." %%C in ('chcp') do set /a "CP=%%C" 2>nul
chcp 65001 >nul
"%GITBASH%" "%SELF%" %*
set "RC=%ERRORLEVEL%"
if defined CP chcp %CP% >nul
exit /b %RC%
