"""A small command-line interface: ``python -m sidstation``."""

from __future__ import annotations

import argparse

from . import __version__
from .bank import Bank


def _cmd_info(bank: Bank) -> int:
    patches = bank.patches
    print(f"prefix={bank.prefix.hex(' ')}  messages={len(bank.messages)}  patches={len(patches)}")
    print(f"{'#':<4}  {'name':<10}  {'version':<7}  {'size':<5}  bytes")
    print("-" * 48)
    for i, patch in enumerate(patches):
        size = patch.declared_size if patch.declared_size is not None else len(patch.data)
        print(f"{i:<4}  {patch.name:<10}  {patch.version:<7}  {size:<5}  {len(patch.data)}")
    return 0


def _cmd_names(bank: Bank) -> int:
    for i, patch in enumerate(bank.patches):
        print(f"{i:>3}  {patch.name}")
    return 0


def _cmd_show(bank: Bank, index: int) -> int:
    patches = bank.patches
    if not 0 <= index < len(patches):
        print(f"error: patch index {index} out of range (0..{len(patches) - 1})")
        return 2
    patch = patches[index]
    on = [
        f"OSC{n + 1}"
        for n, flag in enumerate((patch.osc1_enabled, patch.osc2_enabled, patch.osc3_enabled))
        if flag
    ]
    print(f"name:            {patch.name!r}")
    print(f"version:         {patch.version}")
    print(f"declared_size:   {patch.declared_size}")
    print(f"data length:     {len(patch.data)}")
    print(f"oscillators on:  {', '.join(on)}")
    print(f"poly:            {patch.poly}")
    print(f"filter cutoff:   {patch.filter_cutoff}  resonance: {patch.resonance}")
    for n, osc in enumerate(patch.oscillators):
        print(
            f"  OSC{n + 1}  waveform={osc.waveform} ring={osc.ring_mod} sync={osc.sync}  "
            f"ADSR={osc.attack}/{osc.decay}/{osc.sustain}/{osc.release}"
        )
    for n, table in enumerate(patch.tables):
        print(f"  TABLE{n + 1}  {table!r}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sidstation",
        description="Inspect Elektron SidStation patch (.syx) files.",
    )
    parser.add_argument("--version", action="version", version=f"sidstation {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_info = sub.add_parser("info", help="summarise a bank")
    p_info.add_argument("file")

    p_names = sub.add_parser("names", help="list patch names")
    p_names.add_argument("file")

    p_show = sub.add_parser("show", help="show one patch in detail")
    p_show.add_argument("file")
    p_show.add_argument("index", type=int)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 1
    bank = Bank.read(args.file)
    if args.command == "info":
        return _cmd_info(bank)
    if args.command == "names":
        return _cmd_names(bank)
    if args.command == "show":
        return _cmd_show(bank, args.index)
    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
