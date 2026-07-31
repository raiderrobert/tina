# Triage an incoming bug report

You are handed one open bug report. Work out what it is, then do that one thing.

## Reproduce it first

1. Read the report and identify the smallest command or request that should
   trigger it.
2. Run that against `main`. Write down what you actually saw, not what you
   expected.

## Then branch

- **It reproduces and the fix is contained.** Fix it, add a regression test that
  fails without the fix, and open a pull request that closes the issue. Report
  `resolved`, with the pull request as your artifact.
- **It does not reproduce.** Say what you ran and what happened instead. Report
  `no_action_needed`.
- **It reproduces, but the fix is a judgement call** — a behaviour change, an API
  break, a dependency bump, anything touching money or auth. Do not guess.
  Comment on the issue with what you found and what the options are, and report
  `needs_human`.
- **You could not get far enough to tell.** Report `failed` and say where you got
  stuck.

Never report `resolved` for a pull request you did not open. Every URL you list
is fetched afterwards to confirm it exists.
