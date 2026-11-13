@echo off
setlocal
set "MCU_STUDIO_DIR=%~dp0"

if not exist "%MCU_STUDIO_DIR%Mcu_Studio.exe" (
    echo Mcu_Studio.exe was not found beside this script.
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$app = Join-Path $env:MCU_STUDIO_DIR 'Mcu_Studio.exe';" ^
  "$desktop = [Environment]::GetFolderPath('Desktop');" ^
  "$link = Join-Path $desktop 'Mcu_Studio.lnk';" ^
  "$shell = New-Object -ComObject WScript.Shell;" ^
  "$shortcut = $shell.CreateShortcut($link);" ^
  "$shortcut.TargetPath = $app;" ^
  "$shortcut.WorkingDirectory = $env:MCU_STUDIO_DIR.TrimEnd('\');" ^
  "$shortcut.IconLocation = $app + ',0';" ^
  "$shortcut.Description = 'MCU Live Watch Studio';" ^
  "$shortcut.Save()"

if errorlevel 1 (
    echo Failed to create the desktop shortcut.
    pause
    exit /b 1
)

echo Desktop shortcut created: Mcu_Studio
pause
