"""Open the complete Prime Scene report from Kodi's own settings."""
from pathlib import Path
import xbmcaddon
import xbmcgui
import xbmcvfs

addon = xbmcaddon.Addon('plugin.video.primeinterface')
report = Path(xbmcvfs.translatePath(addon.getAddonInfo('profile'))) / 'prime-scene-diagnostics.jsonl'
try:
    content = report.read_text(encoding='utf-8')
except OSError:
    content = 'No diagnostic report exists yet. Open Prime Scene and repeat the operation once.'
xbmcgui.Dialog().textviewer('Prime Scene diagnostics', content)
