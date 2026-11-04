@echo off
setlocal
cd /d "%~dp0"
echo ===== Video Downloader - build on this PC =====

where python >nul 2>nul
if errorlevel 1 goto nopython

python -m pip install --upgrade pip pyinstaller
if not exist bin mkdir bin

if not exist bin\yt-dlp.exe powershell -NoProfile -Command "Invoke-WebRequest 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe' -OutFile 'bin\yt-dlp.exe'"
if not exist bin\ffmpeg.exe powershell -NoProfile -Command "Invoke-WebRequest 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl.zip' -OutFile 'ffmpeg.zip'; Expand-Archive -Force 'ffmpeg.zip' 'ffmpeg_tmp'; Get-ChildItem 'ffmpeg_tmp' -Recurse -Include ffmpeg.exe,ffprobe.exe | Copy-Item -Destination 'bin'; Remove-Item -Recurse -Force 'ffmpeg_tmp','ffmpeg.zip'"

pyinstaller --noconfirm --onedir --windowed --name VideoDownloader --icon app\icon.ico --add-data "app\icon.ico;." app\video_downloader.py
robocopy bin dist\VideoDownloader\bin /E >nul

set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" goto hasinno
echo.
echo The app is ready in:  dist\VideoDownloader   (run VideoDownloader.exe inside)
echo For a proper Setup.exe, install Inno Setup 6 from https://jrsoftware.org/isinfo.php and run this file again.
goto end

:hasinno
"%ISCC%" /DAppVersion=1.0.0 installer\installer.iss
echo.
echo Installer ready:  installer\output\VideoDownloader-Setup.exe
goto end

:nopython
echo Python was not found. Install it from python.org (tick "Add Python to PATH") and run this again.

:end
pause
