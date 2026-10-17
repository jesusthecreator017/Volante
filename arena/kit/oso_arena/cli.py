"""`oso-arena` / `python -m oso_arena`: train, export, check, eval, match, animate.
Exit codes: 0 ok, 1 check failed or usage error."""
import argparse
import json
import sys
from pathlib import Path

from oso_arena import basemodel, bot, builds, check, evaluate, export, log, match, policy


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # usage errors exit 1 (argparse's default is 2)
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def base_path():
    return str(basemodel.path())  # ValueError "the base model is not bundled in this version of the kit" when it is missing


def _who(x):
    return "bot" if x == "bot" else base_path() if x == "base" else x


def _load_state(path):
    """(state, header) for export: a .safetensors training state (the trainer's checkpoints; the header carries the run's build) or, from
    older runs, a .pt checkpoint of ours (needs torch, loaded with weights_only)."""
    path = export.state_path(path)
    if str(path).endswith(".safetensors"):
        from safetensors import safe_open
        from safetensors.numpy import load_file

        with safe_open(str(path), framework="np") as f:
            head = dict(f.metadata() or {})
        return load_file(str(path)), head
    try:
        import torch
    except ImportError:
        raise ValueError("exporting a .pt checkpoint needs torch (pip install torch); a .safetensors training state does not") from None

    # weights_only=True: tensors and plain containers only, so this cannot execute code from the file (our own .pt checkpoints load)
    ck = torch.load(path, map_location="cpu", weights_only=True)
    return (ck["model"] if "model" in ck else ck), {}


def _run_build(a, head):
    """--build when given, else the build the trainer recorded in the checkpoint's header."""
    if a.build:
        return builds.parse(a.build)
    if "build" not in head:
        raise ValueError("this checkpoint does not record a build: pass --build power,speed,reach,accuracy")
    try:
        b = json.loads(head["build"])
    except ValueError:
        b = None
    errs = builds.validate(b)
    if errs:
        raise ValueError("the build in the checkpoint is invalid (" + "; ".join(errs) + "): pass --build")
    return b


def cmd_check(a):
    r = check.check_file(base_path() if a.file == "base" else a.file)
    if r.ok:
        m = r.meta
        print(f"ok: {m['name']!r} build {json.dumps(m['build'], separators=(',', ':'))} sha256 {m['sha256']}")
        return 0
    for reason in r.reasons:
        print(reason)
    return 1


def cmd_export(a):
    state, head = _load_state(a.ckpt)
    export.export(state, a.out, a.name, a.description, _run_build(a, head))
    print(f"wrote {a.out}")
    r = check.check_file(a.out)
    for reason in r.reasons:
        print(reason)
    return 0 if r.ok else 1


def cmd_train(argv):
    """`oso-arena train ...`: every argument after `train` belongs to the trainer's own parser (oso_arena.train)."""
    if any(x in ("-h", "--help") for x in argv):  # needs no torch
        from oso_arena import trainargs

        trainargs.parse_args(argv)
    try:
        from oso_arena import train
    except ImportError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return train.main(argv)


def cmd_eval(a):
    pa = policy.NumpyPolicy.load(a.file)
    vs = _who(a.vs)
    pb = None if vs == "bot" else policy.NumpyPolicy.load(vs)
    fb = bot.bot_actions if pb is None else pb.act
    bb = builds.ALL6 if pb is None else pb.meta["build"]
    r = evaluate.play_games(pa.act, fb, a.games, a.seed, builds=(pa.meta["build"], bb))
    evaluate.table(f"{pa.meta['name']} vs {a.vs}", evaluate.summarize(r))
    return 0


def cmd_match(a):
    lg = match.play(_who(a.a), _who(a.b), a.seed, first_server=a.first_server)
    log.write(a.out, lg)
    res = "void" if lg.voided else f"{lg.a_name} {lg.score[0]} - {lg.score[1]} {lg.b_name}"
    print(f"{res}, {lg.rallies} rallies, {lg.duration_s / 60:.1f} simulated min -> {a.out}")
    return 0


def cmd_animate(a):
    from oso_arena import animate

    animate.to_replay(log.read(a.log), a.out)
    print(f"wrote {a.out}")
    return 0


def main(argv=None):
    ap = _Parser(prog="oso-arena", description="oso Arena kit (contract oso-arena/1)")
    sub = ap.add_subparsers(dest="cmd", required=True, parser_class=_Parser)
    sub.add_parser("train", help="train a model with PPO self-play (needs torch: the [train] extra); see: oso-arena train --help", add_help=False)
    p = sub.add_parser("check", help="check a submission file (or the packaged base model: check base)")
    p.add_argument("file")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("export", help="training checkpoint (runs/x/best) -> submission file; the build comes from the run")
    p.add_argument("ckpt")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--description", default="")
    p.add_argument("--build", help="power,speed,reach,accuracy (integers 1-10, sum 24); default: the build recorded in the checkpoint")
    p.set_defaults(fn=cmd_export)
    p = sub.add_parser("eval", help="play games against the bot, the base model or another file")
    p.add_argument("file")
    p.add_argument("--vs", default="bot", help="bot | base | FILE")
    p.add_argument("--games", type=int, default=100)
    p.add_argument("--seed", type=int, default=1)
    p.set_defaults(fn=cmd_eval)
    p = sub.add_parser("match", help="play one game and write the match log")
    p.add_argument("a", help="FILE | bot | base")
    p.add_argument("b", help="FILE | bot | base")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--first-server", type=int, choices=(0, 1))
    p.set_defaults(fn=cmd_match)
    p = sub.add_parser("animate", help="match log -> replay.bin for the viewer")
    p.add_argument("log")
    p.add_argument("-o", "--out", required=True)
    p.set_defaults(fn=cmd_animate)
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["train"]:
        return cmd_train(argv[1:])
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
