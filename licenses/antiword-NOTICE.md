# Antiword notice

Dirracuda's optional Analyst feature can invoke the system-installed Antiword program to
extract text from legacy Microsoft Word binary documents inside its parser sandbox.
Dirracuda does not copy or redistribute the Antiword binary or its mapping files.

- Program: Antiword
- Required upstream version: 0.37
- Required Debian package revision: 0.37-17
- Licence: GNU General Public License, version 2 or (at your option) any later version
- Debian source package: https://sources.debian.org/src/antiword/0.37-17/
- Manual and licence notice:
  https://manpages.debian.org/testing/antiword/antiword.1.en.html

Install the dependency from the operating-system package manager:

```bash
sudo apt install antiword
dpkg-query -W -f='${db:Status-Status} ${Version}\n' antiword
```

Analyst accepts only an installed result of `installed 0.37-17`. Other revisions fail
preflight rather than running an older or unverifiable parser. A future portability card
must define a controlled source-build and source-offer process before Dirracuda may
redistribute Antiword itself.
