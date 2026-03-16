FEAT: identify config options which aren't yet shown in dialog and expose them
BUG: clean out old/deprecated code (fallback to exclusion text etc)
BUG: some, but not all, hosts that are positive for malware are still being extracted
FEAT: API drop down with manager
BUG: extract not traversing and downloading all shares/dirs/files
BUG: when SLB window open with subtask (i.e. explorer), run scan, DB view needs a way to refresh
BUG: RCE scan unselected in start new scan, but persists in probe options if previously ticked - should be ingerited from new scan options
FEAT: "rescan" option from server list browser
DOCS: VM and VPN advice
BUG: remove "enable rce analysis" tick box from start new scan -> configure bulk probe
BUG: server list browser -> filters -> templates dropdown needs "choose template..." entry
FEAT: search  details (ie for patterns in probe reslts) and notes
FEAT: keybinding to close window/diaogs
FEAT: background tasks. button on dash to display
FEAT: save notes in server details automatically on close
FEAT: remove "notes saved" dialog
BUG: some compromised hosts not being flagged (marked as avoid in live db)
FEAT: display notes on hover if present
PICS: template manager
PICS: probe dialog
PICS: detailed view
PICS: pry interface
DOCS: explain column symbols in server list browser
