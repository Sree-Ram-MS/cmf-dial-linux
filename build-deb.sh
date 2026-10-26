#!/bin/sh
# Build the .deb: ./build-deb.sh  ->  dist/cmf-dial_<version>_all.deb
set -e
cd "$(dirname "$0")"
VERSION=$(python3 -c "import re;print(re.search(r'\"(.+)\"', open('cmfdial/__init__.py').read()).group(1))")
B=build/pkg
rm -rf build && mkdir -p $B/DEBIAN $B/usr/lib/cmf-dial/cmfdial $B/usr/bin $B/usr/share/applications \
  $B/usr/lib/systemd/user $B/usr/lib/udev/rules.d $B/usr/lib/modules-load.d dist
cp cmfdial/*.py $B/usr/lib/cmf-dial/cmfdial/
install -m755 bin/cmf-dial bin/cmf-dial-settings $B/usr/bin/
install -m644 packaging/files/io.github.sreerammsofficial.CmfDial.desktop $B/usr/share/applications/
install -m644 packaging/files/cmf-dial.service $B/usr/lib/systemd/user/
install -m644 packaging/files/70-cmf-dial.rules $B/usr/lib/udev/rules.d/
install -m644 packaging/files/cmf-dial-modules.conf $B/usr/lib/modules-load.d/cmf-dial.conf
sed "s/@VERSION@/$VERSION/" packaging/debian/control.in > $B/DEBIAN/control
install -m755 packaging/debian/postinst packaging/debian/prerm $B/DEBIAN/
dpkg-deb --root-owner-group --build $B dist/cmf-dial_${VERSION}_all.deb
