# KHROXY managed module
"""Additive CLI entrypoints. Legacy parser and TUI remain unchanged."""
import argparse
import json
from .storage import Store


def dispatch(app, argv):
    if not argv or argv[0] not in ('storage', 'profile', 'import-data', 'engine', 'test-profiles'):
        return False
    if argv[0] == 'test-profiles':
        from .testing import cli
        cli(app, argv[1:])
        return True
    if argv[0] == 'engine':
        from .engines import cli
        cli(app, argv[1:])
        return True
    parser = argparse.ArgumentParser(prog='khroxy ' + argv[0])
    if argv[0] == 'import-data':
        parser.add_argument('--file')
        parser.add_argument('--source', default='local')
        parser.add_argument('--allow-plaintext', action='store_true')
        args = parser.parse_args(argv[1:])
        app.check_name(args.source)
        if args.file:
            path = app.Path(args.file)
            if path.is_symlink() or not path.is_file() or path.stat().st_size > app.MAX_SUB_BYTES:
                raise ValueError('Unsafe or oversized import file')
            with path.open('rb') as stream: raw = stream.read(app.MAX_SUB_BYTES + 1)
        else:
            raw = app.sys.stdin.buffer.read(app.MAX_SUB_BYTES + 1)
        nodes = app.parse_subscription(raw, args.allow_plaintext)
        fd = app.lock()
        try:
            state, summary = app.importing.merge(app.load(), nodes, args.source)
            app.write_file(app.STATE, app.serial(state))
        finally:
            app.os.close(fd)
        print(json.dumps(summary))
        return True
    verbs = parser.add_subparsers(dest='action', required=True)
    if argv[0] == 'storage':
        verbs.add_parser('status')
        verbs.add_parser('migrate')
        p = verbs.add_parser('backup', help='Write a private backup containing credentials; never publish it')
        p.add_argument('path')
        p = verbs.add_parser('restore'); p.add_argument('path')
        p = verbs.add_parser('history'); p.add_argument('--profile'); p.add_argument('--limit', type=int, default=100)
    else:
        for action in ('enable','disable','delete','restore','purge'):
            p = verbs.add_parser(action); p.add_argument('name')
            if action == 'purge': p.add_argument('--confirm', required=True, choices=('PURGE',))
    args = parser.parse_args(argv[1:])
    store = Store(app.STATE)
    if argv[0] == 'storage' and args.action == 'status':
        print(json.dumps({'backend':'sqlite' if store.exists else 'legacy-json', 'schema':1}))
        return True
    fd = app.lock()
    try:
        if args.action == 'migrate':
            changed = store.migrate()
            print(json.dumps({'backend':'sqlite','migrated':changed,'legacy_preserved':True}))
        elif argv[0] == 'profile':
            app.check_name(args.name)
            store.lifecycle(args.name, args.action)
            print(json.dumps({'profile':args.name,'action':args.action}))
        elif args.action == 'backup':
            store.export(args.path)
            print(json.dumps({'backup_written':True,'contains_credentials':True}))
        elif args.action == 'restore':
            if app.running():
                raise ValueError('Stop the managed proxy before restoring a backup; existing sessions are not interrupted automatically')
            store.restore(args.path)
            print(json.dumps({'restored':True,'pre_restore_backup':True}))
        elif args.action == 'history':
            print(json.dumps(store.history('measurements', args.profile, args.limit)))
    finally:
        app.os.close(fd)
    return True
