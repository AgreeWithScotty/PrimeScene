"""Open the complete Prime Scene report from Kodi's own settings."""
from pathlib import Path
import xbmcaddon
import xbmcgui
import xbmcvfs

addon = xbmcaddon.Addon('plugin.video.primeinterface')
report = Path(xbmcvfs.translatePath(addon.getAddonInfo('profile'))) / 'prime-scene-diagnostics.jsonl'
try:
    with report.open('rb') as source:
        size=report.stat().st_size;source.seek(max(0,size-8*1024*1024));raw=source.read(8*1024*1024)
    if size>len(raw):raw=raw.partition(b'\n')[2]
    content=raw.decode('utf-8',errors='replace')
except OSError:
    content = 'No diagnostic report exists yet. Open Smooth Prime and repeat the operation once.'
xbmcgui.Dialog().textviewer('Smooth Prime diagnostics', content)
