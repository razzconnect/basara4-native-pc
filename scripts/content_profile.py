"""Prepare explicit offline AppContent metadata; SKU is a probe setting, not a license check."""
import argparse
import json
import struct
from pathlib import Path
from prepare import read_sfo, sfo

MAX_ADDONS = 64
ADDON_LABEL_LENGTH = 16


def find_addons(dlc_dir, title_id):
    addons = {}
    if dlc_dir is None or not str(dlc_dir).strip():
        return []
    root = Path(dlc_dir)
    if not root.is_dir():
        print(f'DLC: folder does not exist: {root}')
        return []
    for path in sorted(root.iterdir()):
        if not path.is_dir():
            continue
        sfo_path = path / 'sce_sys' / 'param.sfo'
        if not sfo_path.is_file():
            continue
        values = read_sfo(sfo_path)
        if not values:
            print(f'DLC: skipped {path.name}: unreadable param.sfo')
            continue
        content = values.get('CONTENT_ID', '')
        label = content[20:] if len(content) > 20 else ''
        if values.get('TITLE_ID') != title_id or values.get('CATEGORY') != 'ac' or len(label) != ADDON_LABEL_LENGTH:
            print(f'DLC: skipped {path.name}: not an add-on of {title_id} ({content or "no CONTENT_ID"})')
            continue
        try:
            encoded = label.encode('ascii')
        except UnicodeEncodeError:
            print(f'DLC: skipped {path.name}: entitlement label is not ASCII')
            continue
        if any(not (chr(char).isalnum() or char == ord('_')) for char in encoded):
            print(f'DLC: skipped {path.name}: invalid entitlement label')
            continue
        if path.name != label:
            print(f'DLC: skipped {path.name}: folder must be named {label}')
            continue
        addons.setdefault(label, (label, values.get('TITLE', label), path.name))
    all_addons = list(addons.values())
    for label, _title, _source in all_addons[MAX_ADDONS:]:
        print(f'DLC: skipped {label}: runtime supports at most {MAX_ADDONS} add-ons')
    result = all_addons[:MAX_ADDONS]
    for label, title, source in result:
        printable_title = title.encode('ascii', 'backslashreplace').decode('ascii')
        print(f'DLC: ready to mount {printable_title} ({label}, {source})')
    return result


def prepare(game, out, sku='full', dlc_dir=None):
    values=sfo((game/'sce_sys/param.sfo').read_bytes())
    params=[values.get(f'USER_DEFINED_PARAM_{i}',0) for i in range(1,5)]
    if any(type(v) is not int or not 0<=v<=0xffffffff for v in params):
        raise ValueError('invalid user-defined parameter')
    addons = find_addons(dlc_dir, values.get('TITLE_ID'))
    profile=dict(title_id=values.get('TITLE_ID'),sku=sku,sku_source='explicit probe setting',
                 user_params=params,
                 mounted_addons=[dict(label=l, title=t, source=s) for l, t, s in addons],
                 boot_attr=0)
    out.mkdir(parents=True,exist_ok=True)
    blob = struct.pack('<8s6I', b'G3CONT02', {'full':3,'trial':1}[sku], *params, len(addons))
    for label, _title, _source in addons:
        blob += label.encode('ascii') + b'\0'
    (out/'content.bin').write_bytes(blob)
    (out/'content-profile.json').write_text(json.dumps(profile,indent=2)+'\n')
    print(f'AppContent profile: SKU={sku} (probe setting), user params={params}, add-ons={len(addons)}')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('game',type=Path)
    p.add_argument('--out',type=Path,default=Path(__file__).resolve().parent.parent/'out')
    p.add_argument('--sku',choices=['full','trial'],default='full')
    p.add_argument('--dlc-dir',type=Path,help='folder containing extracted DLC folders')
    a=p.parse_args();prepare(a.game,a.out,a.sku,a.dlc_dir)
