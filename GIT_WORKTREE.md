# Git Worktree Cheat Sheet

A worktree is an extra working directory attached to the same repository.
Each worktree checks out its own branch, so you can work on two branches at
once without stashing or switching. History is shared, so a commit made in
any worktree is visible from all of them.

## Synopsis

```
git worktree add [-f] [--detach] [--checkout] [--lock [--reason <string>]]
                 [--orphan] [(-b | -B) <new-branch>] <path> [<commit-ish>]
git worktree list [-v | --porcelain [-z]]
git worktree lock [--reason <string>] <worktree>
git worktree move <worktree> <new-path>
git worktree prune [-n] [-v] [--expire <expire>]
git worktree remove [-f] <worktree>
git worktree repair [<path>...]
git worktree unlock <worktree>
```

## Commands

| Command | What it does |
|---------|--------------|
| `add <path> [<commit-ish>]` | Create a new worktree at `<path>`. With no commit given, a branch named after the directory is created. |
| `add -b <branch> <path> [<start>]` | Create a new branch and check it out in the new worktree. `-B` creates or resets the branch. |
| `add --detach <path> <commit>` | Check out a detached HEAD, useful for inspecting an old commit or tag. |
| `add --orphan -b <branch> <path>` | Start a branch with no history. |
| `add -f ...` | Allow checking out a branch already used by another worktree. |
| `add --lock [--reason <text>] ...` | Lock the worktree as soon as it is created. |
| `list` | Show every worktree with path, HEAD, and branch. `-v` adds lock and prune status, `--porcelain` gives a script-friendly format, `-z` separates entries with NUL. |
| `lock <worktree> [--reason <text>]` | Protect a worktree's metadata from `prune`. Use for worktrees on removable or network drives that are sometimes unmounted. |
| `unlock <worktree>` | Remove a lock. |
| `move <worktree> <new-path>` | Relocate a worktree directory and update the internal links. The main worktree cannot be moved. |
| `remove <worktree>` | Delete the worktree directory and its metadata. Refuses if there are uncommitted changes unless `-f` is passed. |
| `prune` | Clean up metadata for worktrees whose directories were deleted by hand. `-n` is a dry run, `-v` reports each removal, `--expire <time>` only prunes entries older than that time. |
| `repair [<path>...]` | Fix the links between the repository and its worktrees after either side was moved or copied by hand. |

## Typical workflow for this repo

Work on a second branch while keeping the main checkout untouched:

```bash
# 1. Create a worktree with a new branch based on main
git worktree add -b week15-notes ../ISC2310-week15 main

# 2. Work inside it
cd ../ISC2310-week15
#    ... edit notebooks, then:
git add .
git commit -m "Add week 15 notes"
git push -u origin week15-notes

# 3. See all worktrees
git worktree list

# 4. Clean up when done
cd ../ISC2310
git worktree remove ../ISC2310-week15
git branch -d week15-notes
```

## Other useful patterns

```bash
# Look at an old commit without disturbing your current work
git worktree add --detach ../ISC2310-old 9a88428

# Review a colleague's branch side by side with yours
git fetch origin feature-x
git worktree add ../ISC2310-feature-x feature-x

# Recover after deleting a worktree folder with rm -rf
git worktree prune -v

# Recover after moving the whole repo folder
git worktree repair
```

## Things to know

- A branch can only be checked out in one worktree at a time.
- Worktrees live on your machine only. They are not pushed to GitHub.
- Deleting the folder by hand leaves stale metadata behind. Use `remove` or run `prune` afterwards.
- The main worktree is the original clone. It cannot be removed or moved with these commands.
