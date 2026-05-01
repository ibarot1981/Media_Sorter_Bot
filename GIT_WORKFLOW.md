# Working On This Project From Home And Office

This guide is for a simple two-PC workflow:

- Home PC
- Office PC

The goal is:

1. Push your latest work from one PC to GitHub
2. Pull that latest work on the other PC
3. Keep doing that every time you switch locations

## Basic Idea

Think of GitHub as the shared middle point:

`Home PC -> GitHub <- Office PC`

Rules to keep things safe and simple:

1. Before starting work on a PC, pull the latest changes from GitHub.
2. After finishing work on a PC, commit and push your changes to GitHub.
3. Do not work on both PCs at the same time without pushing and pulling in between.

## Part 1: First-Time Setup On Office PC

Do this once on the office PC.

### 1. Install Git

Check if Git is already installed:

```powershell
git --version
```

If it is not installed, install Git for Windows from:

`https://git-scm.com/download/win`

### 2. Choose Where To Keep The Project

Example:

```powershell
cd E:\dev\python
```

You can use any folder you like.

### 3. Clone The Project From GitHub

```powershell
git clone https://github.com/ibarot1981/Media_Sorter_Bot.git
cd Media_Sorter_Bot
```

This downloads the full project to the office PC.

### 4. Create And Activate The Virtual Environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 5. Install Project Requirements

```powershell
pip install -r requirements.txt
```

### 6. Create Your Local Config

If `config.yaml` already came with the repo, edit it for the office machine.

If needed, copy from the example:

```powershell
Copy-Item config.example.yaml config.yaml
```

Then edit:

- Telegram bot token
- `server_id`
- `server_name`
- local storage paths
- temp path
- database path
- allowed Telegram user IDs

Important:

- If home and office use different local paths, each PC may need different `config.yaml` values.
- If you want different local settings on each machine, do not commit machine-specific `config.yaml` changes unless you intentionally want both machines to use the same config.

## Part 2: Daily Workflow On Any PC

Every time you start working on either PC:

### 1. Open The Project Folder

```powershell
cd E:\dev\python\Media_Sorter_Bot
```

### 2. Activate The Virtual Environment

```powershell
.venv\Scripts\Activate.ps1
```

### 3. Pull The Latest Code First

```powershell
git pull origin main
```

This brings in anything you pushed from the other location.

### 4. Do Your Work

Edit files, test, run the app, and make your changes.

## Part 3: When You Finish Working On A PC

Before leaving that PC, save your work to GitHub.

### 1. Check What Changed

```powershell
git status
```

### 2. Add Your Changed Files

To add everything:

```powershell
git add .
```

### 3. Commit Your Work

```powershell
git commit -m "Describe what you changed"
```

Examples:

```powershell
git commit -m "Add duplicate detection"
git commit -m "Update README and config editor"
git commit -m "Fix Telegram save flow"
```

### 4. Push To GitHub

```powershell
git push origin main
```

Now the other PC can pull this latest work.

## Part 4: Switching Between Home And Office

Use this pattern every time.

### If You Worked At Home And Now Go To Office

On home PC:

```powershell
git status
git add .
git commit -m "Your message"
git push origin main
```

Then on office PC:

```powershell
git pull origin main
```

### If You Worked At Office And Now Go Home

On office PC:

```powershell
git status
git add .
git commit -m "Your message"
git push origin main
```

Then on home PC:

```powershell
git pull origin main
```

## Part 5: Very Simple Safe Habit

Use this checklist every time:

### Starting Work

```powershell
git pull origin main
```

### Finishing Work

```powershell
git add .
git commit -m "Your message"
git push origin main
```

If you follow just that habit, you will usually stay safe.

## Part 6: What If Git Says There Are Changes Before Pull?

Sometimes `git pull origin main` may fail because you already changed files on this PC.

Check:

```powershell
git status
```

If the changes are your real work:

```powershell
git add .
git commit -m "Save local work before pull"
git pull origin main
```

If Git asks you to reconcile branch histories, run:

```powershell
git pull --rebase origin main
```

If you are unsure, stop there and inspect the message carefully.

## Part 7: What If Both PCs Changed The Same File?

That is called a merge conflict.

Git will tell you.

Typical safe approach:

1. Read `git status`
2. Open the conflicted file
3. Keep the correct version or combine both
4. Save the file
5. Run:

```powershell
git add .
git commit -m "Resolve merge conflict"
```

If you are new to Git, the best habit is to avoid conflicts by:

- pushing before leaving one PC
- pulling before starting on the other PC
- not editing the same unfinished work in both places

## Part 8: Recommended Rule For `config.yaml`

This project has machine-specific settings such as:

- storage paths
- temp paths
- database paths
- server name
- server ID

Because home PC and office PC may be different, you should decide one of these approaches:

### Option A: Keep `config.yaml` Local On Each Machine

Best if each PC has different paths.

In that case:

1. Keep `config.example.yaml` in Git
2. Keep each machine's `config.yaml` edited locally
3. Consider adding `config.yaml` to `.gitignore` later if you do not want machine-specific config committed

### Option B: Commit `config.yaml`

Best only if both PCs should use almost the same config.

Be careful, because one PC may overwrite settings needed by the other.

For this project, Option A is usually safer.

## Part 9: Recommended Rule For The Python Environment

Do not copy `.venv` from one PC to another.

On each PC:

1. clone or pull the code
2. create that PC's own `.venv`
3. run:

```powershell
pip install -r requirements.txt
```

## Part 10: Useful Commands

See current status:

```powershell
git status
```

Pull latest work:

```powershell
git pull origin main
```

Push your work:

```powershell
git push origin main
```

See commit history:

```powershell
git log --oneline --decorate --graph -10
```

See which remote is connected:

```powershell
git remote -v
```

## Part 11: Best Practice For You Right Now

Since you are new to Git, use this simple rule:

1. Work on only one PC at a time
2. Before leaving that PC, always commit and push
3. On the other PC, always pull before starting work

That alone will prevent most problems.

## Part 12: Example Full Office-PC Setup

```powershell
cd E:\dev\python
git clone https://github.com/ibarot1981/Media_Sorter_Bot.git
cd Media_Sorter_Bot
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
git pull origin main
```

## Part 13: Example Normal Start-And-End Session

Start work:

```powershell
cd E:\dev\python\Media_Sorter_Bot
.venv\Scripts\Activate.ps1
git pull origin main
```

Finish work:

```powershell
git status
git add .
git commit -m "Short message about today’s work"
git push origin main
```

## Part 14: If You Want Extra Safety

Before big changes:

```powershell
git status
git pull origin main
```

After important progress:

```powershell
git add .
git commit -m "Save progress"
git push origin main
```

Small, frequent commits are easier to manage than one huge commit.
