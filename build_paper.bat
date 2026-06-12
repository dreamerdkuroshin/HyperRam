@echo off
REM Build script for HyperRAM Research Paper (LaTeX)
REM Requires MiKTeX or TeX Live installed
REM Download MiKTeX from: https://miktex.org/

echo Compiling research_paper.tex (pass 1)...
pdflatex -interaction=nonstopmode research_paper.tex
if %errorlevel% neq 0 (
    echo Pass 1 had errors - check research_paper.log
    exit /b %errorlevel%
)

echo Compiling pass 2 (references)...
pdflatex -interaction=nonstopmode research_paper.tex

echo Done! Output: research_paper.pdf
