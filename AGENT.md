# Project Purpose

- The nightly runner checks out all branches of a number of git
  repositories and reruns them (sandboxed) nightly when new commits
  come in.
- There's also a web server that lets users start nightly runs, show
  status, and assess server health.
- The main aspect of server health we care about is disk space. Mainly
  we need to warn users when they are using too much of it.
- Two core components: the nightly runner (updates Github
  repositories, starts nightlies, logs, uploads results) and the web
  server (checks status, shows health info). These must be *loosely*
  coupled, crashes in either one shouldn't affect the other. Data is
  based between them using on-disk runfiles, not actual communication.

# Coding Style

- Maintenance budget is low. Minimize code. Do things awkwardly if it
  means shorter code, fewer concepts, simpler functions.
- Always type-check with `mypy` before committing.
- Document all new config file keys in `views/docs.view`.
