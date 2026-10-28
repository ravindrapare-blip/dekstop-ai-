---
title: Daemon
description: Run a shared Open Interpreter app-server daemon for multiple clients.
---

By default, `interpreter` runs everything in the foreground process: the
terminal UI and the runtime live in the same process, and exiting the UI ends
the session cleanly. You do not need a daemon for normal use.

For setups where several clients should share one runtime — for example an
editor integration and a terminal attached to the same machine — Open
Interpreter can also run its app-server as a background **daemon** that clients
connect to.

## Managing the daemon

```bash
interpreter app-server daemon start
interpreter app-server daemon restart
interpreter app-server daemon stop
interpreter app-server daemon version
```

`start` launches a daemon listening on a local unix socket and returns once it
is healthy. Repeated `start` calls reuse a healthy running daemon instead of
spawning a second one.

## Connecting a client

Point any app-server client, including the terminal UI, at the daemon's
endpoint:

```bash
interpreter --remote <ws-url-or-unix-socket>
```

See [Server deployments](/docs/remote) for remote endpoints and
[SDK](/docs/sdk) for programmatic clients.

## Where the daemon lives

The daemon's runtime files live under your Open Interpreter home directory,
which defaults to `~/.openinterpreter` and can be overridden with the
`INTERPRETER_HOME` environment variable. The daemon log is the first place to
look when debugging startup problems.

## Troubleshooting

- **`start` reports the app server is running but not managed** — another
  process is already listening on the daemon socket. Stop that process or use
  a different home directory.
- **Something is wedged after an update or crash** — run
  `interpreter app-server daemon stop` and start again.
