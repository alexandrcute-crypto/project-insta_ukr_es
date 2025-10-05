@echo off
set FILE=%1
set OUTDIR=%~dp1transcripts
if not exist "%OUTDIR%" mkdir "%OUTDIR%"
whisper "%FILE%" --model small --language uk --task transcribe --device cpu --fp16 False --output_format txt --output_dir "%OUTDIR%"
echo Done: %OUTDIR%
pause
