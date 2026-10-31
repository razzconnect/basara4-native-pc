@echo off
cd /d "%~dp0"
set "DISABLE_LAYER_AMD_SWITCHABLE_GRAPHICS_1=1"
start "" "%~dp0Sengoku Basara 4.exe" --play
