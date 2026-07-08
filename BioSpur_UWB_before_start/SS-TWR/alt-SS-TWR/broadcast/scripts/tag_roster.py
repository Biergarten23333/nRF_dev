#!/usr/bin/env python3
"""SINGLE SOURCE OF TRUTH for tag_id <-> BS name (and slot), from the master's runtime CFG
assignment. Driver, logger, and analysis must all resolve tag identity through here -- never
hardcode a tag map. The authority is the line the master emits to recv/raw.log:
    CFG assigned[<conn>]: bs=BS<hex> tag=<id> slot=<s>/<n> ...
(The coherent-test session was bitten by a hardcoded serial-hex map; this eliminates that class.)

CLI:  python3 tag_roster.py <session_dir> [--write]   # print (and optionally write tag_roster.json)
API:  roster_from_session(dir) -> {'by_id':{id:name}, 'by_name':{name:{tag_id,slot}}}
      load_roster(dir)   # prefer tag_roster.json if present, else parse recv raw.log
      name_for(dir, tag_id)
"""
import glob, os, re, json, sys

CFG_RE = re.compile(r'CFG assigned\[(\d+)\]: bs=(BS[0-9A-Fa-f]+) tag=(\d+)(?: slot=(\d+)/(\d+))?')

def _recv_logs(session_dir):
    # tolerate both the continuous layout (<dir>/recv/raw.log) and the old per-chunk layout
    pats = [f'{session_dir}/recv/raw.log', f'{session_dir}/recv*/raw.log',
            f'{session_dir}/**/recv*/raw.log', f'{session_dir}/chunk*/recv/raw.log']
    out = []
    for p in pats:
        out += glob.glob(p, recursive=True)
    return sorted(set(out))

def roster_from_session(session_dir):
    by_id, by_name = {}, {}
    for rl in _recv_logs(session_dir):
        try:
            for line in open(rl, errors='ignore'):
                m = CFG_RE.search(line)
                if not m:
                    continue
                tid, name = m.group(3), m.group(2)
                slot = int(m.group(4)) if m.group(4) else None
                by_id[tid] = name
                by_name[name] = {'tag_id': int(tid), 'slot': slot}
        except FileNotFoundError:
            pass
        if by_id:
            break     # tag_ids are assigned once per session; first log with CFG lines suffices
    return {'by_id': by_id, 'by_name': by_name}

def write_roster_json(session_dir):
    r = roster_from_session(session_dir)
    with open(os.path.join(session_dir, 'tag_roster.json'), 'w') as f:
        json.dump(r, f, indent=2)
    return r

def load_roster(session_dir):
    p = os.path.join(session_dir, 'tag_roster.json')
    if os.path.exists(p):
        try:
            return json.load(open(p))
        except (ValueError, OSError):
            pass
    return roster_from_session(session_dir)

def name_for(session_dir, tag_id):
    return load_roster(session_dir)['by_id'].get(str(tag_id))

if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit('usage: tag_roster.py <session_dir> [--write]')
    d = sys.argv[1]
    r = write_roster_json(d) if '--write' in sys.argv[2:] else roster_from_session(d)
    print(json.dumps(r, indent=2))
