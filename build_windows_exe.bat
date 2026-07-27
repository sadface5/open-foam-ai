@echo off
REM ===========================================================================
REM  Builds the OpenFOAM/SPUMA Debugging Assistant into a single Windows .exe.
REM  Double-click this file, or run it from a terminal in the project folder.
REM ===========================================================================
setlocal

echo.
echo === Activating the virtual environment ===
call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo Could not find .venv. Create it first with:  python -m venv .venv
    pause
    exit /b 1
)

echo.
echo === Making sure build tools are installed ===
pip install -r requirements.txt

echo.
echo === Building the .exe (this can take a few minutes) ===
pyinstaller --noconfirm --clean --name "OpenFOAM-AI" --windowed --onefile ^
  --collect-all sklearn --collect-all scipy --collect-submodules anthropic ^
  main.py
if errorlevel 1 (
    echo Build failed. See the messages above.
    pause
    exit /b 1
)

echo.
echo === Copying editable folders next to the app ===
REM The app reads these from beside the .exe so you can edit them without rebuilding.
xcopy /E /I /Y skills dist\skills >nul
xcopy /E /I /Y knowledge dist\knowledge >nul
if exist .env (
    copy /Y .env dist\.env >nul
) else (
    echo ANTHROPIC_API_KEY=PASTE_YOUR_KEY_HERE>dist\.env
)

echo.
echo ============================================================
echo  Done!  Your app is:  dist\OpenFOAM-AI.exe
echo  Folders next to it:  dist\skills  and  dist\knowledge
echo  Set your API key inside the app (Settings), or edit dist\.env
echo ============================================================
echo.
pause
endlocal
