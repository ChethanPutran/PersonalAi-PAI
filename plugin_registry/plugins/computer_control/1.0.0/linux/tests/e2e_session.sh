#!/usr/bin/env bash
# Runs INSIDE `dbus-run-session -- xvfb-run`: a private desktop (own X server, own session bus, own
# accessibility bus) with one accessible GTK3 app, then the e2e driver against the real plugin library.
# Nothing here touches the real desktop. Only the background jobs started here are cleaned up.
#   usage: e2e_session.sh <python> <driver.py> <gtk_app.py>
PY="$1"; DRIVER="$2"; APP="$3"
trap 'kill $(jobs -p) 2>/dev/null' EXIT
TMPH=$(mktemp -d); RT=$(mktemp -d); chmod 700 "$RT"
CLEAN="env -i HOME=$TMPH XDG_RUNTIME_DIR=$RT DISPLAY=$DISPLAY XAUTHORITY=$XAUTHORITY DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS PATH=/usr/bin:/bin:/usr/libexec LANG=C.UTF-8 GDK_BACKEND=x11 XDG_SESSION_TYPE=x11 GTK_A11Y=atspi GTK_MODULES=gail:atk-bridge NO_AT_BRIDGE=0 PAI_CC_SO=$PAI_CC_SO PAI_REGISTRY_ROOT=$PAI_REGISTRY_ROOT"
$CLEAN /usr/libexec/at-spi-bus-launcher --launch-immediately >/dev/null 2>&1 &
sleep 1.5
$CLEAN /usr/libexec/at-spi2-registryd >/dev/null 2>&1 &
sleep 1
$CLEAN busctl --user set-property org.a11y.Bus /org/a11y/bus org.a11y.Status IsEnabled b true || exit 3
$CLEAN /usr/bin/python3 "$APP" >/dev/null 2>&1 &
sleep 4
$CLEAN PYTHONPATH= "$PY" "$DRIVER" 2>&1
