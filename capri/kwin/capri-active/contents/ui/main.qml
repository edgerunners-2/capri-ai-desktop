/* Capri's eyes on KDE Wayland: KWin knows the active window, X11 can't see native Wayland windows.
   This script copies the active window's title, app id, frame box and a heartbeat stamp into the
   [CapriActive] group of kwinrc every time the focus changes (QtCore's Settings writes kwinrc by
   itself); capri/linux.py reads kwinrc. The stamp also lets Capri tell the watcher is loaded:
   KWin runs this file on every KWin start, so the stamp is fresh whenever the watcher is there. */
import QtQuick
import org.kde.kwin
import QtCore

QtObject {
    id: capri

    property Settings eyes: Settings {
        objectName: "CapriActive"
        property string title: ""
        property string app: ""
        property string box: "0 0 0 0"
        property int stamp: 0
    }

    function grab() {
        const w = Workspace.activeWindow;
        if (!w || !w.normalWindow) {
            capri.eyes.title = "";
            capri.eyes.app = "";
            capri.eyes.box = "0 0 0 0";
        } else {
            capri.eyes.title = w.caption;
            capri.eyes.app = w.desktopFileName || w.resourceClass || "";
            const g = w.frameGeometry;  // global scene pixels, matching grim / Spectacle coordinates
            capri.eyes.box = Math.round(g.x) + " " + Math.round(g.y) + " " + Math.round(g.width) + " "
                             + Math.round(g.height);
        }
        capri.eyes.stamp = Math.floor(Date.now() / 1000);
    }

    property Connections focus: Connections {
        target: Workspace
        function onWindowActivated() { capri.grab(); }
        function onWindowAdded() { capri.grab(); }
        function onWindowRemoved() { capri.grab(); }
    }

    // titles change inside the active window too (browser tabs): follow the window that has focus
    property Connections caption: Connections {
        target: Workspace.activeWindow
        function onCaptionChanged() { capri.grab(); }
        function onActiveChanged() { capri.grab(); }
        function onMinimizedChanged() { capri.grab(); }
        function onFrameGeometryChanged() { capri.grab(); }
    }

    Component.onCompleted: capri.grab();
}
