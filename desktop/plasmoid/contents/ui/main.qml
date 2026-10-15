import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as Plasma5Support
import org.kde.plasma.plasmoid

PlasmoidItem {
    id: root

    property string receiverState: "absent"
    property string deviceLabel: "CachyDrop-C5-Reciever"
    property int remaining: 0
    property string detail: "Connect the registered " + deviceLabel
    property string pendingCommand: ""
    property double pendingDeadline: 0
    property bool pendingSawTransition: false
    readonly property bool active: receiverState === "starting" || receiverState === "receiving" || receiverState === "stopping"
    readonly property bool transitioning: receiverState === "starting" || receiverState === "stopping"
    readonly property string countdown: {
        const mins = Math.floor(remaining / 60)
        const secs = remaining % 60
        return mins + ":" + (secs < 10 ? "0" : "") + secs
    }
    readonly property string panelText: receiverState === "receiving" ? "AirDrop " + countdown
        : receiverState === "starting" || receiverState === "stopping" ? "AirDrop …" : "AirDrop"
    readonly property color stateColor: receiverState === "error" ? Kirigami.Theme.negativeTextColor
        : receiverState === "receiving" ? Kirigami.Theme.positiveTextColor
        : receiverState === "starting" || receiverState === "stopping" ? Kirigami.Theme.neutralTextColor
        : receiverState === "absent" ? Kirigami.Theme.disabledTextColor : Kirigami.Theme.textColor

    Plasmoid.title: "AirDrop"
    Plasmoid.icon: "network-wireless-hotspot"
    // Keep the applet installed but remove it from the visible panel whenever
    // the physical AirDrop dongle is absent. Hotplug status polling makes it
    // reappear without adding/removing the applet configuration.
    Plasmoid.status: receiverState === "absent" ? PlasmaCore.Types.HiddenStatus : PlasmaCore.Types.ActiveStatus
    toolTipMainText: deviceLabel + " · " + panelText
    toolTipSubText: receiverState === "ready"
        ? "Click to receive for 10 minutes. On iPhone, open Share → AirDrop and choose ESP32C5 AirDrop."
        : detail

    function updateStatus(raw) {
        try {
            const value = JSON.parse(raw)
            deviceLabel = value.device || "CachyDrop-C5-Reciever"
            const incomingState = value.state || "error"
            if (pendingCommand !== "") {
                if (incomingState === "starting" || incomingState === "stopping") pendingSawTransition = true
                const completed = pendingCommand === "start"
                    ? incomingState === "receiving" || (incomingState === "error" && pendingSawTransition) || incomingState === "absent"
                    : incomingState === "ready" || incomingState === "error" || incomingState === "absent"
                if (!completed && Date.now() < pendingDeadline) return
                pendingCommand = ""
                pendingDeadline = 0
            }
            receiverState = incomingState
            remaining = Number(value.remaining || 0)
            detail = value.detail || ""
        } catch (e) {
            receiverState = "error"
            detail = "Could not read " + deviceLabel + " status"
        }
    }

    function toggle() {
        if (receiverState === "absent" || transitioning) return
        if (receiverState === "receiving") {
            pendingCommand = "stop"
            receiverState = "stopping"
            remaining = 0
            detail = "Stopping the AirDrop receiver"
        } else {
            pendingCommand = "start"
            receiverState = "starting"
            remaining = 0
            detail = "Starting the AirDrop receiver"
        }
        pendingSawTransition = false
        pendingDeadline = Date.now() + 20000
        actionSource.connectSource("/usr/local/bin/esp32c5-airdrop-control " + pendingCommand)
    }

    Plasma5Support.DataSource {
        id: statusSource
        engine: "executable"
        connectedSources: ["/usr/local/bin/esp32c5-airdrop-control status"]
        interval: 1000
        onNewData: function(sourceName, data) {
            if (data["stdout"] !== undefined) root.updateStatus(data["stdout"])
        }
    }

    Plasma5Support.DataSource {
        id: actionSource
        engine: "executable"
        onNewData: function(sourceName, data) {
            disconnectSource(sourceName)
            if (data["exit code"] !== undefined && Number(data["exit code"]) !== 0) {
                root.pendingCommand = ""
                root.pendingDeadline = 0
                root.receiverState = "error"
                root.detail = "Could not control the C5 receiver service"
            }
            statusSource.disconnectSource("/usr/local/bin/esp32c5-airdrop-control status")
            statusSource.connectSource("/usr/local/bin/esp32c5-airdrop-control status")
        }
    }

    compactRepresentation: MouseArea {
        visible: root.receiverState !== "absent"
        Layout.minimumWidth: visible ? row.implicitWidth + Kirigami.Units.smallSpacing * 2 : 0
        Layout.maximumWidth: Layout.minimumWidth
        enabled: root.receiverState !== "absent" && !root.transitioning
        hoverEnabled: true
        onClicked: root.toggle()

        RowLayout {
            id: row
            anchors.centerIn: parent
            spacing: Kirigami.Units.smallSpacing
            opacity: parent.pressed ? 0.55 : 1.0
            Behavior on opacity { NumberAnimation { duration: 80 } }
            Kirigami.Icon {
                visible: !root.transitioning
                source: "network-wireless-hotspot"
                implicitWidth: Kirigami.Units.iconSizes.smallMedium
                implicitHeight: implicitWidth
                color: root.stateColor
            }
            Controls.BusyIndicator {
                visible: root.transitioning
                running: visible
                implicitWidth: Kirigami.Units.iconSizes.smallMedium
                implicitHeight: implicitWidth
            }
            Controls.Label {
                text: root.panelText
                color: root.stateColor
                font.bold: root.receiverState === "receiving"
            }
        }
    }

    fullRepresentation: Item {
        implicitWidth: Kirigami.Units.gridUnit * 18
        implicitHeight: Kirigami.Units.gridUnit * 8
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: Kirigami.Units.largeSpacing
            Controls.Label { text: root.panelText; font.pointSize: Kirigami.Theme.defaultFont.pointSize + 2; font.bold: true }
            Controls.Label { text: root.detail; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            Controls.Button {
                text: root.active ? "Stop receiving" : "Receive for 10 minutes"
                enabled: root.receiverState !== "absent" && !root.transitioning
                onClicked: { root.toggle(); root.expanded = false }
            }
        }
    }
}
