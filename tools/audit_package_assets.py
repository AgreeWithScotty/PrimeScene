"""Check the actual package's data/asset references; do not run provider code."""
import ast
import hashlib
import json
import sys
from pathlib import Path
import xml.etree.ElementTree as ET


def inspect(root):
    root = Path(root).resolve()
    files = {p.relative_to(root).as_posix(): p for p in root.rglob('*')
             if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}
    runtime = {name: path.read_text(encoding='utf-8-sig') for name, path in files.items()
               if path.suffix in ('.py', '.xml', '.json')}
    assets = {}
    for name, path in files.items():
        if path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.ttf', '.otf', '.json'):
            continue
        refs = [{'file': other, 'lines': [i for i, line in enumerate(text.splitlines(), 1)
                                         if path.name in line]}
                for other, text in runtime.items() if other != name and path.name in text]
        assets[name] = {'bytes': path.stat().st_size,
                        'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'references': refs}

    # Validate local literal paths and mapped/dynamic asset families, not just
    # absence of unused files. Amazon/profile artwork is intentionally external.
    missing = []
    media = root / 'resources/skins/Default/media'
    for name, text in runtime.items():
        if not name.endswith('.xml'):
            continue
        for node in ET.fromstring(text).iter():
            value = (node.text or '').strip()
            if node.tag not in ('texture', 'texturefocus', 'texturenofocus', 'texturebg',
                                'midtexture', 'lefttexture', 'righttexture', 'overlaytexture', 'filename', 'icon', 'fanart'):
                continue
            if not value or '$' in value or '://' in value:
                continue
            target = (root / value if name == 'addon.xml' else
                      root / 'resources/skins/Default/fonts' / value if node.tag == 'filename' else media / value)
            if not target.is_file():
                missing.append({'file': name, 'reference': value})
    art = json.loads(files['resources/lib/genre_art.json'].read_text(encoding='utf-8'))['tiles']
    for key, names in art.items():
        for value in names:
            if Path(value).name != value or not (media / 'genre-art' / value).is_file():
                missing.append({'file': 'resources/lib/genre_art.json', 'tile': key, 'reference': value})

    # Import reachability is supporting inventory only. Entry scripts are also
    # invoked by Kodi/RunScript and package __init__ files are implicit imports.
    modules = {name[:-3].replace('/', '.'): name for name in runtime if name.endswith('.py')}
    edges = {}
    for module, name in modules.items():
        imports = set()
        for node in ast.walk(ast.parse(runtime[name])):
            if isinstance(node, ast.Import):
                imports.update(n.name for n in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = module.split('.')[:-1]
                if node.level:
                    base = base[:len(base) - node.level + 1]
                    target = '.'.join(base + ([node.module] if node.module else []))
                else:
                    target = node.module or ''
                imports.add(target)
                imports.update(target + '.' + n.name for n in node.names)
        edges[module] = sorted(i for i in imports if i in modules)
    pending = [name for name in modules if '.' not in name]
    reachable = set(pending)
    while pending:
        for target in edges[pending.pop()]:
            if target not in reachable:
                reachable.add(target)
                pending.append(target)
    unreachable = [name for module, name in modules.items()
                   if module not in reachable and not module.endswith('.__init__')]
    return {'assets': assets, 'unused_files': sorted(n for n, v in assets.items() if not v['references']),
            'missing_references': missing, 'python_files': len(modules),
            'unreachable_python_files': sorted(unreachable),
            'scope': 'Actual bundled runtime Python/XML/JSON literals, dynamic genre map, manifest/font paths; no provider/Kodi simulation.'}


if __name__ == '__main__':
    report = inspect(sys.argv[1])
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'assets'}, indent=2))
    raise SystemExit(bool(report['unused_files'] or report['missing_references']))
