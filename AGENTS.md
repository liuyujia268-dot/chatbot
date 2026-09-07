# AGENTS.md

## Environment

This project is developed and operated primarily on Windows.

Unless explicitly instructed otherwise, assume the shell environment is:

```text
cmd.exe
```

Do NOT assume PowerShell, Bash, WSL, Git Bash, or other Unix-like shells are available.

---

## Shell Rules

All shell commands must use Windows CMD syntax.

### Required

Use commands compatible with `cmd.exe`, for example:

```cmd
dir
cd
copy
move
del
rmdir
mkdir
type
where
find
findstr
set
set PATH=...
echo
start
tasklist
taskkill
```

Use Windows-style paths:

```text
C:\Users\User\Project
D:\Workspace\MyApp
.\src
..\build
```

Quote paths containing spaces:

```cmd
cd /d "D:\My Projects\Test App"
```

When changing drives and directories at the same time, prefer:

```cmd
cd /d D:\Workspace\Project
```

---

## Do Not Use PowerShell

Do not generate or execute PowerShell commands unless the user explicitly requests PowerShell.

Avoid commands or syntax such as:

```powershell
Get-ChildItem
Get-Content
Set-Location
Remove-Item
Copy-Item
Move-Item
New-Item
Select-String
Where-Object
ForEach-Object
Invoke-WebRequest
$env:PATH
$variable = ...
```

Do not use PowerShell-specific constructs such as:

```text
$()
$_
@()
@{}
-ErrorAction
-LiteralPath
-Recurse
-Force
```

If a task can be completed using CMD, use CMD.

---

## Do Not Assume Unix Tools

Do not assume the following commands exist:

```text
ls
cat
cp
mv
rm
grep
sed
awk
touch
chmod
which
export
pwd
```

Use Windows CMD equivalents instead.

Examples:

```text
ls        -> dir
cat       -> type
cp        -> copy
mv        -> move
rm file   -> del
rm -r     -> rmdir /s /q
grep      -> findstr
which     -> where
pwd       -> cd
```

If a Unix-style tool is provided by a project's own toolchain, it may be used only when its availability has been verified.

---

## Command Chaining

Use CMD operators.

Run the next command only if the previous command succeeds:

```cmd
command1 && command2
```

Run the next command regardless of whether the previous command succeeds:

```cmd
command1 & command2
```

Run the next command only if the previous command fails:

```cmd
command1 || command2
```

Do not use Bash-specific multiline syntax.

For long or complicated command sequences, prefer creating or editing a `.bat` or `.cmd` script instead of forcing everything into one command line.

---

## Environment Variables

Use CMD syntax:

```cmd
set NAME=value
echo %NAME%
```

Example:

```cmd
set NODE_ENV=development
npm run dev
```

For a single chained invocation:

```cmd
set NODE_ENV=development && npm run dev
```

Do not use:

```text
$env:NODE_ENV="development"
export NODE_ENV=development
```

---

## File Operations

Create a directory:

```cmd
mkdir build
```

Copy a file:

```cmd
copy /Y "source.txt" "destination.txt"
```

Copy a directory:

```cmd
xcopy "src" "backup\src" /E /I /Y
```

If `robocopy` is appropriate, it may be used:

```cmd
robocopy "src" "backup\src" /E
```

Move:

```cmd
move /Y "old.txt" "new.txt"
```

Delete a file:

```cmd
del /F /Q "file.txt"
```

Delete a directory recursively:

```cmd
rmdir /S /Q "build"
```

Print a text file:

```cmd
type "file.txt"
```

Search text recursively:

```cmd
findstr /S /N /I /C:"keyword" *.*
```

---

## Executables

When invoking executables, prefer commands that work directly from CMD.

Examples:

```cmd
python --version
node --version
npm --version
git --version
cmake --version
```

Use `where` to locate an executable:

```cmd
where python
where node
where git
```

If an executable path contains spaces:

```cmd
"C:\Program Files\Example\tool.exe" --version
```

Do not use PowerShell's `&` invocation syntax.

---

## Python

Prefer:

```cmd
python script.py
```

If `python` is unavailable, check:

```cmd
where python
where py
```

Then use:

```cmd
py script.py
```

For virtual environments:

```cmd
python -m venv .venv
.venv\Scripts\activate.bat
```

Do not use PowerShell activation scripts such as:

```text
.venv\Scripts\Activate.ps1
```

---

## Node.js / npm

Use standard CMD-compatible commands:

```cmd
npm install
npm run build
npm run dev
npm test
```

Prefer invoking package-local binaries through:

```cmd
npx <command>
```

rather than assuming a package is installed globally.

---

## Git

Use normal Git commands from CMD:

```cmd
git status
git diff
git log --oneline
git branch
```

Before making large or destructive changes, inspect the current repository state.

Do not discard unrelated user changes.

Do not run destructive commands such as:

```cmd
git reset --hard
git clean -fd
```

unless explicitly required and clearly justified.

---

## Windows Path Handling

Remember that Windows paths commonly contain:

```text
\
:
spaces
()
[]
&
```

Quote paths whenever there is any ambiguity.

Good:

```cmd
type "C:\Users\User Name\project\file.txt"
```

Avoid unnecessarily converting Windows paths to `/c/...` or Unix-style paths.

When a command requires a path relative to the repository root, prefer:

```text
.\path\to\file
```

---

## Temporary Files

Prefer placing temporary project files inside the project workspace when practical.

Do not scatter temporary scripts or generated files across the user's Desktop, Documents directory, or system directories.

Clean up temporary files after they are no longer needed.

---

## Editing

Before modifying a file:

1. Inspect the relevant file and surrounding code.
2. Preserve the existing coding style unless there is a reason to change it.
3. Make the smallest reasonable change.
4. Do not rewrite unrelated code.
5. After editing, verify the result.

When possible, run an appropriate build, test, lint, compiler, or syntax check.

---

## Command Safety

Before executing destructive filesystem operations, verify the target path carefully.

Be especially cautious with:

```cmd
del
rmdir /s
robocopy /MIR
format
diskpart
git clean
git reset
```

Never delete broad directories based on an unverified environment variable or an empty path.

Avoid commands such as:

```cmd
rmdir /S /Q "%SOME_VAR%"
```

unless `%SOME_VAR%` has first been confirmed to contain the intended path.

---

## Permissions

Do not assume Administrator privileges.

Prefer commands that work under the current user's permissions.

If Administrator privileges are genuinely required, explain why instead of silently relying on elevation.

Do not modify system-wide configuration, registry entries, services, firewall rules, drivers, or global environment variables unless the task actually requires it.

---

## Package Installation

Before installing software globally, check whether the project already provides the required dependency.

Prefer project-local dependencies.

Examples:

```cmd
npm install
python -m pip install -r requirements.txt
```

Do not silently install unrelated global tools.

If a tool is missing, first verify with:

```cmd
where <tool>
```

---

## Build and Test Workflow

After making code changes, use the project's existing commands whenever available.

Typical examples:

```cmd
npm test
npm run build
python -m pytest
cmake --build build
```

Do not claim a build or test passed unless the command was actually run successfully.

If verification cannot be performed, clearly state what was not verified.

---

## Repository Awareness

Before substantial work, inspect the repository structure and relevant configuration files.

Useful CMD commands include:

```cmd
dir
dir /A
dir /S /B
git status
```

Do not recursively dump huge directories unless necessary.

Avoid scanning generated or dependency directories such as:

```text
node_modules
.git
dist
build
target
.venv
venv
```

unless the task specifically concerns them.

---

## Scripts

For reusable Windows automation, prefer:

```text
.bat
.cmd
```

unless the project already uses another scripting language such as Python.

Batch scripts must use CMD-compatible syntax.

Example:

```bat
@echo off
setlocal

echo Building project...
npm run build

if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo Build completed successfully.
exit /b 0
```

---

## Priority

When choosing how to perform a shell task, use this order:

1. Existing project scripts or tools.
2. Native Windows CMD commands.
3. Cross-platform executables already installed in the project environment.
4. A small Python script when CMD is unsuitable for complex processing.

PowerShell should only be used when explicitly requested or when a task genuinely cannot reasonably be completed otherwise.

If PowerShell appears necessary, do not silently switch shells. Explain the reason first.

---

## Final Rule

Assume all terminal commands shown to the user will be copied directly into Windows `cmd.exe`.

Therefore:

* Commands must be valid CMD syntax.
* Paths must follow Windows conventions.
* Do not mix CMD, PowerShell, and Bash syntax.
* Do not use PowerShell merely because the operating system is Windows.
* If shell behavior is uncertain, verify it before relying on it.
