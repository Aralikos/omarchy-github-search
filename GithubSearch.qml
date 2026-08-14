import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import QtQuick
import qs.Commons
import qs.Ui

// Thin view over github-search-helper.py: the Python coprocess owns fetching,
// caching, filtering, ranking, cloning, and config. This file only renders
// rows and forwards key presses as JSON-lines commands.
Item {
  id: root

  property string omarchyPath: Quickshell.env("OMARCHY_PATH")
  property string homePath: Quickshell.env("HOME")
  property var shell: null
  property var manifest: null

  property bool opened: false
  property string filterText: ""
  property int selectedIndex: 0
  property bool cursorActive: false
  property bool refreshing: false
  property bool everLoaded: false
  property string cloneRoot: homePath + "/Development/github"

  readonly property string pluginDir: (manifest && manifest.__sourceDir)
    ? manifest.__sourceDir
    : homePath + "/.config/omarchy/plugins/emiifont.github-search"

  // Shares the [menu] surface tokens — themes that style the menu also
  // style this overlay.
  property color background: Color.menu.background
  property color foreground: Color.menu.text
  property color border: Color.menu.border
  property var borderSpec: Border.surfaceSpec("menu", "border", border, Math.max(1, Style.space(2)))
  property color scrim: Color.menu.scrim
  property color selectedBackground: Color.menu.selectedBackground
  property color selectedText: Color.menu.selectedText
  readonly property int cornerRadius: Style.cornerRadius
  property string fontFamily: Style.font.menuFamily
  property int contentMargin: Style.spacing.panelPadding
  property int headerHeight: Math.max(Style.space(34), Style.font.title + Style.spacing.controlPaddingY * 2)
  property int footerHeight: Style.font.bodySmall + Style.spacing.controlPaddingY * 2
  property int contentSpacing: Style.spacing.md
  property int cardWidth: Math.min(Style.space(520), panel.width - Style.gapsOut * 2)
  property int cardHeight: Math.min(Style.space(500), panel.height - Style.gapsOut * 2)
  property int rowHeight: Style.font.title + Style.font.bodySmall + Style.spacing.labelGap + Style.spacing.controlPaddingY * 2

  function open(payloadJson) {
    root.opened = true
    root.filterText = ""
    root.selectedIndex = 0
    root.cursorActive = true
    if (helperProc.running) {
      root.send({ cmd: "filter", query: "" })
      root.send({ cmd: "refresh" })
    } else {
      // A fresh helper emits config, cached rows, and a refresh on its own.
      helperProc.running = true
    }
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function close() {
    root.opened = false
  }

  function dismiss() {
    root.opened = false
    if (root.shell && typeof root.shell.hide === "function")
      root.shell.hide((root.manifest && root.manifest.id) || "emiifont.github-search")
  }

  function toggle() {
    if (root.opened) root.dismiss()
    else root.open("{}")
  }

  function send(payload) {
    if (helperProc.running) helperProc.write(JSON.stringify(payload) + "\n")
  }

  function handleEvent(msg) {
    if (msg.event === "rows") {
      if (msg.query !== root.filterText) return // stale response
      displayModel.clear()
      for (var i = 0; i < msg.rows.length; i++) displayModel.append(msg.rows[i])
      root.everLoaded = root.everLoaded || msg.rows.length > 0
      if (displayModel.count === 0) root.selectedIndex = 0
      else if (root.selectedIndex >= displayModel.count) root.selectedIndex = displayModel.count - 1
      root.cursorActive = displayModel.count > 0
      Qt.callLater(function() {
        if (displayModel.count > 0) resultList.positionViewAtIndex(root.selectedIndex, ListView.Contain)
      })
    } else if (msg.event === "status") {
      root.refreshing = msg.refreshing === true
      root.everLoaded = root.everLoaded || msg.count > 0
    } else if (msg.event === "config") {
      root.cloneRoot = msg.cloneRoot || root.cloneRoot
    }
  }

  function select(delta) {
    if (displayModel.count === 0) return
    if (!cursorActive) {
      cursorActive = true
      selectedIndex = delta < 0 ? displayModel.count - 1 : 0
    } else {
      selectedIndex = (selectedIndex + delta + displayModel.count) % displayModel.count
    }
    resultList.positionViewAtIndex(selectedIndex, ListView.Contain)
  }

  function selectPage(delta) {
    if (displayModel.count === 0) return
    var visibleRows = Math.max(1, Math.floor(resultList.height / root.rowHeight))
    var newIndex = cursorActive ? selectedIndex + delta * visibleRows : (delta < 0 ? displayModel.count - 1 : 0)
    cursorActive = true
    selectedIndex = Math.max(0, Math.min(displayModel.count - 1, newIndex))
    resultList.positionViewAtIndex(selectedIndex, ListView.Contain)
  }

  function setFilter(nextFilter) {
    root.filterText = nextFilter
    root.selectedIndex = 0
    root.cursorActive = true
    root.send({ cmd: "filter", query: nextFilter })
  }

  function activateIndex(index, cloneRequested) {
    if (index < 0 || index >= displayModel.count) return
    var row = displayModel.get(index)
    if (cloneRequested) root.send({ cmd: "clone", name: row.name })
    else root.send({ cmd: "open", url: row.url })
    root.dismiss()
  }

  ListModel { id: displayModel }

  Process {
    id: helperProc
    command: ["setpriv", "--pdeathsig", "TERM", "python3", root.pluginDir + "/github-search-helper.py"]
    stdinEnabled: true
    stdout: SplitParser {
      onRead: function(data) {
        try {
          root.handleEvent(JSON.parse(data))
        } catch (e) { }
      }
    }
  }

  PanelWindow {
    id: panel
    visible: root.opened
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    WlrLayershell.namespace: "omarchy-github-search"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
    exclusionMode: ExclusionMode.Ignore

    Rectangle {
      anchors.fill: parent
      color: root.scrim
    }

    MouseArea {
      anchors.fill: parent
      onClicked: root.dismiss()
    }

    BorderSurface {
      id: card
      width: root.cardWidth
      height: root.cardHeight
      radius: root.cornerRadius
      anchors.centerIn: parent
      color: root.background
      borderSpec: root.borderSpec
      padding: root.contentMargin

      MouseArea { anchors.fill: parent; onClicked: {} }

      Item {
        id: keyCatcher
        anchors.fill: parent
        focus: true

        Keys.priority: Keys.BeforeItem
        Keys.onPressed: function(event) {
          if (event.key === Qt.Key_Escape) {
            if (root.filterText) root.setFilter("")
            else root.dismiss()
            event.accepted = true
          } else if (Util.editsFilter(event, root.filterText)) {
            root.setFilter(Util.editedFilter(event, root.filterText))
            event.accepted = true
          } else if (event.key === Qt.Key_Up) {
            root.select(-1)
            event.accepted = true
          } else if (event.key === Qt.Key_Down) {
            root.select(1)
            event.accepted = true
          } else if (event.key === Qt.Key_PageUp) {
            root.selectPage(-1)
            event.accepted = true
          } else if (event.key === Qt.Key_PageDown) {
            root.selectPage(1)
            event.accepted = true
          } else if (event.key === Qt.Key_F5) {
            root.send({ cmd: "refresh" })
            event.accepted = true
          } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
            if (root.cursorActive) root.activateIndex(root.selectedIndex, (event.modifiers & Qt.ControlModifier) !== 0)
            else if (displayModel.count > 0) root.cursorActive = true
            event.accepted = true
          } else if (event.text && event.text.length === 1 && event.text.charCodeAt(0) >= 32 && event.text.charCodeAt(0) !== 127) {
            root.setFilter(root.filterText + event.text)
            event.accepted = true
          }
        }
      }

      Column {
        anchors.fill: parent
        anchors.topMargin: card.contentTopInset
        anchors.rightMargin: card.contentRightInset
        anchors.bottomMargin: card.contentBottomInset
        anchors.leftMargin: card.contentLeftInset
        spacing: root.contentSpacing

        Rectangle {
          width: parent.width
          height: root.headerHeight
          radius: root.cornerRadius
          color: "transparent"

          Text {
            anchors.left: parent.left
            anchors.right: refreshBadge.left
            anchors.rightMargin: Style.spacing.md
            anchors.verticalCenter: parent.verticalCenter
            text: root.filterText || "Search GitHub repos…"
            color: root.foreground
            opacity: root.filterText ? 1 : 0.58
            font.family: root.fontFamily
            font.pixelSize: Style.font.heading
            elide: Text.ElideRight
          }

          Text {
            id: refreshBadge
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            text: root.refreshing ? "󰑓" : ""
            color: root.foreground
            opacity: 0.5
            font.family: root.fontFamily
            font.pixelSize: Style.font.heading
          }
        }

        Item {
          width: parent.width
          height: parent.height - root.headerHeight - root.footerHeight - root.contentSpacing * 2

          ListView {
            id: resultList
            anchors.fill: parent
            model: displayModel
            clip: true
            boundsBehavior: Flickable.StopAtBounds

            delegate: Rectangle {
              required property int index
              required property string name
              required property string desc
              required property string url
              required property bool priv

              readonly property bool hasCursor: root.cursorActive && index === root.selectedIndex

              width: resultList.width
              height: root.rowHeight
              radius: root.cornerRadius
              color: hasCursor ? root.selectedBackground : "transparent"

              Column {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.leftMargin: Style.spacing.rowPaddingX
                anchors.rightMargin: Style.spacing.rowPaddingX
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.spacing.labelGap

                Text {
                  width: parent.width
                  text: (priv ? "\uf023 " : "\uf09b ") + name
                  color: hasCursor ? root.selectedText : root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.title
                  elide: Text.ElideRight
                }

                Text {
                  width: parent.width
                  visible: desc !== ""
                  text: desc
                  color: hasCursor ? root.selectedText : root.foreground
                  opacity: 0.6
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  elide: Text.ElideRight
                }
              }

              MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onContainsMouseChanged: if (containsMouse) {
                  root.cursorActive = true
                  root.selectedIndex = index
                }
                onClicked: function(mouse) {
                  root.cursorActive = true
                  root.selectedIndex = index
                  root.activateIndex(index, (mouse.modifiers & Qt.ControlModifier) !== 0)
                }
              }
            }
          }

          Column {
            anchors.centerIn: parent
            spacing: Style.space(8)
            visible: displayModel.count === 0

            Text {
              text: root.refreshing && !root.everLoaded ? "󰑓" : "\uf09b"
              color: root.selectedText
              opacity: 0.8
              font.family: root.fontFamily
              font.pixelSize: Style.font.displayLarge
              horizontalAlignment: Text.AlignHCenter
              width: parent.width
            }

            Text {
              text: root.refreshing && !root.everLoaded
                ? "Loading repositories…"
                : (!root.everLoaded
                  ? "No repositories loaded — is `gh` authenticated?"
                  : "No matches for “" + root.filterText + "”")
              color: root.foreground
              opacity: 0.7
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              horizontalAlignment: Text.AlignHCenter
              width: parent.width
            }
          }
        }

        Text {
          width: parent.width
          height: root.footerHeight
          text: "↵ open in browser · ⌃↵ clone to " + root.cloneRoot.replace(root.homePath, "~") + " · F5 refresh"
          color: root.foreground
          opacity: 0.45
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          horizontalAlignment: Text.AlignHCenter
          verticalAlignment: Text.AlignVCenter
          elide: Text.ElideRight
        }
      }
    }
  }
}
