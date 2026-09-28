import argparse
import json
from . import describe, execute, replay_probe


def main():
    parser = argparse.ArgumentParser(description='Bounded operations from the bundled interface model')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('describe')
    commands.add_parser('replay', help='check recorded evidence offline; no hardware claim')
    call = commands.add_parser('execute')
    call.add_argument('operation')
    call.add_argument('--parameters', type=json.loads, default={})
    call.add_argument('--binding', type=json.loads, default={})
    call.add_argument('--allow-effect', action='append', choices=['write','actuate'], default=[])
    call.add_argument('--replay', help='path to an explicit interface-replay/1 fixture')
    args = parser.parse_args()
    try:
        if args.command == 'describe':
            result = describe()
        elif args.command == 'replay':
            result = replay_probe()
        else:
            from interface_runtime.evidence import read_json
            fixture = read_json(args.replay) if args.replay else None
            result = execute(args.operation,args.parameters,binding=args.binding,
                             allow_effects=args.allow_effect,replay=fixture)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result = {'ok':False,'error':{'code':'package_error','message':str(exc)}}
    print(json.dumps(result,allow_nan=False))
    return 0 if result.get('ok', True) else 1


if __name__ == '__main__':
    raise SystemExit(main())
