# Contributing to Capri

Capri is open source and we want help, choom. Whether you code, draw, or just break things and tell us about it,
there's room for you.

## Ways to help

- **Art.** Her nap sprite (`assets/desk/nap.png`, a small transparent PNG) is wide open.
- **Brain.** Better prompts, smarter memory, new abilities (`capri/brain/`, `capri/abilities/`).
- **Desktop life.** New effects, moves and reactions (`capri/desk/`).
- **Ports.** Windows is home; Linux runs from `arch/run.sh` / `kali/run.sh` but needs testing. macOS help is welcome.
- **Bugs.** Open an issue with what you did, what she did, and the end of `data/capri.log`.

## Getting started

1. Fork the repo and clone it.
2. Run `windows\start.bat`, `bash arch/run.sh` or `bash kali/run.sh`, Python 3.10-3.13, any GPU with 4-8 GB VRAM. Her brain
   and ears download from her right-click menu.
3. Her log is `data\capri.log` (brain: `data\llama-server.log`, ears: `data\whisper-server.log`).

## Pull requests

- Keep each PR to one thing, and say what it changes and how you tested it.
- Match the style around you: small functions, plain names, comments that say *why*.
- Don't commit models, `data/`, `config.yaml` or recordings (they're gitignored).

## A note on the art

The code is MIT. Capri's art (her skins, sprites and menu UI) is by xilia_world00 (Discord), drawn for Capri.
